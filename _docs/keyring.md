---
layout: single
title: Keyring integration
permalink: /docs/keyring
sidebar:
  nav: "docs"
---

The Dropbox authentication token will be saved in your system's keyring where available.
On macOS, it will always be saved in macOS Keychain where only Maestral will have access
to it (or Python, if running as a Python package).

On Linux, the situation is a bit more complicated. In general, Maestral will prefer any
keyring which is advertised via the Secret Service API, as implemented for example by
Gnome Kering and KWallet. Note that those are typically only available in headless
sessions as they are provided by the desktop environment. If such a D-Bus service is not
available or the user does not unlock the keyring, Maestral will fall back to storing
the token in a plain text file at `~/.local/share/python_keyring/keyring_pass.cfg`. This
will only be secure if the home folder is encrypted.

Please also note that while macOS limits access to a Keychain item only to the
application that created it, most Linux keyrings can only be unlocked for all
applications or none at all.
