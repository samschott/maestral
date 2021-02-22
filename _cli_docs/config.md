---
layout: single
title: maestral config
permalink: /cli/config/
sidebar:
  nav: "cli-docs"
---

A command group for direct access to config values.

**Warning:** Changing some config values must be accompanied by maintenance tasks. For
example, changing the config value for the Dropbox location needs to be accompanied by
actually moving the folder. This command only gets / sets the value in the config file.
Most changes will also require a restart of the daemon to become effective.

Use the commands from the Settings section instead wherever possible. They will take
effect immediately, perform accompanying tasks for you, and never leave the daemon in an
inconsistent state.

Currently available config keys are:

- `path`: the location of the local Dropbox folder
- `excluded_items`: list of files or folders excluded by selective sync
- `account_id`: the ID of the linked Dropbox account
- `notification_level`: the level for desktop notifications
- `log_level`: the log level.
- `update_notification_interval`: interval in secs to check for updates
- `keyring`: the keyring backend to use (full path of the class)
- `reindex_interval`: the interval in seconds for full reindexing
- `max_cpu_percent`: maximum CPU usage target per core
- `keep_history`: the sync history to keep in seconds
- `upload`: if upload sync is enabled
- `download`: if download sync is enabled

## maestral config get

Print the value of a given configuration key.

### Syntax

```
maestral config get [OPTIONS] KEY
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral config get

Update configuration with a value for the given key.

Values will be cast to the proper type, raising an error where this is not possibly. For
instance, setting a boolean config value to `1` will actually set it to `True`.

### Syntax

```
maestral config set [OPTIONS] KEY VALUE
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```