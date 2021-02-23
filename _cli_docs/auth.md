---
layout: single
title: maestral auth
permalink: /cli/auth/
sidebar:
  nav: "cli-docs"
---

A command group to link, unlink and view the linked Dropbox account.

### Examples

```shell
# links a new account to the "work" config
$ maestral auth link -c "work"

# prints information about the linked account for the "work" config
$ maestral auth status -c "work"

Email:         dropbox-user@gmail.com
Account-type:  Basic
Dropbox-ID:    dbid:KJHasjoikjhkh192379123nijh98

# unlinks the account linked to the "work" config
$ maestral auth unlink -c "work"
```

## maestral auth link

Link a new Dropbox account.

### Syntax

```
maestral auth link [OPTIONS]
```

### Options

```
-r, --relink              Relink to the current account. Keeps the sync state.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```

## maestral auth status

View authentication status.

### Syntax

```
maestral auth status [OPTIONS]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```

## maestral auth unlink

Unlink your Dropbox account.

### Syntax

```
maestral auth unlink [OPTIONS]
```

### Options

```
-Y, --yes                 Skip the confirmation prompt.
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```