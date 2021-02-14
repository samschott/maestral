
Config files
============

The config files are located at ``$XDG_CONFIG_HOME/maestral`` on Linux (typically
``~/.config/maestral``) and ``~/Library/Application Support/maestral`` on macOS. Each
configuration will get its own INI file with the settings documented below.

Config values in the sections ``main`` and ``account`` should not be edited manually but
rather through the corresponding CLI commands or GUI options. This is because changes of
these settings require Maestral to perform accompanying actions, e.g., download items
which have been removed from the excluded list or move the local Dropbox directory.
Those will not be performed if the user edits the options manually.

Changes to the other sections may be performed manually but will only take effect once
Maestral is restarted. Maestral will overwrite the entire config file if any change is
made to one of the options through the ``maestral.config`` module.

.. code-block:: ini

    [main]

    # The current Dropbox directory
    path = /Users/samschott/Dropbox (Maestral)

    # List of excluded files and folders
    excluded_items = ['/test_folder', '/sub/folder']

    # Config file version (not the Maestral version!)
    version = 15.0.0

    [account]

    # Unique Dropbox account ID. The account's email
    # address may change and is therefore not stored here.
    account_id = dbid:AABP7CC5bpYd8cGHqIColDFrMoc9SdhACA4

    [app]

    # Level for desktop notifications:
    # 15 = FILECHAANGE
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
