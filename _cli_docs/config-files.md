---
layout: single
title: maestral config-files
permalink: /cli/config-files
sidebar:
  nav: "cli-docs"
---

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
--help   Show help for this command and exit.
```
