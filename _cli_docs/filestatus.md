---
title: maestral filestatus
permalink: /cli/filestatus
---

Show the sync status of a local file or folder. This command takes a local path as an
argument. On case-sensitive file systems, the local path must be correctly cased.

Returned value will be "uploading", "downloading", "up to date", "error", or "unwatched"
(for files outside of the Dropbox directory). This will always be "unwatched" if syncing
is paused. This command can be used to for instance to query information for a plugin to
a file-manager.

### Syntax

```
maestral filestatus [OPTIONS] LOCAL_PATH
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```
