---
layout: single
permalink: /cli/notify/
sidebar:
  nav: "cli-docs"
---

# maestral notify

A command group to manage desktop notifications.

## maestral notify level

Get or set the level for desktop notifications.

Changes will take effect immediately.

### Syntax

```
maestral notify level [OPTIONS] [[ERROR|SYNCISSUE|FILECHANGE]]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral notify snooze

Snooze desktop notifications of file changes.

### Syntax

```
maestral notify snooze [OPTIONS] MINUTES
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```
