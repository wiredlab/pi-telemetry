# pi-telemetry
This script and service collects system telemetry for a Raspberry Pi.


## Requirements

* `mosquitto` >= 2.0

`sudo apt update && sudo apt install mosquitto-clients`



## Install

* Clone this repository somewhere convenient.

* Copy `pi-telemetry.env.example` to `pi-telemetry.env` and change the variables as appropriate.  NOTE: systemd does not do variable substitution in this file!

* Edit variable `PREFIX` in `Makefile` if desired.

* `sudo make`


## Uninstall

This will stop and disable the systemd service and remove the installed files (including the customized configuration file).

* `sudo make uninstall`


