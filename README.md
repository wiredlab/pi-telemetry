# pi-telemetry
This script and service collects system telemetry for a Raspberry Pi.

It auto-detects relevant temperatures, fans, network interfaces, disks, and
known services. Fields are omitted when they are not relevant to the machine.


## Requirements

* Python 3.11 or newer with `venv`

`sudo apt update && sudo apt install python3-venv`

Raspberry Pi OS Bullseye ships Python 3.9 by default, which is too old for
this service. The install target checks the interpreter version and aborts before
installing when `PYTHON` does not meet the minimum.


## Install

* Clone this repository somewhere convenient.

* Copy `pi-telemetry.env.example` to `pi-telemetry.env` and change the variables as appropriate.  NOTE: systemd does not do variable substitution in this file!

* Edit variable `PREFIX` in `Makefile` if desired.

* `sudo make`

The install target creates a production virtualenv at `/opt/pi-telemetry/venv`
and installs Python package requirements there.


## Configuration

The env file should usually only need MQTT connection settings. Discovery is
automatic by default.

Optional settings:

* `PI_TELEMETRY_ID`: telemetry id, default is hostname.
* `PI_TELEMETRY_SLEEP_TIME`: seconds between publishes, default is 30.
* `PI_TELEMETRY_SERVICES`: comma-separated confirmed systemd service names to
  include in addition to built-in purpose services.
* `PI_TELEMETRY_DOCKER_CONTAINERS`: comma-separated confirmed Docker container
  names to include in service telemetry.

Service telemetry is grouped by manager:

```json
{
  "services": {
    "systemd": {
      "tailscaled": "active"
    },
    "docker": {
      "radiosonde_auto_rx": "active"
    }
  }
}
```

Use `./pi_telemetry.py --discover-services --pretty` on a reachable Pi to print
candidate systemd services and Docker containers. Confirm that the names are
part of that machine's purpose before adding them to `PI_TELEMETRY_SERVICES` or
`PI_TELEMETRY_DOCKER_CONTAINERS`; generic OS services such as SSH, cron, and
dbus should not be added just because they are running.


## Status topics

Telemetry is published to `${MQTT_TOPIC_PREFIX}/${PI_TELEMETRY_ID}/telemetry`.

Service status is published retained to
`${MQTT_TOPIC_PREFIX}/${PI_TELEMETRY_ID}/status`.

The service publishes `{"state":"connected"}` after MQTT connect and registers
a retained last-will message of `{"state":"lost-connection"}`.


## Uninstall

This will stop and disable the systemd service and remove the installed files (including the customized configuration file).

* `sudo make uninstall`
