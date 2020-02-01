# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from maestral.monitor import *


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
    FileMovedEvent(path(1), path(1) + '.sb-b78ef837-dLht38'),  # move old version to backup
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
