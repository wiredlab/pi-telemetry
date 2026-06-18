import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pi_telemetry


class TelemetryTests(unittest.TestCase):
    def test_collect_payload_omits_unavailable_optional_fields(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            sys = root / "sys"
            proc.mkdir()
            sys.mkdir()
            (proc / "uptime").write_text("123.45 67.89\n")
            (proc / "loadavg").write_text("0.10 0.20 0.30 1/100 1234\n")
            (proc / "meminfo").write_text(
                "MemTotal:       1000 kB\n"
                "MemAvailable:   750 kB\n"
                "SwapTotal:       500 kB\n"
                "SwapFree:        250 kB\n"
            )

            payload = pi_telemetry.collect_payload(
                pi_telemetry.Config(telemetry_id="test-host", topic_prefix="prefix"),
                paths=pi_telemetry.Paths(proc=proc, sys=sys),
                command_runner=lambda *_args: None,
                disk_usage=lambda _path: None,
            )

        self.assertEqual(payload["host"], "test-host")
        self.assertEqual(payload["uptime"], 123.45)
        self.assertEqual(payload["load"], [0.1, 0.2, 0.3])
        self.assertEqual(payload["memory"]["total"], 1024000)
        self.assertEqual(payload["memory"]["available"], 768000)
        self.assertNotIn("temps", payload)
        self.assertNotIn("fan", payload)
        self.assertNotIn("network", payload)
        self.assertNotIn("disk", payload)
        self.assertNotIn("services", payload)

    def test_discovers_network_interfaces_with_wifi_details(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            sys = root / "sys"
            (proc / "net").mkdir(parents=True)
            (sys / "class" / "net" / "wlan0" / "wireless").mkdir(parents=True)
            (sys / "class" / "net" / "tailscale0").mkdir(parents=True)
            (proc / "net" / "route").write_text(
                "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
                "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
            )
            (proc / "net" / "wireless").write_text(
                "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
                " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
                "wlan0: 0000   68.  -42.  -256        0      0      0      0      0        0\n"
            )

            def run(args):
                if args[:3] == ["ip", "-j", "addr"]:
                    return json.dumps(
                        [
                            {
                                "ifname": "lo",
                                "operstate": "UNKNOWN",
                                "addr_info": [
                                    {"family": "inet", "local": "127.0.0.1"}
                                ],
                            },
                            {
                                "ifname": "wlan0",
                                "operstate": "UP",
                                "addr_info": [
                                    {"family": "inet", "local": "192.168.1.20"}
                                ],
                            },
                            {
                                "ifname": "tailscale0",
                                "operstate": "UNKNOWN",
                                "addr_info": [
                                    {"family": "inet", "local": "100.64.0.2"}
                                ],
                            },
                            {
                                "ifname": "docker0",
                                "operstate": "UP",
                                "addr_info": [
                                    {"family": "inet", "local": "172.17.0.1"}
                                ],
                            },
                        ]
                    )
                if args == ["iwgetid", "wlan0", "--raw"]:
                    return "valpo-media\n"
                return None

            network = pi_telemetry.collect_network(
                pi_telemetry.Paths(proc=proc, sys=sys),
                command_runner=run,
            )

        self.assertEqual(
            network,
            {
                "wlan0": {
                    "ip": "192.168.1.20",
                    "state": "UP",
                    "gateway": "192.168.1.1",
                    "essid": "valpo-media",
                    "quality": "68/70",
                    "signal_dbm": -42,
                },
                "tailscale0": {"ip": "100.64.0.2", "state": "UNKNOWN"},
            },
        )

    def test_discovers_disks_from_mountinfo(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            proc.mkdir()
            (proc / "self").mkdir()
            (proc / "self" / "mountinfo").write_text(
                "24 22 8:1 / / rw,relatime - ext4 /dev/root rw\n"
                "25 22 8:2 / /boot rw,relatime - vfat /dev/mmcblk0p1 rw\n"
                "26 22 0:24 / /run rw,nosuid,nodev - tmpfs tmpfs rw\n"
                "27 22 8:3 / /data rw,relatime - ext4 /dev/sda1 rw\n"
                "28 22 7:1 / /snap/tool/1 ro,nodev,relatime - squashfs /dev/loop1 ro\n"
            )

            seen = []

            def disk_usage(path):
                seen.append(path)
                return pi_telemetry.DiskUsage(total=1000, used=250, free=750)

            disks = pi_telemetry.collect_disks(
                pi_telemetry.Paths(proc=proc, sys=root / "sys"),
                disk_usage=disk_usage,
            )

        self.assertEqual(seen, [Path("/"), Path("/boot"), Path("/data")])
        self.assertEqual(disks["/"]["percent"], 25.0)
        self.assertNotIn("/run", disks)
        self.assertNotIn("/snap/tool/1", disks)

    def test_detects_custom_systemd_services_from_running_units(self):
        def run(args):
            self.assertEqual(
                args,
                [
                    "systemctl",
                    "list-units",
                    "--type=service",
                    "--state=running",
                    "--no-legend",
                    "--no-pager",
                ],
            )
            return (
                "rtl_433.service loaded active running rtl_433 receiver\n"
                "piaware.service loaded active running PiAware ADS-B client\n"
                "ssh.service loaded active running OpenBSD Secure Shell server\n"
            )

        services = pi_telemetry.detect_services(command_runner=run)

        self.assertEqual(
            services,
            {"systemd": {"piaware": "active", "rtl_433": "active"}},
        )

    def test_load_config_parses_systemd_and_docker_service_lists(self):
        config = pi_telemetry.load_config(
            {
                "PI_TELEMETRY_ID": "baird",
                "PI_TELEMETRY_SERVICES": " tailscaled.service, piaware, tailscaled.service ",
                "PI_TELEMETRY_DOCKER_CONTAINERS": " radiosonde_auto_rx, chasemapper, radiosonde_auto_rx ",
            }
        )

        self.assertEqual(config.telemetry_id, "baird")
        self.assertEqual(config.extra_services, ("tailscaled", "piaware"))
        self.assertEqual(config.docker_containers, ("radiosonde_auto_rx", "chasemapper"))

    def test_status_messages_use_configured_topic(self):
        config = pi_telemetry.Config(telemetry_id="hertz", topic_prefix="wiredlab/hertz")

        self.assertEqual(
            pi_telemetry.topic(config, "telemetry"),
            "wiredlab/hertz/hertz/telemetry",
        )
        self.assertEqual(pi_telemetry.TELEMETRY_TOPIC_SUFFIX, "telemetry")
        self.assertEqual(pi_telemetry.status_payload("connected"), {"state": "connected"})
        self.assertEqual(
            pi_telemetry.status_payload("lost-connection"),
            {"state": "lost-connection"},
        )

    def test_makefile_rejects_python_older_than_minimum(self):
        completed = subprocess.run(
            ["make", "check-python", "MIN_PYTHON_VERSION=999.0"],
            cwd=Path(__file__).resolve().parent.parent,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("pi-telemetry requires Python 999.0 or newer", completed.stderr)


if __name__ == "__main__":
    unittest.main()
