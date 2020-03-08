# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from watchdog.events import (
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent,
    DirCreatedEvent, DirDeletedEvent, DirMovedEvent,
)
from maestral.sync import UpDownSync


def path(i):
    return f'test_{i}.txt'


# Simple cases
file_events_test0 = [
    # created + deleted -> None
    FileCreatedEvent(path(1)),
    FileDeletedEvent(path(1)),
    # deleted + created -> modified
    FileDeletedEvent(path(2)),
    FileCreatedEvent(path(2)),
]

res0 = [
    # created + deleted -> None
    # deleted + created -> modified
    FileModifiedEvent(path(2))
]

# Single file events, keep as is
file_events_test1 = [
    FileModifiedEvent(path(1)),
    FileCreatedEvent(path(2)),
    FileDeletedEvent(path(3)),
    FileMovedEvent(path(4), path(5)),
]

res1 = [
    FileModifiedEvent(path(1)),
    FileCreatedEvent(path(2)),
    FileDeletedEvent(path(3)),
    FileMovedEvent(path(4), path(5)),
]

# Difficult move cases
file_events_test2 = [
    # created + moved -> created
    FileCreatedEvent(path(1)),
    FileMovedEvent(path(1), path(2)),
    # moved + deleted -> deleted
    FileMovedEvent(path(1), path(4)),
    FileDeletedEvent(path(4)),
    # moved + moved back -> modified
    FileMovedEvent(path(5), path(6)),
    FileMovedEvent(path(6), path(5)),
    # moved + moved -> deleted + created (this is currently not handled as a single moved)
    FileMovedEvent(path(7), path(8)),
    FileMovedEvent(path(8), path(9)),
]

res2 = [
    # created + moved -> created
    FileCreatedEvent(path(2)),
    # moved + deleted -> deleted
    FileDeletedEvent(path(1)),
    # moved + moved back -> modified
    FileModifiedEvent(path(5)),
    # moved + moved -> deleted + created (this is currently not handled as a single moved)
    FileDeletedEvent(path(7)),
    FileCreatedEvent(path(9)),
]

# Gedit save event
file_events_test3 = [
    FileCreatedEvent('.gedit-save-UR4EC0'),         # save new version to tmp file
    FileModifiedEvent('.gedit-save-UR4EC0'),        # modify tmp file
    FileMovedEvent(path(1), path(1) + '~'),         # move old version to backup
    FileMovedEvent('.gedit-save-UR4EC0', path(1)),  # replace old version with tmp file
]

res3 = [
    FileModifiedEvent(path(1)),    # modified file
    FileCreatedEvent(path(1) + '~'),  # backup
]

# macOS safe-save event
file_events_test4 = [
    FileMovedEvent(path(1), path(1) + '.sb-b78ef837-dLht38'),  # move to backup
    FileCreatedEvent(path(1)),                                 # create new version
    FileDeletedEvent(path(1) + '.sb-b78ef837-dLht38'),         # delete backup
]

res4 = [
    FileModifiedEvent(path(1)),  # modified file
]

# Word on macOS created event
file_events_test5 = [
    FileCreatedEvent(path(1)),
    FileDeletedEvent(path(1)),
    FileCreatedEvent(path(1)),
    FileCreatedEvent('~$' + path(1)),
]

res5 = [
    FileCreatedEvent(path(1)),         # created file
    FileCreatedEvent('~$' + path(1)),  # backup (will be deleted when file is closed)
]


# type changes
file_events_test6 = [
    # keep as is
    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(1)),
    # keep as is
    DirDeletedEvent(path(2)),
    FileCreatedEvent(path(2)),
]

res6 = [
    # keep as is
    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(1)),
    # keep as is
    DirDeletedEvent(path(2)),
    FileCreatedEvent(path(2)),
]


# difficult type changes
file_events_test7 = [
    # convert to FileDeleted -> DirCreated
    FileModifiedEvent(path(1)),
    FileDeletedEvent(path(1)),
    FileCreatedEvent(path(1)),
    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(1)),
    # convert to FileDeleted(path1) -> DirCreated(path2)
    FileModifiedEvent(path(1)),
    FileDeletedEvent(path(1)),
    FileCreatedEvent(path(1)),
    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(1)),
    DirMovedEvent(path(1), path(2)),
]

res7 = [
    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(1)),

    FileDeletedEvent(path(1)),
    DirCreatedEvent(path(2)),
]


class DummyUpDownSync(UpDownSync):

    def __init__(self):
        pass

    def _should_split_excluded(self, event):
        return False

    def is_excluded(self, dbx_path):
        return False


# TODO: test splitting mignore paths
def test_clean_local_events():

    sync = DummyUpDownSync()

    cleaned_file_events_test0 = sync._clean_local_events(file_events_test0)
    cleaned_file_events_test1 = sync._clean_local_events(file_events_test1)
    cleaned_file_events_test2 = sync._clean_local_events(file_events_test2)
    cleaned_file_events_test3 = sync._clean_local_events(file_events_test3)
    cleaned_file_events_test4 = sync._clean_local_events(file_events_test4)
    cleaned_file_events_test5 = sync._clean_local_events(file_events_test5)
    cleaned_file_events_test6 = sync._clean_local_events(file_events_test6)

    assert set(cleaned_file_events_test0) == set(res0)
    assert set(cleaned_file_events_test1) == set(res1)
    assert set(cleaned_file_events_test2) == set(res2)
    assert set(cleaned_file_events_test3) == set(res3)
    assert set(cleaned_file_events_test4) == set(res4)
    assert set(cleaned_file_events_test5) == set(res5)
    assert set(cleaned_file_events_test6) == set(res6)


# Create a Dropbox test account to automate the below test.
# We will not test individual methods of `maestral.sync` but rather ensure
# an effective result: successful syncing and conflict resolution in
# standard and challenging cases.

def test_sync_cases():
    # Currently those tests are performed manually with the following test cases:
    #
    #  * Remote file replaced with a folder (OK): Check mtime and create CC of local file
    #    if necessary.
    #  * Remote folder replaced with a file (OK):
    #    Recurse through ctimes of children and check if we have any un-synced changes.
    #    If yes, create CCs for those items. Others will be deleted.
    #  * Local file replaced with a folder (OK):
    #    Check server-modified time of file and only delete if older. Otherwise, let
    #    Dropbox handle creating a CC.
    #  * Local folder replaced with a file (NOK):
    #    Possible data loss on conflict, could solve by checking folder for changes since
    #    last cursor before deletion.
    #  * Remote and local items modified during sync pause (OK)
    #  * Remote and local items created during sync pause (OK)
    #  * Remote and local items deleted during sync pause (OK)
    #  * Local item created / modified -> registered for upload -> deleted before up: (OK)
    #    Ok because FileNotFoundError will be caught silently.
    #  * Local item created / modified -> uploaded -> deleted before re-download: (OK)
    #    Will not be re-downloaded if rev is still in index.
    #  * Local item created / modified -> uploaded -> modified before re-download: (OK)
    #    Will not be re-downloaded if rev is the same.
    #  * Local item deleted -> uploaded -> created before re-download: (OK)
    #    Local rev == remote rev == None. Deletion will not be carried out.
    #  * Remote item created -> registered -> local item created before download (OK):
    #    Local rev is None but file exists => CC created.
    #  * Remote item deleted -> registered -> local item created before download (OK):
    #    Local rev == remote rev == None. Deletion will not be carried out.
    #  * Remote item deleted -> registered -> local item deleted before download (OK):
    #    Local rev exists: deletion will be carried out locally and fail silently.
    #  * Remote item deleted -> registered -> local item modified before download (OK):
    #    Local rev != None, deletion will be carried out. Fix by comparing mtime and
    #    keep local item if local_mtime > last_sync.
    #  * Remote item modified -> registered -> local item modified before download (OK):
    #    Local rev != remote rev. Compare ctime and create CC if ctime > last_sync and
    #    file contents are different.
    #
    #  Note: CC = conflicting copy
    #

    pass
