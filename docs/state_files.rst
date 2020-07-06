
State files
===========

Maestral saves its persistent state in two files: ``{config_name}.index`` for the file
index and ``{config_name}.state`` for anything else. Both files are located at
``$XDG_DATA_DIR/maestral`` on Linux (typically ``~/.local/share/maestral``) and
``~/Library/Application Support/maestral`` on macOS. Each configuration will get its
own state file.


Index file
**********

The index file contains all the tracked files and folders with their lower-case path
relative to the Dropbox folder and their "rev". Each line contains a single entry, written
as a dictionary ``{path: rev}`` in json format, for example:

.. code-block:: python

    {"/test folder/subfolder/file.txt": "015a4ae1f15853400000001695a6c40"}

If there are multiple entries (lines) which refer to the same path, the last entry
overwrites any previous entries. This allows rapidly updating the rev for a file or folder
by appending a new line to the index file without needing to write an entire file. An
entry with ``rev == None`` means that any previous entries for this path and its children
should be discarded.

After a sync cycle has completed, the file is cleaned up and all duplicate or empty
entries are removed.


State file
**********

The state file has the following sections:

.. code-block:: ini

    [account]
    
    email = foo@bar.com
    display_name = Foo Bar
    abbreviated_name = FB
    type = business
    usage = 39.2% of 1312.8TB used
    usage_type = team
    
    # The type of OAuth access token used:
    # legacy: long-lived token
    # offline: short-lived token with long-libed refresh token
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
    
    # Dbx paths with sync errors
    download_errors = []
    
    # Dbx paths of interrupted downloads
    pending_downloads = []
    
    # List of recent file-changes as dicts
    recent_changes = []

    [main]
    
    # State file version (not the Maestral version!)
    version = 12.0.0

Notably, account info which can be changed by the user such as the email address is saved
in the state file while only the fixed Dropbox ID is saved in the config file.
