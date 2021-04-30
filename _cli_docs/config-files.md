---
title: maestral config-files
permalink: /cli/config-files
---

List all configured Dropbox accounts. To create a new configuration, simply start the sync
daemon with a new config name and follow the setup dialog.

### Syntax

```
maestral config-files [OPTIONS]
```

### Examples

```shell
$ maestral config-files

Config name  Account                 Path
private      dropbox-user@gmail.com  ~/.config/maestral/private.ini
work         user@corp.org           ~/.config/maestral/work.ini

```

### Options

```
--clean  Remove config files without a linked account.
--help   Show help for this command and exit.
```
