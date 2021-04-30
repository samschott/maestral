---
title: Start on login
permalink: /docs/autostart
---

File syncing should happen in the background and should require as little user
interaction as possible. Therefore, both the GUI and the daemon provide options to start
on login.

The settings panel provides a checkbox to start the GUI on login. On macOS, this will
create an appropriate launchd service. On Linux, this will create an appropriate
".desktop" entry.

The CLI includes an equivalent command `maestral autostart` which will create the
appropriate systemd (Linux) or launchd (macOS) entry to start the Maestral daemon with
the selected config on login. If used together with the GUI's "Start on login" option,
the GUI will simply attach itself to the started daemon.

## Creating your own systemd service file

On Linux, the `maestral autostart` command sets up and enables a systemd service with
reasonable defaults for each config with which it is run. However, in some cases, it may
make sense to manually create a systemd service file with custom settings.

To run the Maestral with your own systemd configuration, you can adapt the template
below and save it at "~/.config/systemd/user/maestral.service". `/usr/local/bin/maestral`
should be replaced with the path to the command line script, as returned by `which
maestral`.

```ini
[Unit]
Description = Maestral daemon

[Service]
Type = notify
NotifyAccess = exec
ExecStart = /usr/local/bin/maestral start -f
ExecStop = /usr/local/bin/maestral stop
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
