---
title: maestral diff
permalink: /cli/diff
---

Compare two revisions of a file.

If no revs are passed to the command, you can select the revisions interactively. If
only one rev is passed, it is compared to the local version of the file. The diff is
shown via a pager if longer 30 lines.

**Warning:** The specified revisions will be downloaded to temp files and loaded into
memory to generate the diff. Depending on the file size, this may use significant disk
space and memory.

### Syntax

```
maestral diff [OPTIONS] DROPBOX_PATH
```

### Options

```
-v, --rev TEXT             Revisions to compare (multiple allowed).
--no-color                 Don't use colors for the diff.
--no-pager                 Don't use a pager for output.
-l, --limit INTEGER RANGE  Maximum number of revs to list.  [default: 10]
-c, --config-name CONFIG   Run command with the given configuration.
--help                     Show help for this command and exit.
```
