---
layout: single
permalink: /docs/autostart
sidebar:
  nav: "docs"
---

# Systemd and launchd integration

File syncing should happen in the background and should require as little user
interaction as possible. To simplify this, the GUI includes an option to automatically
start on login. On macOS, this will create an appropriate launchd service which will
show up as a "Login item" in System Preferences. On Linux, this will create an
appropriate ".desktop" entry.

The CLI includes an equivalent command `maestral autostart` which will create the
appropriate systemd (Linux) or launchd (macOS) entry to start the Maestral daemon with
the selected config on login. If used together with the GUI's "Start on login" option,
the GUI will simply attach itself to the started daemon.

## systemd integration

Maestral plays nicely with systemd. This means that it will notify systemd of its status
while it is running and send log output to the journal. The latter requires the
installation of [python-systemd](https://github.com/systemd/python-systemd) which is not
a default requirement. If you install Maestral with the syslog option `pip3 install
maestral[syslog]`, this dependency will be automatically installed for you. Note however
that a pip installation will build python-systemd from source and requires gcc, systemd
headers and python headers and may therefore fail on some systems. It is recommended to
install python-systemd from your distribution's package manager instead:

On Fedora/RHEL/CentOS:
```
dnf install python3-systemd
```
On Debian/Ubuntu/Mint:
```
apt-get install python3-systemd
```

The `maestral autostart` command setup and enable a systemd service with reasinable
defaults for each config with which it is run. However, in some cases, it may make sense
to manually create a systemd service file with custom settings.

To run the Maestral with your own systemd configuration, you can adapt the template
below and save it at "~/.config/systemd/user/maestral.service". `/usr/bin/maestral`
should be replaced with the path to the command line script, as returned by `which
maestral`.

```ini
[Unit]
Description = Maestral daemon

[Service]
Type = notify
NotifyAccess = exec
ExecStart = /usr/bin/maestral start -f
ExecStop = /usr/bin/maestral stop
ExecStopPost=/usr/bin/env bash -c "if [ ${SERVICE_RESULT} != success ]; \
then notify-send Maestral 'Daemon failed'; fi"
WatchdogSec = 30s

[Install]
WantedBy = default.target
```

This configures Maestral as a notify service, i.e., systemd will expect it to send
periodic status updates. On your next login, the Maestral daemon will be started
automatically. The command given for `ExecStopPost` will send a notification when
Maestral crashes (during startup or later).

To sync a different account, configure it first and use `maestral start -f -c CONFIG`
instead (and rename the service file accordingly).
