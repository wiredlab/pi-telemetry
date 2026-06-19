#!/usr/bin/env python3
"""Collect Raspberry Pi and single-board-computer telemetry."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable


LOG = logging.getLogger("pi-telemetry")
TELEMETRY_TOPIC_SUFFIX = "telemetry"

AUTO_SERVICE_NAMES = {
    "dump1090-fa",
    "graphs1090",
    "piaware",
    "readsb",
    "rtl_433",
    "tar1090",
    "tailscaled",
}

COMMON_SYSTEMD_SERVICE_NAMES = {
    "avahi-daemon",
    "bluetooth",
    "cron",
    "dbus",
    "dhcpcd",
    "getty@tty1",
    "NetworkManager",
    "polkit",
    "rsyslog",
    "ssh",
    "systemd-journald",
    "systemd-logind",
    "systemd-resolved",
    "systemd-timesyncd",
    "systemd-udevd",
    "udisks2",
    "wpa_supplicant",
}

DISK_FS_TYPES = {
    "btrfs",
    "ext2",
    "ext3",
    "ext4",
    "f2fs",
    "vfat",
    "xfs",
}


@dataclasses.dataclass(frozen=True)
class Config:
    telemetry_id: str = dataclasses.field(default_factory=socket.gethostname)
    topic_prefix: str = "pi-telemetry"
    interval: float = 10.0
    mqtt_host: str | None = None
    mqtt_port: int = 8883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    extra_services: tuple[str, ...] = ()
    docker_containers: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Paths:
    proc: Path = Path("/proc")
    sys: Path = Path("/sys")


@dataclasses.dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int


CommandRunner = Callable[[list[str]], str | None]
DiskUsageFn = Callable[[Path], DiskUsage | None]


def parse_csv_names(value: str, *, strip_service_suffix: bool = False) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        name = item.strip()
        if strip_service_suffix:
            name = name.removesuffix(".service")
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return tuple(names)


def load_config(environ: dict[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    extra_services = parse_csv_names(
        env.get("PI_TELEMETRY_SERVICES", ""),
        strip_service_suffix=True,
    )
    docker_containers = parse_csv_names(
        env.get("PI_TELEMETRY_DOCKER_CONTAINERS", "")
    )
    return Config(
        telemetry_id=env.get("PI_TELEMETRY_ID") or socket.gethostname(),
        topic_prefix=env.get("MQTT_TOPIC_PREFIX", "pi-telemetry").strip("/"),
        interval=float(env.get("PI_TELEMETRY_SLEEP_TIME", "30")),
        mqtt_host=env.get("MQTT_HOST"),
        mqtt_port=int(env.get("MQTT_PORT", "8883")),
        mqtt_username=env.get("MQTT_USER"),
        mqtt_password=env.get("MQTT_PASS"),
        extra_services=extra_services,
        docker_containers=docker_containers,
    )


def run_command(args: list[str]) -> str | None:
    if shutil.which(args[0]) is None:
        return None
    try:
        completed = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def current_timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def topic(config: Config, suffix: str) -> str:
    return f"{config.topic_prefix}/{config.telemetry_id}/{suffix}".strip("/")


def status_payload(state: str) -> dict[str, str]:
    return {"state": state}


def collect_payload(
    config: Config,
    *,
    paths: Paths = Paths(),
    command_runner: CommandRunner = run_command,
    disk_usage: DiskUsageFn | None = None,
) -> dict:
    payload: dict = {
        "timestamp": current_timestamp(),
        "host": config.telemetry_id,
    }
    add_if_present(payload, "model", collect_model(paths))
    add_if_present(payload, "uptime", collect_uptime(paths))
    add_if_present(payload, "load", collect_load(paths))
    add_if_present(payload, "memory", collect_memory(paths))
    add_if_present(payload, "temps", collect_temperatures(paths, command_runner))
    add_if_present(payload, "fan", collect_fan(paths))
    add_if_present(
        payload,
        "disk",
        collect_disks(paths, disk_usage=disk_usage or real_disk_usage),
    )
    add_if_present(payload, "network", collect_network(paths, command_runner))
    add_if_present(
        payload,
        "services",
        detect_services(
            command_runner=command_runner,
            extra_systemd=config.extra_services,
            docker_containers=config.docker_containers,
        ),
    )
    return payload


def add_if_present(payload: dict, key: str, value):
    if value not in (None, {}, []):
        payload[key] = value


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip("\x00\n ")
    except OSError:
        return None


def collect_model(paths: Paths) -> str | None:
    return read_text(paths.proc / "device-tree" / "model")


def collect_uptime(paths: Paths) -> float | None:
    text = read_text(paths.proc / "uptime")
    if not text:
        return None
    try:
        return round(float(text.split()[0]), 2)
    except (ValueError, IndexError):
        return None


def collect_load(paths: Paths) -> list[float] | None:
    text = read_text(paths.proc / "loadavg")
    if not text:
        return None
    try:
        return [round(float(value), 2) for value in text.split()[:3]]
    except ValueError:
        return None


def collect_memory(paths: Paths) -> dict | None:
    text = read_text(paths.proc / "meminfo")
    if not text:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if fields:
            try:
                values[name] = int(fields[0]) * 1024
            except ValueError:
                continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    memory = {
        "total": total,
        "available": available,
        "percent": round((total - available) * 100 / total, 1),
    }
    swap_total = values.get("SwapTotal", 0)
    if swap_total:
        swap_free = values.get("SwapFree", 0)
        memory["swap"] = {
            "total": swap_total,
            "free": swap_free,
            "percent": round((swap_total - swap_free) * 100 / swap_total, 1),
        }
    return memory


def collect_temperatures(paths: Paths, command_runner: CommandRunner) -> dict | None:
    temps: dict[str, float] = {}
    for zone in sorted((paths.sys / "class" / "thermal").glob("thermal_zone*")):
        raw_temp = read_text(zone / "temp")
        if raw_temp is None:
            continue
        try:
            value = float(raw_temp)
        except ValueError:
            continue
        name = read_text(zone / "type") or zone.name
        if abs(value) > 200:
            value = value / 1000
        temps[name] = round(value, 1)

    vcgencmd = command_runner(["vcgencmd", "measure_temp"])
    if vcgencmd and "temp=" in vcgencmd:
        try:
            value = vcgencmd.split("temp=", 1)[1].split("'", 1)[0]
            temps.setdefault("cpu", round(float(value), 1))
        except (ValueError, IndexError):
            pass
    return temps or None


def collect_fan(paths: Paths) -> dict | None:
    fan: dict[str, int] = {}
    for device in sorted((paths.sys / "class" / "thermal").glob("cooling_device*")):
        state = read_int(device / "cur_state")
        if state is None:
            continue
        name = read_text(device / "type") or device.name
        fan[name] = state
    for hwmon in sorted((paths.sys / "class" / "hwmon").glob("hwmon*")):
        for input_path in sorted(hwmon.glob("fan*_input")):
            rpm = read_int(input_path)
            if rpm is not None:
                fan[input_path.stem] = rpm
    return fan or None


def read_int(path: Path) -> int | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def real_disk_usage(path: Path) -> DiskUsage | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def collect_disks(paths: Paths, disk_usage: DiskUsageFn = real_disk_usage) -> dict | None:
    mountinfo = read_text(paths.proc / "self" / "mountinfo")
    if not mountinfo:
        return None
    disks: dict[str, dict] = {}
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        fs_fields = after.split()
        if len(fields) < 5 or len(fs_fields) < 2:
            continue
        mount_point = fields[4].replace("\\040", " ")
        fs_type = fs_fields[0]
        if fs_type not in DISK_FS_TYPES:
            continue
        usage = disk_usage(Path(mount_point))
        if usage is None or usage.total <= 0:
            continue
        disks[mount_point] = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.used * 100 / usage.total, 1),
        }
    return dict(sorted(disks.items())) or None


def collect_network(paths: Paths, command_runner: CommandRunner = run_command) -> dict | None:
    output = command_runner(["ip", "-j", "addr"])
    if not output:
        return None
    try:
        interfaces = json.loads(output)
    except json.JSONDecodeError:
        return None
    gateways = default_gateways(paths)
    wireless = wireless_stats(paths)
    network: dict[str, dict] = {}
    for interface in interfaces:
        name = interface.get("ifname")
        if not name or not is_relevant_interface(name):
            continue
        addresses = [
            addr.get("local")
            for addr in interface.get("addr_info", [])
            if addr.get("family") == "inet" and addr.get("local")
        ]
        if not addresses:
            continue
        data = {
            "ip": addresses[0],
            "state": interface.get("operstate", "UNKNOWN"),
        }
        if name in gateways:
            data["gateway"] = gateways[name]
        if is_wireless(paths, name):
            essid = command_runner(["iwgetid", name, "--raw"])
            if essid and essid.strip():
                data["essid"] = essid.strip()
            if name in wireless:
                data.update(wireless[name])
        network[name] = data
    return network or None


def is_relevant_interface(name: str) -> bool:
    ignored_prefixes = ("br-", "docker", "lxc", "veth", "virbr")
    if name == "lo" or name.startswith(ignored_prefixes):
        return False
    return True


def is_wireless(paths: Paths, interface: str) -> bool:
    return (paths.sys / "class" / "net" / interface / "wireless").exists()


def default_gateways(paths: Paths) -> dict[str, str]:
    text = read_text(paths.proc / "net" / "route")
    if not text:
        return {}
    gateways: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            raw = int(fields[2], 16).to_bytes(4, byteorder="little")
        except ValueError:
            continue
        gateways[fields[0]] = ".".join(str(part) for part in raw)
    return gateways


def wireless_stats(paths: Paths) -> dict[str, dict]:
    text = read_text(paths.proc / "net" / "wireless")
    if not text:
        return {}
    stats: dict[str, dict] = {}
    for line in text.splitlines()[2:]:
        name, separator, values = line.partition(":")
        if not separator:
            continue
        fields = values.split()
        if len(fields) < 3:
            continue
        quality = fields[1].rstrip(".")
        signal = fields[2].rstrip(".")
        try:
            stats[name.strip()] = {
                "quality": f"{int(float(quality))}/70",
                "signal_dbm": int(float(signal)),
            }
        except ValueError:
            continue
    return stats


def detect_systemd_services(
    *,
    command_runner: CommandRunner = run_command,
    extra: Iterable[str] = (),
) -> dict[str, str] | None:
    output = running_systemd_unit_output(command_runner)
    if not output:
        return None
    wanted = AUTO_SERVICE_NAMES | {service.removesuffix(".service") for service in extra}
    services: dict[str, str] = {}
    for name in parse_systemd_unit_names(output):
        if name in wanted:
            services[name] = "active"
    return dict(sorted(services.items())) or None


def detect_docker_services(
    *,
    command_runner: CommandRunner = run_command,
    containers: Iterable[str] = (),
) -> dict[str, str] | None:
    wanted = {container for container in containers if container}
    if not wanted:
        return None
    output = command_runner(["docker", "ps", "--all", "--format", "{{.Names}}\t{{.State}}"])
    if not output:
        return None
    services: dict[str, str] = {}
    for line in output.splitlines():
        name, separator, state = line.partition("\t")
        if not separator:
            continue
        name = name.strip()
        state = state.strip().lower()
        if name not in wanted:
            continue
        services[name] = "active" if state == "running" else state
    return dict(sorted(services.items())) or None


def running_systemd_unit_output(command_runner: CommandRunner = run_command) -> str | None:
    return command_runner(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--no-pager",
        ]
    )


def parse_systemd_unit_names(output: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        unit = line.split(maxsplit=1)[0] if line.strip() else ""
        if not unit.endswith(".service"):
            continue
        name = unit.removesuffix(".service")
        if name not in seen:
            names.append(name)
            seen.add(name)
    return tuple(names)


def discover_systemd_service_candidates(
    *,
    command_runner: CommandRunner = run_command,
) -> tuple[str, ...]:
    output = running_systemd_unit_output(command_runner)
    if not output:
        return ()
    candidates = [
        name
        for name in parse_systemd_unit_names(output)
        if name in AUTO_SERVICE_NAMES and name not in COMMON_SYSTEMD_SERVICE_NAMES
    ]
    return tuple(sorted(candidates))


def discover_docker_container_candidates(
    *,
    command_runner: CommandRunner = run_command,
) -> tuple[str, ...]:
    output = command_runner(["docker", "ps", "--all", "--format", "{{.Names}}\t{{.State}}"])
    if not output:
        return ()
    names: set[str] = set()
    for line in output.splitlines():
        name, _separator, _state = line.partition("\t")
        name = name.strip()
        if name:
            names.add(name)
    return tuple(sorted(names))


def discover_service_candidates(
    *,
    command_runner: CommandRunner = run_command,
) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    systemd = discover_systemd_service_candidates(command_runner=command_runner)
    docker = discover_docker_container_candidates(command_runner=command_runner)
    if systemd:
        candidates["systemd"] = list(systemd)
    if docker:
        candidates["docker"] = list(docker)
    return candidates


def detect_services(
    *,
    command_runner: CommandRunner = run_command,
    extra_systemd: Iterable[str] = (),
    docker_containers: Iterable[str] = (),
) -> dict[str, dict[str, str]] | None:
    services: dict[str, dict[str, str]] = {}
    systemd = detect_systemd_services(command_runner=command_runner, extra=extra_systemd)
    if systemd:
        services["systemd"] = systemd
    docker = detect_docker_services(
        command_runner=command_runner,
        containers=docker_containers,
    )
    if docker:
        services["docker"] = docker
    return services or None


def publish_loop(config: Config):
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.mqtt_username or config.mqtt_password:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
    client.tls_set()
    client.will_set(
        topic(config, "status"),
        json.dumps(status_payload("lost-connection"), separators=(",", ":")),
        qos=1,
        retain=True,
    )

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            client.publish(
                topic(config, "status"),
                json.dumps(status_payload("connected"), separators=(",", ":")),
                qos=1,
                retain=True,
            )
        else:
            LOG.error("MQTT connect failed: %s", reason_code)

    client.on_connect = on_connect
    client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
    client.loop_start()
    log_detected(config)
    try:
        while True:
            payload = collect_payload(config)
            client.publish(
                topic(config, TELEMETRY_TOPIC_SUFFIX),
                json.dumps(payload, separators=(",", ":")),
                qos=1,
            )
            time.sleep(config.interval)
    finally:
        client.publish(
            topic(config, "status"),
            json.dumps(status_payload("disconnected"), separators=(",", ":")),
            qos=1,
            retain=True,
        ).wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()


def service_log_names(services: dict[str, dict[str, str]] | None) -> list[str]:
    if not services:
        return []
    names: list[str] = []
    for manager, manager_services in sorted(services.items()):
        for name in sorted(manager_services):
            names.append(f"{manager}:{name}")
    return names


def log_detected(config: Config):
    payload = collect_payload(config)
    LOG.info("detected disks: %s", ", ".join(payload.get("disk", {}).keys()) or "none")
    LOG.info("detected interfaces: %s", ", ".join(payload.get("network", {}).keys()) or "none")
    LOG.info(
        "detected services: %s",
        ", ".join(service_log_names(payload.get("services"))) or "none",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="collect one payload and exit")
    parser.add_argument(
        "--discover-services",
        action="store_true",
        help="print candidate systemd and Docker service names for confirmation",
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    if args.discover_services:
        print(
            json.dumps(
                discover_service_candidates(),
                indent=2 if args.pretty else None,
                sort_keys=True,
            )
        )
        return 0
    if args.once:
        print(json.dumps(collect_payload(config), indent=2 if args.pretty else None, sort_keys=True))
        return 0
    missing = [
        name
        for name, value in {
            "MQTT_HOST": config.mqtt_host,
            "MQTT_USER": config.mqtt_username,
            "MQTT_PASS": config.mqtt_password,
        }.items()
        if not value
    ]
    if missing:
        parser.error(f"missing required environment variables: {', '.join(missing)}")
    publish_loop(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
