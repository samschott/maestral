---
title: Configuration file
permalink: /docs/configfile
---

The config files are located at `$XDG_CONFIG_HOME/maestral` on Linux (typically
`~/.config/maestral`) and `~/Library/Application Support/maestral` on macOS. Each
configuration will get its own INI file with the settings documented below.

Changes to the config values may be performed through [`maestral config set`]({{
site.baseurl }}/cli/config) directly but some will only take effect once you restart
Maestral. Any manual changes to the config file may be overwritten if the daemon is
still running.

**You must not change the config values in the sections "main" and "account"
directly** but rather use the corresponding CLI commands or GUI options, for instance
[`maestral move-dir`]({{ site.baseurl }}/cli/move-dir) or
[`maestral excluded add`]({{site.baseurl }}/cli/excluded). This is because changes of
these settings require Maestral to perform accompanying actions, e.g., download folders
which have been removed from the excluded list or move the local Dropbox directory.
Those will not be performed if you edit the values directly.

```ini
[main]

# The current Dropbox directory
path = /Users/samschott/Dropbox (Maestral)

# List of excluded files and folders
excluded_items = ['/test_folder', '/sub/folder']

# Config file version (not the Maestral version!)
version = 15.0.0

[account]

# Unique Dropbox account ID. The account's email
# address may change and is therefore not stored here.
account_id = dbid:AABP7CC5bpYd8cGHqIColDFrMoc9SdhACA4

[app]

# Level for desktop notifications:
# 15 = FILECHAANGE
# 30 = SYNCISSUE
# 40 = ERROR
# 100 = NONE
notification_level = 15

# Level for log messages:
# 10 = DEBUG
# 20 = INFO
# 30 = WARNING
# 40 = ERR0R
log_level = 20

# Interval in sec to check for updates
update_notification_interval = 604800

[sync]

# Interval in sec to perform a full reindexing
reindex_interval = 604800

# Maximum CPU usage per core
max_cpu_percent = 20.0

# Maximum sync event history to keep in seconds
keep_history = 604800

# Whether upload sync is enabled
upload = True

# Whether download sync is enabled
download = True

```
