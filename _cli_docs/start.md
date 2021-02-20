---
layout: single
permalink: /cli/start/
sidebar:
  nav: "cli-docs"
---

# maestral start

Start the sync daemon. This will also run an interactive setup dialog if a Dropbox account
hasn't been linked yet. You can specify different config names to link and sync with
multiple Dropbox accounts.

### Syntax

```
maestral start [OPTIONS]
```

### Examples

```shell
# startes the sync daemon with the "work" config
$ maestral start -c "work"

# startes maestral in the foreground and prints logs to console
$ maestral start -f -v
```

### Options

```
-f, --foreground          Start Maestral in the foreground.
-v, --verbose             Print log messages to stdout when used together with '-f'.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for command.
```
