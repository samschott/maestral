---
layout: single
title: maestral sharelink
permalink: /cli/sharelink/
sidebar:
  nav: "cli-docs"
---

A command group to create and manage shared links.

### Examples

```shell
# create a shared link with password protection
$ maestral sharelink create "/Work/Review Meeting" --password="super secret"
https://www.dropbox.com/sh/tjnht9i1l0fliuoXwCnHqa?dl=0

# list all shared linke for an account
$ maestral sharelink list

URL                                        Item            Access    Expires
https://www.dropbox.com/sh/tjnht9Hqa?dl=0  Review Meeting  password  -
https://www.dropbox.com/sh/tjnsdd1qa?dl=0  Photos 2018     public    January 2024

# revoke a shared link
$ maestral sharelink revoke https://www.dropbox.com/sh/tjnsdd1qa?dl=0
```

## maestral sharelink create

Create a shared link for a file or folder. Some options such as password protection for
or an expiry date for the link are only supported on Professional and Plus accounts.

### Syntax

```
maestral sharelink create [OPTIONS] DROPBOX_PATH
```

### Options

```
-p, --password TEXT       Optional password for the link.
-e, --expiry DATE         Expiry time for the link (e.g. '2025-07-24 20:50').
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```

## maestral sharelink list

Lists all shared links for a specific file or folder or for the entire Dropbox if no path
is given.

### Syntax

```
maestral sharelink list [OPTIONS] [DROPBOX_PATH]
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```

## maestral sharelink revoke

### Syntax

```
maestral sharelink revoke [OPTIONS] URL
```

### Options

```
-c, --config-name CONFIG  Run command with the given configuration.
--help                    Show help for this command and exit.
```
