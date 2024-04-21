#### Changed:

* Dropped support for Python 3.7 which was reached end-of-life on 27 Jun 2023.

#### Fixed:

* Fixes the default systemd service name when enabling autostart on Linux. This was a regression in v1.7.3. Autostart entries created with `maestral autostart -Y` prior to v1.7.3 will continue to work.
* Fixes an issue where data transport errors that are retried could result in zero byte files being created in the cloud if the local file size is smaller than 4 MB.
* Fixes an issue where renaming a local file by making unicode normalisation changes only could lead to a cycle of deletion and re-creation on Dropbox servers when syncing with Maestral from multiple machines.
