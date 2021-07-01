
SERVICE=pi-telemetry

# directory tree where to install the script
PREFIX=/usr/local


# action when calling with no argument
default: enable start


# set the location of the installed script
$(SERVICE).service: $(SERVICE).service.in
	sed 's,PREFIX,$(PREFIX),' $< > $@

install: $(SERVICE).service $(SERVICE).env
	install --mode=755 $(SERVICE) $(PREFIX)/bin/
	install --mode=600 $(SERVICE).env /etc/default/
	install --mode=644 $(SERVICE).service /usr/lib/systemd/system/

uninstall: disable
	rm $(PREFIX)/bin/$(SERVICE)
	rm /etc/default/$(SERVICE).env
	rm /usr/lib/systemd/system/$(SERVICE).service

enable: install
	systemctl enable $(SERVICE).service

start: install reload
	systemctl start $(SERVICE).service

restart: install reload
	systemctl restart $(SERVICE).service

reload:
	systemctl daemon-reload

disable: stop
	systemctl disable $(SERVICE).service

stop:
	systemctl stop $(SERVICE).service

