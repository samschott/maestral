---
layout: single
permalink: /cli/ls/
sidebar:
  nav: "cli-docs"
---

# maestral ls

List contents of a Dropbox directory.

This command fetches the latest contents from Dropbox servers and may therefore take some
time to return.

### Syntax

```
maestral ls [OPTIONS] [DROPBOX_PATH]
```

### Examples

```shell

# list contents of the Dropbox root in a grid
$ maestral ls
.mignore  data_upload     MATLAB_scripts
Photos    Python_scripts  Work

# list contents of a Dropbox folder with details
$ maestral ls -l "Work"

Name              Type       Size  Shared   Syncing   Last Modified
kickoff-2019.key  file    12.5 MB  private  ✓         18 Jan 2021
Berlin 2018       folder        -  shared   ✓         -
Meetings 2019     folder        -  shared   excluded  -

```

### Options

```
-l, --long                Show output in long format with metadata.
-d, --include-deleted     Include deleted items in listing.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```
