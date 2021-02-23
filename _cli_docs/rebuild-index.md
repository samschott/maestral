---
layout: single
title: maestral rebuild-index
permalink: /cli/rebuild-index/
sidebar:
  nav: "cli-docs"
---

Rebuild the sync index.

Rebuilding the index may take several minutes, depending on the size of your Dropbox.
Any changes to local files will be synced once rebuilding has completed. If the daemon is
stopped during the process, rebuilding will start again on the next launch. If the daemon
is not currently running, a rebuild will be scheduled for the next startup.

### Syntax

```
maestral rebuild-index [OPTIONS]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```
