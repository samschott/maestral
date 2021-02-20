---
layout: single
permalink: /cli/config-files/
sidebar:
  nav: "cli-docs"
---

# maestral config-files

List all configured Dropbox accounts.

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
--help   Show this message and exit.
```
