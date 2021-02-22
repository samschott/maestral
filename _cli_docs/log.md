---
layout: single
title: maestral log
permalink: /cli/log/
sidebar:
  nav: "cli-docs"
---

A command group to wiew and manage the log.

### Examples

```shell
# get the current log level
$ maestral log level
Log level: INFO

# set the log level
$ maestral log level DEBUG
✓ Log level set to DEBUG.

# show logs in an external editor
$ maestral log show -e
```

## maestral log clear

Clear the log files.

### Syntax

```
maestral log clear [OPTIONS]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral log level

Get or set the log level.

Changes will take effect immediately.

### Syntax

```
maestral log level [OPTIONS] [[DEBUG|INFO|WARNING|ERROR]]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral log show

Print logs to the console.

### Syntax

```
maestral log show [OPTIONS]
```

### Options

```
-e, --external            Open in external program.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```