---
layout: single
title: maestral status
permalink: /cli/status
sidebar:
  nav: "cli-docs"
---

Show the status of the daemon.

### Syntax

```
maestral status [OPTIONS]
```

### Examples

```shell
# startes the sync daemon with the "work" config
$ maestral status

Account:      dropbox-user@gmail.com (Business)
Usage:        48.3% of 1498.4 TB used
Status:       Connected
Sync errors:  0

```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```
