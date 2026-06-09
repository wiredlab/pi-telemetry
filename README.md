# pi-telemetry
This script and service collects system telemetry for a Raspberry Pi.

It auto-detects relevant temperatures, fans, network interfaces, disks, and
known services. Fields are omitted when they are not relevant to the machine.


## Requirements

* Python 3 with `venv`

`sudo apt update && sudo apt install python3-venv`



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
* `PI_TELEMETRY_SLEEP_TIME`: seconds between publishes, default is 10.
* `PI_TELEMETRY_SERVICES`: comma-separated systemd service names to include in
  addition to auto-detected services.


## Status topics

Telemetry is published to `${MQTT_TOPIC_PREFIX}/${PI_TELEMETRY_ID}/telemetry`.

Service status is published retained to
`${MQTT_TOPIC_PREFIX}/${PI_TELEMETRY_ID}/status`.

The service publishes `{"state":"connected"}` after MQTT connect and registers
a retained last-will message of `{"state":"lost-connection"}`.


## Uninstall

This will stop and disable the systemd service and remove the installed files (including the customized configuration file).

* `sudo make uninstall`
