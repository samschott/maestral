---
layout: single
permalink: /cli/excluded/
sidebar:
  nav: "cli-docs"
---

# maestral excluded

A command group to view and manage excluded files or folders.

## maestral excluded add

Add a file or folder to the excluded list and re-sync.

### Syntax

```
maestral excluded add [OPTIONS] DROPBOX_PATH
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral excluded list

List all excluded files and folders.

### Syntax

```
maestral excluded list [OPTIONS]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```

## maestral excluded remove

Remove a file or folder from the excluded list and re-sync.

It is safe to call this method with items which have already been included, they will
not be downloaded again. If the given path lies inside an excluded folder, the parent
folder will be included as well (but no other items inside it).


### Syntax

```
maestral excluded remove [OPTIONS] DROPBOX_PATH
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show this message and exit.
```