import sys
import os.path as osp


def run_daemon():
    from sysiphusdbx import SisyphosClient, LocalMonitor, RemoteMonitor
    from config.main import CONF

    import logging

    logger = logging.getLogger()
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

    client = SisyphosClient()

    first_sync = (not CONF.get('internal', 'lastsync') or
                  CONF.get('internal', 'cursor') == '' or
                  not osp.isdir(CONF.get('sysiphusdbx', 'path')))

    if first_sync:
        from sysiphusdbx import Configure
        configure = Configure(client)
        configure.set_dropbox_directory()
        configure.ask_for_excluded_folders()
        CONF.set('internal', 'cursor', '')
        CONF.set('internal', 'lastsync', None)

        result = False
        while not result:
            result = client.get_remote_dropbox()

    remote = RemoteMonitor(client)
    local = LocalMonitor(client, remote)

    local.upload_local_changes_after_inactive()

    remote.start()
    local.start()


# generate sys.argv dictionary
if len(sys.argv) > 1:
    parameters = sys.argv[2:]
    wtd = sys.argv[1]
else:
    wtd = "brick"

if wtd == "--client":
    from sysiphusdbx import client

    print("""SisyphosDBX
(c) Sam Schott, 2018
made with Dropbox SDK from https://www.dropbox.com/developers/reference/sdk \n""")
    client.SisyphosClient()

elif wtd == "--help":
    print("""
Syntax: sisyphosdbx [OPTION]

 --help          - displays this text
 --configuration - runs configuration wizard
 --client        - runs SysiphusDBX API Client
   syntax: sisyphosdbx [OPTION]""")

elif wtd == "--configuration":
    from sysiphusdbx import SisyphosClient, Configure

    client = SisyphosClient()
    configure = Configure(client)
    configure.set_dropbox_directory()
    configure.ask_for_excluded_folders()

elif wtd == "":
    from sysiphusdbx import SisyphosClient, LocalMonitor, RemoteMonitor
    from config.main import CONF

    if CONF.get('sysiphusdbx', 'firstsync'):
        from sysiphusdbx import Configure
        configure = Configure()
        configure.set_dropbox_directory()
        configure.ask_for_excluded_folders()

    client = SisyphosClient()

    local = LocalMonitor(client)
    remote = RemoteMonitor(client)

    local.start()
    remote.start()

    run_daemon()

else:
    print("Invalid syntax. Type orphilia --help for more informations")


if __name__ == '__main__':
    from sysiphusdbx import SisyphosClient, LocalMonitor, RemoteMonitor
    from config.main import CONF

    import logging

    logger = logging.getLogger()
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

    client = SisyphosClient()

    first_sync = (not CONF.get('internal', 'lastsync') or
                  CONF.get('internal', 'cursor') == '' or
                  not osp.isdir(CONF.get('sysiphusdbx', 'path')))

    if first_sync:
        from sysiphusdbx import Configure
        configure = Configure(client)
        configure.set_dropbox_directory()
        configure.ask_for_excluded_folders()
        CONF.set('internal', 'cursor', '')
        CONF.set('internal', 'lastsync', None)

        result = False
        while not result:
            result = client.get_remote_dropbox()

    remote = RemoteMonitor(client)
    local = LocalMonitor(client, remote)

    local.upload_local_changes_after_inactive()

    remote.start()
    local.start()
