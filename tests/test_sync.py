# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from maestral.sync import (
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent,
    UpDownSync
)


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


def test_clean_local_events():

    cleaned_file_events_test0 = UpDownSync._clean_local_events(file_events_test0)
    cleaned_file_events_test1 = UpDownSync._clean_local_events(file_events_test1)
    cleaned_file_events_test2 = UpDownSync._clean_local_events(file_events_test2)
    cleaned_file_events_test3 = UpDownSync._clean_local_events(file_events_test3)
    cleaned_file_events_test4 = UpDownSync._clean_local_events(file_events_test4)
    cleaned_file_events_test5 = UpDownSync._clean_local_events(file_events_test5)

    assert set(cleaned_file_events_test0) == set(res0)
    assert set(cleaned_file_events_test1) == set(res1)
    assert set(cleaned_file_events_test2) == set(res2)
    assert set(cleaned_file_events_test3) == set(res3)
    assert set(cleaned_file_events_test4) == set(res4)
    assert set(cleaned_file_events_test5) == set(res5)


# Create a Dropbox test account to automate the below test.
# We will not test individual methods of `maestral.sync` but rather ensure
# an effective result: successful syncing and conflict resolution in
# standard and challenging cases.

def test_sync_cases():
    # TODO:
    #  * Remote file replaced with a folder (OK)
    #  * Remote folder replaced with a file (OK)
    #  * Local file replaced with a folder (Dropbox always creates conflicting copy)
    #  * Local folder replaced with a file (Dropbox always creates conflicting copy)
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
    #    Local rev is None but file exists => conflicting copy created.
    #  * Remote item deleted -> registered -> local item created before download (OK):
    #    Local rev == remote rev == None. Deletion will not be carried out.
    #  * Remote item deleted -> registered -> local item deleted before download (OK):
    #    Local rev exists: deletion will be carried out locally and fail silently.
    #  * Remote item deleted -> registered -> local item modified before download (OK):
    #    Local rev != None, deletion will be carried out. Fix by comparing mtime and
    #    keep local item if local_mtime > last_sync.
    #  * Remote item modified -> registered -> local item modified before download (OK):
    #    Local rev != remote rev. Compare ctime and create conflicting copy if ctime >
    #    last_sync and file contents are different.

    pass
