
Welcome to Maestral's developer documentation
=============================================

This documentation provides an API reference for the maestral daemon.
It is built from the current dev branch and is intended for developers.
For a user manual and an overview of Maestral's functionality, please
refer to the `wiki <https://github.com/SamSchott/maestral-dropbox/wiki>`_.

.. toctree::
   :hidden:
   :caption: Background
   :maxdepth: 2

   background/sync_logic
   background/logging
   background/config_files
   background/state_files
   background/contributing

.. toctree::
   :hidden:
   :caption: Reference
   :maxdepth: 2

   autoapi/maestral/cli/index
   autoapi/maestral/client/index
   autoapi/maestral/config/index
   autoapi/maestral/constants/index
   autoapi/maestral/daemon/index
   autoapi/maestral/database/index
   autoapi/maestral/errors/index
   autoapi/maestral/fsevents/index
   autoapi/maestral/logging/index
   autoapi/maestral/main/index
   autoapi/maestral/notify/index
   autoapi/maestral/oauth/index
   autoapi/maestral/sync/index
   autoapi/maestral/utils/index

Getting started
***************

To use the Maestral API in a Python interpreter, import the main module first and
initialize a Maestral instance with a configuration name. For this example, we use a new
configuration "private" which is not yet linked to a Dropbox account:

    >>> from maestral.main import Maestral
    >>> m = Maestral(config_name="private")

Config files will be created on-demand for the new configuration, as described in
:doc:`config_files` and :doc:`state_files`.

We now link the instance to an existing Dropbox account. This is done by generating a
Dropbox URL for the user to visit and authorize Maestral. Using the :meth:`link` method,
the resulting auth code is exchanged for an access token to make Dropbox API calls. See
Dropbox's `oauth-guide <https://www.dropbox.com/lp/developers/reference/oauth-guide>`_
for details on the OAuth2 PKCE flow which we use.
When the auth flow is successfully completed, the credentials will be saved in the
system keyring (e.g., macOS Keychain or Gnome Keyring).

    >>> url = m.get_auth_url()  # get token from Dropbox website
    >>> print(f"Please go to {url} to retrieve a Dropbox authorization token.")
    >>> token = input("Enter auth token: ")
    >>> res = m.link(token)

The call to :meth:`link` will return 0 on success, 1 for an invalid code and 2 for
connection errors. We verify that linking succeeded and proceed to create a local
Dropbox folder and start syncing:

    >>> if res == 0:
    ...     m.create_dropbox_directory("~/Dropbox (Private)")
    ...     m.start_sync()
