
State files
===========

Maestral saves its persistent state in two files: ``{config_name}.db`` for the file
index and ``{config_name}.state`` for anything else. Both files are located at
``$XDG_DATA_DIR/maestral`` on Linux (typically ``~/.local/share/maestral``) and
``~/Library/Application Support/maestral`` on macOS. Each configuration will get its
own state file.

Database
********

The index is stored in a SQLite database with contains the sync index, the sync event
history of the last week and a cache of locally calculated content hashes. SQLAlchemy is
used to manage the database and the table declarations are given by the definitions of
:class:`maestral.database.IndexEntry`, :class:`maestral.database.SyncEvent` and
:class:`maestral.database.HashCacheEntry`.

State file
**********

The state file has the following sections:

.. code-block:: ini

    [main]

    # State file version (not the Maestral version!)
    version = 12.0.0

    [account]
    
    email = foo@bar.com
    display_name = Foo Bar
    abbreviated_name = FB
    type = business
    usage = 39.2% of 1312.8TB used
    usage_type = team
    
    # The type of OAuth access token used:
    # legacy: long-lived token
    # offline: short-lived token with long-lived refresh token
    token_access_type = offline

    [app]
    
    # Version for which update / migration scripts have
    # run. This is bumped to the currently installed
    # version after an update.
    updated_scripts_completed = 1.2.0
    
    # Time stamp of last update notification
    update_notification_last = 0.0
    
    # Latest avilable release
    latest_release = 1.2.0
    

    [sync]
    
    # Cursor reflecting last-synced remote state
    cursor = ...
    
    # Time stamp reflecting last-synced local state
    lastsync = 1589979736.623609
    
    # Time stamp of last full reindexing
    last_reindex = 1589577566.8533309

    # Lower case Dropbox paths with upload sync errors
    upload_errors = []

    # Lower case Dropbox paths with download sync errors
    download_errors = []

    # Lower case Dropbox paths of interrupted uploads
    pending_uploads = []

    # Lower case Dropbox paths of interrupted downloads
    pending_downloads = []


Notably, account info which can be changed by the user such as the email address is saved
in the state file while only the fixed Dropbox ID is saved in the config file.
