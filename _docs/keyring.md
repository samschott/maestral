---
title: Keyring integration
permalink: /docs/keyring
---

The Dropbox authentication token will be saved in your system's keyring where available.

Maestral will work with the following keyrings, in order of preference:

* macOS Keychain
* Keyrings implementing the Secret Service D-Bus interface
  (Gnome Keyring, KWallet, KeePassXC and others)
* KWallet directly
* Plain text storage

The best keyring will be chosen when linking to an account and will be used until
unlinked.

Note that keyrings advertised over D-Bus require a running D-Bus session which may not
be available on headless sessions. If such a D-Bus service is not available or the user
does not unlock the keyring, Maestral will fall back to storing the token in a plain
text file at `~/.local/share/python_keyring/keyring_pass.cfg` when linking. This will
only be secure if the home folder is encrypted.

Please also note that while macOS limits access to a Keychain item only to the
application that created it, most Linux keyrings can only be unlocked for all
applications or none at all.
