import pytest
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
)

from maestral.sync import SyncEngine
from maestral.client import DropboxClient
from maestral.keyring import CredentialStorage
from maestral.config import remove_configuration


@pytest.fixture
def sync():
    sync = SyncEngine(DropboxClient("test-config", CredentialStorage("test-config")))
    sync.dropbox_path = "/"

    yield sync

    remove_configuration("test-config")


def ipath(i):
    """Returns path names '/test 1', '/test 2', ..."""
    return f"/test {i}"


def test_single_file_events(sync: SyncEngine) -> None:
    # only a single event for every path -> no consolidation

    file_events = [
        FileModifiedEvent(ipath(1)),
        FileCreatedEvent(ipath(2)),
        FileDeletedEvent(ipath(3)),
        FileMovedEvent(ipath(4), ipath(5)),
    ]

    res = [
        FileModifiedEvent(ipath(1)),
        FileCreatedEvent(ipath(2)),
        FileDeletedEvent(ipath(3)),
        FileMovedEvent(ipath(4), ipath(5)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_single_path_cases(sync: SyncEngine) -> None:
    file_events = [
        # created + deleted -> None
        FileCreatedEvent(ipath(1)),
        FileDeletedEvent(ipath(1)),
        # deleted + created -> modified
        FileDeletedEvent(ipath(2)),
        FileCreatedEvent(ipath(2)),
        # created + modified -> created
        FileCreatedEvent(ipath(3)),
        FileModifiedEvent(ipath(3)),
    ]

    res = [
        # created + deleted -> None
        # deleted + created -> modified
        FileModifiedEvent(ipath(2)),
        # created + modified -> created
        FileCreatedEvent(ipath(3)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_move_events(sync: SyncEngine) -> None:
    file_events = [
        # created + moved -> created
        FileCreatedEvent(ipath(1)),
        FileMovedEvent(ipath(1), ipath(2)),
        # moved + deleted -> deleted
        FileMovedEvent(ipath(3), ipath(4)),
        FileDeletedEvent(ipath(4)),
        # moved + moved back -> modified
        FileMovedEvent(ipath(5), ipath(6)),
        FileMovedEvent(ipath(6), ipath(5)),
        # moved + moved -> deleted + created
        # (this is currently not handled as a single moved)
        FileMovedEvent(ipath(7), ipath(8)),
        FileMovedEvent(ipath(8), ipath(9)),
    ]

    res = [
        # created + moved -> created
        FileCreatedEvent(ipath(2)),
        # moved + deleted -> deleted
        FileDeletedEvent(ipath(3)),
        # moved + moved back -> modified
        FileModifiedEvent(ipath(5)),
        # moved + moved -> deleted + created
        # (this is currently not handled as a single moved)
        FileDeletedEvent(ipath(7)),
        FileCreatedEvent(ipath(9)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_gedit_save(sync: SyncEngine) -> None:
    file_events = [
        FileCreatedEvent("/.gedit-save-UR4EC0"),  # save new version to tmp file
        FileModifiedEvent("/.gedit-save-UR4EC0"),  # modify tmp file
        FileMovedEvent(ipath(1), ipath(1) + "~"),  # move old version to backup
        FileMovedEvent("/.gedit-save-UR4EC0", ipath(1)),  # replace old version with tmp
    ]

    res = [
        FileModifiedEvent(ipath(1)),  # modified file
        FileCreatedEvent(ipath(1) + "~"),  # backup
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_macos_safe_save(sync: SyncEngine) -> None:
    file_events = [
        FileMovedEvent(ipath(1), ipath(1) + ".sb-b78ef837-dLht38"),  # move to backup
        FileCreatedEvent(ipath(1)),  # create new version
        FileDeletedEvent(ipath(1) + ".sb-b78ef837-dLht38"),  # delete backup
    ]

    res = [
        FileModifiedEvent(ipath(1)),  # modified file
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_msoffice_created(sync: SyncEngine) -> None:
    file_events = [
        FileCreatedEvent(ipath(1)),
        FileDeletedEvent(ipath(1)),
        FileCreatedEvent(ipath(1)),
        FileCreatedEvent("/~$" + ipath(1)),
    ]

    res = [
        FileCreatedEvent(ipath(1)),  # created file
        FileCreatedEvent("/~$" + ipath(1)),  # backup
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_type_changes(sync: SyncEngine) -> None:
    file_events = [
        # keep as is
        FileDeletedEvent(ipath(1)),
        DirCreatedEvent(ipath(1)),
        # keep as is
        DirDeletedEvent(ipath(2)),
        FileCreatedEvent(ipath(2)),
    ]

    res = [
        # keep as is
        FileDeletedEvent(ipath(1)),
        DirCreatedEvent(ipath(1)),
        # keep as is
        DirDeletedEvent(ipath(2)),
        FileCreatedEvent(ipath(2)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_type_changes_difficult(sync: SyncEngine) -> None:
    file_events = [
        # convert to FileDeleted -> DirCreated
        FileModifiedEvent(ipath(1)),
        FileDeletedEvent(ipath(1)),
        FileCreatedEvent(ipath(1)),
        FileDeletedEvent(ipath(1)),
        DirCreatedEvent(ipath(1)),
        # convert to FileDeleted(path1) -> DirCreated(path2)
        FileModifiedEvent(ipath(2)),
        FileDeletedEvent(ipath(2)),
        FileCreatedEvent(ipath(2)),
        FileDeletedEvent(ipath(2)),
        DirCreatedEvent(ipath(2)),
        DirMovedEvent(ipath(2), ipath(3)),
    ]

    res = [
        FileDeletedEvent(ipath(1)),
        DirCreatedEvent(ipath(1)),
        FileDeletedEvent(ipath(2)),
        DirCreatedEvent(ipath(3)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


def test_nested_events(sync: SyncEngine) -> None:
    file_events = [
        # convert to a single DirDeleted
        DirDeletedEvent(ipath(1)),
        FileDeletedEvent(ipath(1) + "/file1.txt"),
        FileDeletedEvent(ipath(1) + "/file2.txt"),
        DirDeletedEvent(ipath(1) + "/sub"),
        FileDeletedEvent(ipath(1) + "/sub/file3.txt"),
        # convert to a single DirMoved
        DirMovedEvent(ipath(2), ipath(3)),
        FileMovedEvent(ipath(2) + "/file1.txt", ipath(3) + "/file1.txt"),
        FileMovedEvent(ipath(2) + "/file2.txt", ipath(3) + "/file2.txt"),
        DirMovedEvent(ipath(2) + "/sub", ipath(3) + "/sub"),
        FileMovedEvent(ipath(2) + "/sub/file3.txt", ipath(3) + "/sub/file3.txt"),
    ]

    res = [
        DirDeletedEvent(ipath(1)),
        DirMovedEvent(ipath(2), ipath(3)),
    ]

    cleaned_events = sync._clean_local_events(file_events)
    assert cleaned_events == res


@pytest.mark.benchmark(
    group="local-event-processing",
    min_time=0.1,
    max_time=5,
)
def test_performance(sync: SyncEngine, benchmark) -> None:
    # 10,000 nested deleted events (5,000 folders, 5,000 files)
    file_events = [DirDeletedEvent(n * ipath(1)) for n in range(1, 5001)]
    file_events += [FileDeletedEvent(n * ipath(1) + ".txt") for n in range(1, 5001)]

    # 10,000 nested moved events (5,000 folders, 5,000 files)
    file_events += [DirMovedEvent(n * ipath(2), n * ipath(3)) for n in range(1, 5001)]
    file_events += [
        FileMovedEvent(n * ipath(2) + ".txt", n * ipath(3) + ".txt")
        for n in range(1, 5001)
    ]

    # 4,995 unrelated created events
    file_events += [FileCreatedEvent(ipath(n)) for n in range(5, 5001)]

    res = [
        DirDeletedEvent(ipath(1)),
        FileDeletedEvent(ipath(1) + ".txt"),
        DirMovedEvent(ipath(2), ipath(3)),
        FileMovedEvent(ipath(2) + ".txt", ipath(3) + ".txt"),
    ]
    res += [FileCreatedEvent(ipath(n)) for n in range(5, 5001)]

    cleaned_events = benchmark(sync._clean_local_events, file_events)

    assert cleaned_events == res
