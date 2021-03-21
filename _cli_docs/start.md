---
layout: single
title: maestral start
permalink: /cli/start
sidebar:
  nav: "cli-docs"
---

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

The daemon can be optionally started in the foreground with the `-f, --foreground ` flag
instead of spawning a new process. This can be useful for instance when running it as a
systemd service.

If the `--verbose | -v` flag is set, all log messages will be printed to stderr. When used
together with the foreground option, this means that logs will be printed directly to the
console. When the daemon is started in the background, this will not have any effect
because stdout and stderr will be redirected to `/dev/null`.

```
-f, --foreground          Start Maestral in the foreground.
-v, --verbose             Print log messages to stderr.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```
