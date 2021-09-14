
Config files
============

The config files are located at ``$XDG_CONFIG_HOME/maestral`` on Linux (typically
``~/.config/maestral``) and ``~/Library/Application Support/maestral`` on macOS. Each
configuration will get its own INI file with the settings documented below.

Config values for ``path`` and ``excluded_items`` should not be edited manually but
rather through the corresponding CLI commands or GUI options. This is because changes of
these settings require Maestral to perform accompanying actions, e.g., download items
which have been removed from the excluded list or move the local Dropbox directory.
Those will not be performed if the user edits the options manually.

This also holds for the ``account_id`` which will be written to the config file after
successfully completing the OAuth flow with Dropbox servers.

Any changes will only take effect once Maestral is restarted. Any changes made to the
config file may be overwritten without warning if made while the sync daemon is running.

.. code-block:: ini

    [main]

    # Config file version (not the Maestral version!)
    version = 15.0.0

    [auth]

    # Unique Dropbox account ID. The account's email
    # address may change and is therefore not stored here.
    account_id = dbid:AABP7CC5bpYd8ghjIColDFrMoc9SdhACA4

    # The keychain to store user credentials. If "automatic",
    # will be set automatically from available backends when
    # completing the OAuth flow.
    keyring = keyring.backends.macOS.Keyring

    [app]

    # Level for desktop notifications:
    # 15 = FILECHANGE
    # 30 = SYNCISSUE
    # 40 = ERROR
    # 100 = NONE
    notification_level = 15

    # Level for log messages:
    # 10 = DEBUG
    # 20 = INFO
    # 30 = WARNING
    # 40 = ERR0R
    log_level = 20

    # Interval in sec to check for updates
    update_notification_interval = 604800

    [sync]

    # The current Dropbox directory
    path = /Users/UserName/Dropbox (Maestral)

    # List of excluded files and folders
    excluded_items = ['/test_folder', '/sub/folder']

    # Interval in sec to perform a full reindexing
    reindex_interval = 604800

    # Maximum CPU usage per core
    max_cpu_percent = 20.0

    # Sync history to keep in seconds
    keep_history = 604800

    # Enable upload syncing
    upload = True

    # Enable download syncing
    download = True
