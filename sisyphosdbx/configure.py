
"""
1. Establish session, get token if necessary
2. Configure local Dropbox folder, set firstsync = True if new folder is given
3. Configure excluded foldes

"""
import os
import os.path as osp
from dropbox import files
from sisyphosdbx.config.main import CONF
from sisyphosdbx.config.base import get_home_dir


class Configure():

    def __init__(self, client):

        self.client = client

    def set_dropbox_directory(self):
        """
        Configure dropbox directory. Will trigger a full indexing on next sync.
        """

        def ask_for_path():
            dropbox_path = input("Dropbox folder location:").strip().strip("'")
            dropbox_path = osp.abspath(dropbox_path)

            if dropbox_path == "":
                dropbox_path = osp.join(get_home_dir(), 'Dropbox')

            if not osp.exists(dropbox_path):
                msg = "Dropbox folder does not exist. Should we create?"
                yes = yesno(msg, True)
                if yes:
                    os.makedirs(dropbox_path)
                else:
                    dropbox_path = ask_for_path()

            return dropbox_path

        path = ask_for_path()

        self.client.dropbox_path = path
        CONF.set('main', 'path', path)
        CONF.set('main', 'lastsync', False)

    def ask_for_excluded_folders(self):

        folders = []

        result = self.client.dbx.files_list_folder("", recursive=False)

        for entry in result.entries:
            if isinstance(entry, files.FolderMetadata):
                yes = yesno("Exclude '%s' from sync?" % entry.path_display, False)
                if yes:
                    folders.append(entry.path_display)

        self.client.excluded_folders = folders
        CONF.set('main', 'excluded_folders', folders)


def yesno(message, default):
    """Handy helper function to ask a yes/no question.

    A blank line returns the default, and answering
    y/yes or n/no returns True or False.
    Retry on unrecognized answer.
    Special answers:
    - q or quit exits the program
    - p or pdb invokes the debugger
    """
    if default:
        message += ' [Y/n] '
    else:
        message += ' [N/y] '
    while True:
        answer = input(message).strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        if answer in ('q', 'quit'):
            print('Exit')
            raise SystemExit(0)
        if answer in ('p', 'pdb'):
            import pdb
            pdb.set_trace()
        print('Please answer YES or NO.')
