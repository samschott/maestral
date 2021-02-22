---
layout: single
title: maestral autostart
permalink: /cli/autostart/
sidebar:
  nav: "cli-docs"
---

Automatically start the sync daemon on login.

A systemd or launchd service will be created to start a sync daemon for the given
configuration on user login. If invoked without a flag `-Y` or `-N`, this command will
print the current autostart status to the console.

To start the GUI on login, please use the corresponing option in the settings panel.

### Syntax

```
maestral autostart [OPTIONS]
```

### Options

```
-Y, --yes
-N, --no
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```
