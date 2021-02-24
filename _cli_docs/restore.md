---
layout: single
title: maestral restore
permalink: /cli/restore
sidebar:
  nav: "cli-docs"
---

Restore a previous version of a file.

If no revision number is given, old revisions will be listed to choose from.

### Syntax

```
maestral restore [OPTIONS] DROPBOX_PATH
```

### Examples

```shell
# restore a previous version of a file
$ maestral restore "script.py" --rev 5b92f2015bcf70186ce54
```

### Options

```
-v, --rev TEXT             Revision to restore.
-l, --limit INTEGER RANGE  Maximum number of revs to list. [default: 10]
-c, --config-name CONFIG   Run command with the given configuration.
--help                     Show help for this command and exit.
```
