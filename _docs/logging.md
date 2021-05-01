---
title: Logging
permalink: /docs/logging
---

Maestral will log its activity for debugging purposes. Those logs are not uploaded or
shared with anyone but are saved to your drive. The exact location depends on your
platform:

* macOS: `~/Library/Logs/maestral`
* Linux: `$XDG_CACHE_HOME/maestral`
* Linux fallback: `~/.cache/maestral`

Log files are rotated before exceeding 20 MB and there will be at most two log files, with
the oldest file being replaced on-demand.

By default, the log level is set to "INFO" and no sensitive information such as file
names or folder structures are saved to the log. You can however reduce the log level to
"DEBUG" for more detailed information on individual files and conflict resolution.
Logging must be configured through the [`maestral log`]({{ site.baseurl }}/cli/log)
command and any changes will take effect immediately.

<p><b>Note:</b> Setting the log level to DEBUG will generate detailed logs on sync
activity including potentially private information such as file names and modification
times. Some versions of Maestral may also print environment variables to the logs. Use a
log level of INFO or higher to keep the log free of any private information.
</p>{: .notice--info}

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
