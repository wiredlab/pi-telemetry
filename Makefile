
SERVICE=pi-telemetry

# directory tree where to install the script
PREFIX=/usr/local
APPDIR=/opt/$(SERVICE)
VENV=$(APPDIR)/venv
PYTHON=python3


# action when calling with no argument
default: enable start


# set the location of the installed script
$(SERVICE).service: $(SERVICE).service.in
	sed -e 's,PREFIX,$(PREFIX),' -e 's,APPDIR,$(APPDIR),' $< > $@

$(VENV):
	$(PYTHON) -m venv $(VENV)

install: $(SERVICE).service $(SERVICE).env $(VENV)
	mkdir -p $(APPDIR)
	install --mode=755 $(SERVICE) $(PREFIX)/bin/
	install --mode=644 pi_telemetry.py $(PREFIX)/bin/
	install --mode=644 pi_telemetry.py $(APPDIR)/
	install --mode=644 requirements.txt $(APPDIR)/
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -r $(APPDIR)/requirements.txt
	install --mode=600 $(SERVICE).env /etc/default/
	install --mode=644 $(SERVICE).service /usr/lib/systemd/system/

uninstall: disable
	rm $(PREFIX)/bin/$(SERVICE)
	rm $(PREFIX)/bin/pi_telemetry.py
	rm -rf $(APPDIR)
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
