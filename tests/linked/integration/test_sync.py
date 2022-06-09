from __future__ import annotations

import platform
import sys
import os
import os.path as osp
import time
import shutil
import timeit
from typing import Union, Mapping, TypeVar, cast

import pytest
from watchdog.events import FileCreatedEvent, FileDeletedEvent, DirDeletedEvent
from dropbox import files

from maestral.main import Maestral
from maestral.core import FileMetadata, FolderMetadata
from maestral.models import SyncEvent, SyncDirection
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import (
    delete,
    move,
    is_fs_case_sensitive,
    normalize,
    normalize_unicode,
    fs_max_lengths_for_path,
    to_existing_unnormalized_path,
    walk,
    get_symlink_target,
)
from maestral.exceptions import (
    PathError,
    SymlinkError,
    NoDropboxDirError,
    InsufficientPermissionsError,
    SyncError,
    FolderConflictError,
)
from maestral.errorhandling import convert_api_errors

from .conftest import wait_for_idle


# mypy cannot yet check recursive type definitions...
DirTreeType = Mapping[str, Union[str, Mapping[str, Union[str, Mapping[str, str]]]]]
T = TypeVar("T", dict, DirTreeType)

if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


HOME = get_home_dir()


# ==== test basic sync =================================================================


def test_setup(m: Maestral) -> None:
    assert_synced(m)
    assert_no_errors(m)


@pytest.mark.parametrize(
    "name",
    [
        "test_file.txt",
        # Test composed unicode character (NFC) "ó" in file name. Decomposed characters
        # are tested separately in test_unicode_decomposed because they are renamed on
        # upload.
        b"f\xc3\xb3lder".decode(),
    ],
)
def test_file_lifecycle(m: Maestral, name: str) -> None:
    """Tests creating, modifying and deleting a file."""

    # Test local file creation.
    tree: DirTreeType = {name: "content"}
    create_local_tree(m.dropbox_path, tree)

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Test local file changes.
    tree = {name: "content changed"}
    create_local_tree(m.dropbox_path, tree)

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Test remote file changes.
    tree = {name: "content 1"}
    create_remote_tree(m, tree)

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Test remote file deletion.
    m.client.remove(f"/{name}")

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_folder_tree_local(m: Maestral) -> None:
    """Tests the upload sync of a nested local folder structure."""

    # Test local tree creation.

    tree: DirTreeType = {
        "test_folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }

    create_local_tree(m.dropbox_path, tree)
    wait_for_idle(m, 10)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Test local tree deletion.

    delete(m.dropbox_path + "/test_folder")

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_folder_tree_remote(m: Maestral) -> None:
    """Tests the download sync of a nested remote folder structure."""

    # Test remote tree creation.

    tree: DirTreeType = {
        "test_folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }

    create_remote_tree(m, tree)

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Test remote tree deletion.

    m.client.remove("/test_folder")
    wait_for_idle(m, 15)

    assert_no_errors(m)
    assert_synced(m, {})


def test_local_indexing(m: Maestral) -> None:
    """Tests the upload sync of a nested local folder structure during startup sync."""

    m.stop_sync()
    wait_for_idle(m)

    # Create a local tree.
    tree: DirTreeType = {
        "test_folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }

    create_local_tree(m.dropbox_path, tree)

    # Start sync and check that all items are indexed and uploaded.
    m.start_sync()
    wait_for_idle(m, 10)

    assert_no_errors(m)
    assert_synced(m, tree)

    # Mutate local state.

    m.stop_sync()
    wait_for_idle(m)

    new_tree: DirTreeType = {
        "test_folder": {
            "sub_file_1.txt": {},  # Replace file with folder.
            "sub_file_2.txt": "content...",  # Modify some file content.
            "sub_folder_1": {},  # Keep folder as is.
            "sub_folder_2": "content",  # Replace folder with file
        }
    }
    delete(m.dropbox_path + "/test_folder")
    create_local_tree(m.dropbox_path, new_tree)

    m.start_sync()
    wait_for_idle(m, 10)

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_case_change_local(m: Maestral) -> None:
    """
    Tests the upload sync of local rename which only changes the casing of the name.
    """

    # Start with nested folders.
    tree: DirTreeType = {
        "folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }
    create_local_tree(m.dropbox_path, tree)
    wait_for_idle(m)

    # Rename local parent folder to upper case.
    shutil.move(m.dropbox_path + "/folder", m.dropbox_path + "/FOLDER")
    wait_for_idle(m)

    # Check that case change was propagated to the server.

    md = m.client.get_metadata("/folder")

    assert isinstance(md, FolderMetadata)
    assert md.name == "FOLDER", "casing was not propagated to Dropbox"

    assert_synced(m)
    assert_no_errors(m)


def test_case_change_remote(m: Maestral) -> None:
    """
    Tests the download sync of a remote rename which only changes the casing of the file
    name.
    """

    # Start with nested folders.
    os.mkdir(m.dropbox_path + "/folder")
    os.mkdir(m.dropbox_path + "/folder/Subfolder")
    wait_for_idle(m)

    assert_synced(m)

    # Rename the remote folder.
    m.client.move("/folder", "/FOLDER", autorename=True)

    wait_for_idle(m)

    # Check that case change was propagated to the local folder.

    md = m.client.get_metadata("/folder")

    assert osp.isdir(m.dropbox_path + "/FOLDER")
    assert osp.isdir(m.dropbox_path + "/FOLDER/Subfolder")
    assert isinstance(md, FolderMetadata)
    assert md.name == "FOLDER", "casing was not propagated to local folder"

    assert_synced(m)
    assert_no_errors(m)


def test_mignore(m: Maestral) -> None:
    """Tests the exclusion of local items by a mignore file."""

    # 1) Test that changes have no effect when the sync is running.

    create_local_tree(m.dropbox_path, {"folder": {}, "bar": {}})

    wait_for_idle(m)

    with open(m.sync.mignore_path, "w") as f:
        f.write("foo/\n")  # ignore folder "foo"
        f.write("bar\n")  # ignore file or folder "bar"
        f.write("build\n")  # ignore file or folder "build"

    wait_for_idle(m)

    new_tree: DirTreeType = {".mignore": "foo/\nbar\nbuild\n", "bar": {}, "folder": {}}

    assert_no_errors(m)
    assert_synced(m, new_tree)

    # 2) Test that items are removed after a restart.

    m.stop_sync()
    wait_for_idle(m)
    m.start_sync()

    os.mkdir(m.dropbox_path + "/foo")
    wait_for_idle(m)

    assert not m.client.get_metadata("/foo")
    assert not m.client.get_metadata("/bar")

    # 3) Test that renaming an item excludes it.

    move(m.dropbox_path + "/folder", m.dropbox_path + "/build")
    wait_for_idle(m)

    assert not m.client.get_metadata("/build")

    # 4) Test that renaming an item includes it.

    move(m.dropbox_path + "/build", m.dropbox_path + "/folder")
    wait_for_idle(m)

    assert_no_errors(m)
    assert m.client.get_metadata("/folder")


def test_move_to_existing_file(m: Maestral) -> None:
    """Tests moving a local file onto another and replacing it."""

    # Create two local files.

    tree: DirTreeType = {"file0.txt": "c0", "file1.txt": "c1"}
    create_local_tree(m.dropbox_path, tree)

    wait_for_idle(m)

    # Move file0 to file1.

    shutil.move(m.dropbox_path + "/file0.txt", m.dropbox_path + "/file1.txt")

    wait_for_idle(m)

    # Check that move was propagated to the server.

    new_tree: DirTreeType = {"file1.txt": "c0"}

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_excluded_folder_cleared_on_deletion(m: Maestral) -> None:
    """
    Tests that an entry in our selective sync excluded list gets removed when the
    corresponding item is deleted.
    """

    dbx_path = "/selective_sync_test_folder"
    local_path = m.to_local_path("/selective_sync_test_folder")

    # Create local folder.
    os.mkdir(local_path)
    wait_for_idle(m)

    # Exclude the folder from sync.
    m.exclude_item(dbx_path)
    wait_for_idle(m)

    assert normalize(dbx_path) in m.excluded_items
    assert m.excluded_status(dbx_path) == "excluded"
    assert not osp.exists(local_path)

    # Check that an excluded folder is removed from excluded_list on deletion.
    m.client.remove(dbx_path)
    wait_for_idle(m)

    assert (
        normalize(dbx_path) not in m.excluded_items
    ), 'deleted item is still in "excluded_items" list'

    assert_synced(m)
    assert_no_errors(m)


def test_unix_permissions(m: Maestral) -> None:
    """
    Tests that a newly downloaded file is created with default permissions for our
    process and that any locally set permissions are preserved on remote file
    modifications.
    """

    # Create a remote file and wait for it to download.
    local_path = m.to_local_path("/file.txt")

    create_remote_tree(m, {"file.txt": "content"})
    wait_for_idle(m)

    # Check if its permissions correspond to the default user permissions by comparing
    # them to a reference file in the home directory.

    reference_file = osp.join(HOME, "reference")

    try:
        open(reference_file, "ab").close()
        assert os.stat(local_path).st_mode == os.stat(reference_file).st_mode
    finally:
        delete(reference_file)

    # Make the local file executable.
    os.chmod(local_path, 0o744)
    new_mode = os.stat(local_path).st_mode  # might not be 744...
    wait_for_idle(m)

    # Perform some remote modifications.
    create_remote_tree(m, {"file.txt": "content 2"})
    wait_for_idle(m)

    # Check that the local permissions have not changed.
    assert os.stat(local_path).st_mode == new_mode

    assert_synced(m)
    assert_no_errors(m)


# ==== test conflict resolution ========================================================


def test_file_conflict_modified(m: Maestral) -> None:
    """Tests conflicting local vs remote file changes."""

    # Create a test file and stop syncing.
    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m)

    # Modify file.txt locally
    create_local_tree(m.dropbox_path, {"file.txt": "content modified conflict"})

    # Modify file.txt on remote.
    create_remote_tree(m, {"file.txt": "content 2"})

    # Resume syncing and check for conflicting copy.
    m.start_sync()

    wait_for_idle(m)

    new_tree: DirTreeType = {
        "file (conflicting copy).txt": "content modified conflict",
        "file.txt": "content 2",
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_file_conflict_created(m: Maestral) -> None:
    """Tests conflicting local vs remote file creations."""

    m.stop_sync()

    # Create local and remote files at the same location with different contents.
    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    create_remote_tree(m, {"file.txt": "content 2"})

    # Resume syncing and check for conflicting copy
    m.start_sync()
    wait_for_idle(m)

    new_tree: DirTreeType = {
        "file (conflicting copy).txt": "content",
        "file.txt": "content 2",
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_remote_file_replaced_by_folder(m: Maestral) -> None:
    """Tests the download sync when a file is replaced by a folder."""

    # Create a test file.

    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # Replace the remote file with folder.
        m.client.remove("/file.txt")
        m.client.make_dir("/file.txt")

    wait_for_idle(m, 10)

    assert_no_errors(m)
    assert_synced(m, {"file.txt": {}})


def test_remote_file_replaced_by_folder_and_unsynced_local_changes(m: Maestral) -> None:
    """
    Tests the download sync when a file is replaced by a folder and the local file has
    unsynced changes.
    """

    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # replace remote file with folder
        m.client.remove("/file.txt")
        m.client.make_dir("/file.txt")

        # create local changes
        create_local_tree(m.dropbox_path, {"file.txt": "content modified"})

    wait_for_idle(m)

    display_name = m.get_account_info().display_name

    new_tree: DirTreeType = {
        f"file ({display_name}'s conflicted copy).txt": "content modified",
        "file.txt": {},
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_remote_folder_replaced_by_file(m: Maestral) -> None:
    """Tests the download sync when a folder is replaced by a file."""

    # Note: we use a folder tree here to test recursive ctime checks.
    tree: DirTreeType = {
        "folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }

    create_local_tree(m.dropbox_path, tree)
    wait_for_idle(m)

    with m.sync.sync_lock:
        # Replace remote folder with a file.
        m.client.remove("/folder")
        create_remote_tree(m, {"folder": "content"})

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"folder": "content"})


def test_remote_folder_replaced_by_file_and_unsynced_local_changes(m: Maestral) -> None:
    """
    Tests the download sync when a folder is replaced by a file and the local folder has
    unsynced changes.
    """

    # Create a remote folder.
    create_remote_tree(m, {"folder": {}})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # Replace the remote folder with a file.
        # Remote state:
        # - '/Sync Tests/folder'

        m.client.remove("/folder")
        create_remote_tree(m, {"folder": "content"})

        # Make some local changes to the folder.
        # Local state:
        # - '/Sync Tests/folder/'
        # - '/Sync Tests/folder/subfolder'
        create_local_tree(m.dropbox_path, {"folder": {"subfolder": {}}})

    wait_for_idle(m)

    # Check for expected result:

    new_tree: DirTreeType = {
        "folder": "content",
        "folder (conflicting copy)": {
            "subfolder": {},
        },
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_local_folder_replaced_by_file(m: Maestral) -> None:
    """Tests the upload sync when a local folder is replaced by a file."""

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:
        # replace local folder with file
        delete(m.dropbox_path + "/folder")
        create_local_tree(m.dropbox_path, {"folder": "content"})

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"folder": "content"})


def test_local_folder_file_deleted_event(m: Maestral) -> None:
    """Tests the upload sync when a local folder is deleted but a FileDeletedEvent
    is emitted instead. The deletion should fail."""

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:
        # Remove the folder.
        with m.sync.fs_events.ignore(DirDeletedEvent(m.dropbox_path + "/folder")):
            delete(m.dropbox_path + "/folder")
        # Queue an artificial file deleted event.
        m.sync.fs_events.queue_event(FileDeletedEvent(m.dropbox_path + "/folder"))

    wait_for_idle(m)

    # Assert that the remote folder is not deleted.
    assert m.get_metadata("/folder")


def test_local_folder_replaced_by_file_and_unsynced_remote_changes(m: Maestral) -> None:
    """
    Tests the upload sync when a local folder is replaced by a file and the remote
    folder has unsynced changes.
    """

    # remote folder is currently not checked for unsynced changes but replaced

    create_local_tree(m.dropbox_path, {"folder": {}})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # replace local folder with file
        delete(m.dropbox_path + "/folder")
        create_local_tree(m.dropbox_path, {"folder": "content"})

        # create remote changes
        create_remote_tree(m, {"folder": {"file.txt": "content"}})

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"folder": "content"})


def test_local_file_replaced_by_folder(m: Maestral) -> None:
    """Tests the upload sync when a local file is replaced by a folder."""

    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # replace local file with folder
        os.unlink(m.dropbox_path + "/file.txt")
        os.mkdir(m.dropbox_path + "/file.txt")

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"file.txt": {}})


def test_local_file_folder_deleted_event(m: Maestral) -> None:
    """Tests the upload sync when a local file is deleted but a folder deleted event is
    emitted instead. The deletion should succeed."""

    create_remote_tree(m, {"file.txt": "content"})
    wait_for_idle(m)

    # Delete local file, suppress FS event.
    with m.sync.fs_events.ignore(FileDeletedEvent(m.dropbox_path + "/file.txt")):
        delete(m.dropbox_path + "/file.txt")

    # Queue fake DirDeletedEvent.
    m.sync.fs_events.queue_event(DirDeletedEvent(m.dropbox_path + "/file.txt"))

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_local_file_replaced_by_folder_and_unsynced_remote_changes(m: Maestral) -> None:
    """
    Tests the upload sync when a local file is replaced by a folder and the remote
    file has unsynced changes.
    """

    # Check if server-modified time > last_sync of file and only delete file if
    # older. Otherwise, let Dropbox handle creating a conflicting copy.

    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    wait_for_idle(m)

    with m.sync.sync_lock:
        # replace local file with folder
        delete(m.dropbox_path + "/file.txt")
        create_local_tree(m.dropbox_path, {"file.txt": {}})

        # create remote changes
        create_remote_tree(m, {"file.txt": "content 2"})

    wait_for_idle(m)

    new_tree: DirTreeType = {
        "file.txt": "content 2",
        "file (1).txt": {},
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_selective_sync_conflict(m: Maestral) -> None:
    """
    Tests the creation of a selective sync conflict when a local item is created with a
    path that is excluded by selective sync.
    """

    create_local_tree(m.dropbox_path, {"folder": {}})
    wait_for_idle(m)

    # exclude 'folder' from sync
    m.exclude_item("/folder")
    wait_for_idle(m)

    assert_synced(m, {})

    # recreate 'folder' locally
    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    new_tree: DirTreeType = {
        "folder (selective sync conflict)": {},
        "folder (selective sync conflict 1)": {},
    }

    assert_synced(m, new_tree)


@pytest.mark.skipif(
    not is_fs_case_sensitive(HOME), reason="file system is not case sensitive"
)
def test_case_conflict(m: Maestral) -> None:
    """
    Tests the creation of a case conflict when a local item is created with a path that
    only differs in casing from an existing path.
    """

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    os.mkdir(m.dropbox_path + "/Folder")
    wait_for_idle(m)

    new_tree: DirTreeType = {
        "folder": {},
        "Folder (case conflict)": {},
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_unicode_decomposed(m: Maestral) -> None:
    """
    Tests the lifecycle of a file with decomposed unicode characters.
    """
    file_name = b"fo\xcc\x81lder".decode()  # decomposed ó (NFD)
    local_path = f"{m.dropbox_path}/{file_name}"

    os.mkdir(local_path)
    wait_for_idle(m)

    if platform.system() == "Darwin":
        # Local file stays as is, macOS treats unicode normalisations transparently.
        assert osp.exists(local_path)
        assert osp.samefile(local_path, normalize_unicode(local_path))
    else:
        # Rename to NFC version on Dropbox servers is mirrored locally.
        assert not osp.exists(local_path)
        assert osp.exists(normalize_unicode(local_path))

    assert_no_errors(m)
    assert_synced(m)

    # Test rename.
    target_path = local_path + "_target"
    os.rename(normalize_unicode(local_path), target_path)
    wait_for_idle(m)

    if platform.system() == "Darwin":
        # Local file stays as is, macOS treats unicode normalisations transparently.
        assert osp.exists(target_path)
        assert osp.samefile(target_path, normalize_unicode(target_path))
    else:
        # Rename to NFC version on Dropbox servers is mirrored locally.
        assert not osp.exists(target_path)
        assert osp.exists(normalize_unicode(target_path))

    assert_no_errors(m)
    assert_synced(m)


def test_unicode_conflict(m: Maestral) -> None:
    """
    Tests the creation of a unicode conflict when a local item is created with a path
    that only differs in utf-8 form from an existing path.
    """
    name_decomposed = b"fo\xcc\x81lder"  # decomposed "ó"
    name_composed = b"f\xc3\xb3lder"  # composed "ó"

    os.mkdir(m.dropbox_path.encode() + b"/" + name_decomposed)
    wait_for_idle(m)

    try:
        os.mkdir(m.dropbox_path.encode() + b"/" + name_composed)
    except FileExistsError:
        # File system / OS does not allow for unicode conflicts, e.g., on macOS.
        return

    wait_for_idle(m)

    new_tree: DirTreeType = {
        name_decomposed.decode(): {},
        f"{name_composed.decode()} (unicode conflict)": {},
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


# ==== test race conditions ============================================================


def test_parallel_deletion_when_paused(m: Maestral) -> None:
    """Tests parallel remote and local deletions of an item."""

    # create a local file
    create_local_tree(m.dropbox_path, {"file.txt": "content"})

    wait_for_idle(m)
    assert_synced(m)

    m.stop_sync()
    wait_for_idle(m)

    # delete local file
    delete(m.dropbox_path + "/file.txt")

    # delete remote file
    m.client.remove("/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_local_and_remote_creation_with_equal_content(m: Maestral) -> None:
    """Tests parallel and equal remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    # create remote file with equal content
    create_remote_tree(m, {"file.txt": "content"})

    m.start_sync()
    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"file.txt": "content"})


def test_local_and_remote_creation_with_different_content(m: Maestral) -> None:
    """Tests parallel and different remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    create_local_tree(m.dropbox_path, {"file.txt": "content"})
    # create remote file with different content
    create_remote_tree(m, {"file.txt": "content 2"})

    m.start_sync()
    wait_for_idle(m)

    new_tree: DirTreeType = {
        "file.txt": "content 2",
        "file (conflicting copy).txt": "content",
    }

    assert_no_errors(m)
    assert_synced(m, new_tree)


def test_local_deletion_during_upload(m: Maestral) -> None:
    """Tests the case where a local item is deleted during the upload."""

    # We mimic a deletion during upload by queueing a fake FileCreatedEvent.
    fake_created_event = FileCreatedEvent(m.dropbox_path + "/file.txt")
    m.manager.sync.fs_events.queue_event(fake_created_event)

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_rapid_local_changes(m: Maestral) -> None:
    """Tests local changes to the content of a file with varying intervals."""

    for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
        time.sleep(t)
        with open(m.dropbox_path + "/file.txt", "a") as f:
            f.write(f"{t} ")

    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {"file.txt": "0.1 0.1 0.5 0.5 1.0 1.0 2.0 2.0 "})


def test_rapid_remote_changes(m: Maestral) -> None:
    """Tests remote changes to the content of a file with varying intervals."""

    create_remote_tree(m, {"file.txt": "content"})
    wait_for_idle(m)

    md = m.client.get_metadata("/file.txt")

    assert isinstance(md, FileMetadata)

    for t in (0.1, 0.2, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5):
        time.sleep(t)

        md = m.client.dbx.files_upload(
            str(t).encode(),
            "/file.txt",
            mode=files.WriteMode.update(md.rev),  # type: ignore
        )

    wait_for_idle(m, 5)

    assert_no_errors(m)
    assert_synced(m, {"file.txt": "2.5"})


# ==== test error handling =============================================================


def test_local_path_error(m: Maestral) -> None:
    """Tests error handling for forbidden file names."""

    # paths with backslash are not allowed on Dropbox
    # we create such a local folder and assert that it triggers a sync issue

    test_path_dbx = "/folder\\"
    test_path_local = m.to_local_path(test_path_dbx)

    os.mkdir(test_path_local)
    wait_for_idle(m)

    assert_sync_error(m, PathError, test_path_local, test_path_dbx, SyncDirection.Up)

    # remove folder with invalid name and assert that sync issue is cleared

    delete(test_path_local)
    wait_for_idle(m)

    assert_no_errors(m)
    assert_synced(m, {})


def test_local_indexing_error(m: Maestral) -> None:
    """Tests handling of PermissionError during local indexing."""

    dbx_path = "/folder/file.txt"
    local_path = m.to_local_path(dbx_path)

    os.makedirs(local_path)
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m)

    # change permissions of local folder
    os.chmod(local_path, 0o000)

    m.start_sync()
    wait_for_idle(m)

    # check for fatal errors
    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[0].local_path == local_path


def test_local_permission_error(m: Maestral) -> None:
    """Tests error handling on local PermissionError."""

    m.stop_sync()

    dbx_path = "/file"
    local_path = m.to_local_path(dbx_path)

    create_local_tree(m.dropbox_path, {"file": "content"})
    os.chmod(m.dropbox_path + "/file", 0o000)

    m.start_sync()
    wait_for_idle(m)

    assert_sync_error(
        m,
        InsufficientPermissionsError,
        local_path,
        dbx_path,
        SyncDirection.Up,
    )

    # reset file permission
    os.chmod(local_path, 0o666)
    os.utime(local_path, times=None)  # touch
    wait_for_idle(m)

    # check that error is cleared and file is uploaded
    assert_no_errors(m)
    assert_synced(m, {"file": "content"})


def test_long_path_error(m: Maestral) -> None:
    """Tests error handling on trying to download an item with a too long path."""

    max_path_length, _ = fs_max_lengths_for_path()

    # Create a remote file with a path name longer than locally allowed.
    test_path = "/nested" * (max_path_length // 6)

    try:
        create_remote_tree(m, {test_path: "content"})
    except PathError:
        pytest.skip(f"Cannot create path with {len(test_path)} chars on Dropbox")

    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(sync_errors) > 0
    assert sync_errors[-1].dbx_path == test_path
    assert sync_errors[-1].type == "PathError"
    assert sync_errors[-1].direction == SyncDirection.Down


@pytest.mark.parametrize(
    "name",
    [
        "file_🦑",  # U+1F991
    ],
)
def test_unicode_forbidden(m: Maestral, name: str) -> None:
    """Tests syncing files with exotic unicode characters."""

    local_path = osp.join(m.dropbox_path, name)

    os.mkdir(local_path)
    wait_for_idle(m)

    assert_sync_error(m, PathError, local_path, direction=SyncDirection.Up)


@pytest.mark.skipif(
    sys.platform != "linux", reason="macOS enforces utf-8 path encoding"
)
def test_unknown_path_encoding(m: Maestral, capsys) -> None:
    """
    Tests the handling of a local path with bytes that cannot be decoded with the
    file system encoding reported by the platform.
    """

    # create a path with Python surrogate escapes and convert it to bytes
    test_path_dbx = "/my_folder_\udce4"
    test_path_local = m.to_local_path(test_path_dbx)
    test_path_local_bytes = os.fsencode(test_path_local)

    # create the local directory while we are syncing
    os.mkdir(test_path_local_bytes)
    wait_for_idle(m)

    # 1) Check that the sync issue is logged

    # This requires that our sync logic from the emitted watchdog event all the
    # way to `SyncEngine._on_local_created` can handle strings with surrogate escapes.

    assert_sync_error(m, PathError, test_path_local, test_path_dbx, SyncDirection.Up)

    # 2) Check that the sync is retried after pause / resume

    # This requires that our logic to save failed paths in our state file and retry the
    # sync on startup can handle strings with surrogate escapes.

    m.stop_sync()
    m.start_sync()

    wait_for_idle(m)

    assert_sync_error(m, PathError, test_path_local, test_path_dbx, SyncDirection.Up)

    # 3) Check that the error is cleared when the file is deleted

    # This requires that `SyncEngine.upload_local_changes_while_inactive` can handle
    # strings with surrogate escapes all they way to `SyncEngine._on_local_deleted`.

    delete(test_path_local_bytes)  # type: ignore
    wait_for_idle(m)

    assert normalize(test_path_dbx) not in m.sync.upload_errors

    assert_synced(m)
    assert_no_errors(m)


def test_symlink_error(m: Maestral) -> None:

    local_path = m.dropbox_path + "/link"

    os.symlink("to_nowhere", local_path)
    wait_for_idle(m)

    assert_sync_error(m, SymlinkError, local_path, direction=SyncDirection.Up)


def test_symlink_indexing_error(m: Maestral) -> None:

    m.stop_sync()

    local_path = m.dropbox_path + "/link"

    os.symlink("to_nowhere", local_path)

    m.start_sync()
    wait_for_idle(m)

    assert_sync_error(m, SymlinkError, local_path, direction=SyncDirection.Up)


def test_dropbox_dir_delete_during_sync(m: Maestral) -> None:

    delete(m.dropbox_path)

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert isinstance(m.fatal_errors[0], NoDropboxDirError)


@pytest.mark.skipif(is_fs_case_sensitive(HOME), reason="file system is case sensitive")
def test_dropbox_dir_rename_during_sync(m: Maestral) -> None:

    dirname, basename = osp.split(m.dropbox_path)

    # Move the directory to a new location with a different casing.
    shutil.move(m.dropbox_path, osp.join(dirname, basename.upper()))

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert isinstance(m.fatal_errors[0], NoDropboxDirError)


def test_dropbox_dir_delete_during_pause(m: Maestral) -> None:

    m.stop_sync()

    delete(m.dropbox_path)

    m.start_sync()

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert isinstance(m.fatal_errors[0], NoDropboxDirError)


# ==== performance tests ===============================================================


def test_sync_event_conversion_performance(m: Maestral) -> None:
    """Tests the performance of converting remote file changes to SyncEvents."""

    # Create remote tree with 6 entries.
    m.stop_sync()

    tree: DirTreeType = {
        "test_folder": {
            "sub_file_1.txt": "content",
            "sub_file_2.txt": "content",
            "sub_folder_1": {},
            "sub_folder_2": {
                "sub_sub_file.txt": "content",
            },
        }
    }

    create_remote_tree(m, tree)

    res = m.client.list_folder("/", recursive=True)

    def setup():
        m.sync.reset_sync_state()
        m.sync._case_conversion_cache.clear()

    def generate_sync_events():
        cleaned_res = m.sync._clean_remote_changes(res)
        cleaned_res.entries.sort(key=lambda x: x.path_lower.count("/"))
        for md in cleaned_res.entries:
            SyncEvent.from_metadata(md, m.sync)

    n_loops = 1000  # equivalent to 5,000 items

    duration = timeit.timeit(stmt=generate_sync_events, setup=setup, number=n_loops)

    assert duration < 4  # expected ~ 1.8 sec


# ==== test recovery from inconsistent state ===========================================


def test_invalid_pending_download(m: Maestral) -> None:
    """
    Tests error handling when an invalid path is saved in the pending downloads list.
    This can happen for instance when Dropbox servers have a hickup or when our state
    file gets corrupted.
    """

    # add a non-existent path to the pending downloads list
    bogus_path = "/bogus path"
    m.manager.download_queue.put(bogus_path)

    # trigger a resync
    m.stop_sync()
    m.start_sync()
    wait_for_idle(m)

    # assert that there are no sync errors / fatal errors and that the invalid path
    # was cleared
    assert bogus_path not in m.manager.download_queue

    assert_no_errors(m)


def test_out_of_order_indexing(m: Maestral) -> None:
    """Tests applying remote events when children are synced before their parents."""

    m.stop_sync()

    # Create a nested remote folder structure.

    tree: DirTreeType = {
        "parent": {
            "child_1": {},
            "child_2": "content",
        },
    }

    create_remote_tree(m, tree)

    # Fetch remote index manually and scramble order.

    all_changes = []

    for changes, cursor in m.sync.list_remote_changes_iterator(m.sync.remote_cursor):
        all_changes += changes

    # Reverse order of changes with children coming first.
    for sync_event in reversed(all_changes):
        m.sync._create_local_entry(sync_event)

    # Check that all local items have been created.
    assert_no_errors(m)
    assert_synced(m, tree)


# ==== assert helpers ==================================================================


def assert_no_errors(m: Maestral) -> None:
    assert len(m.fatal_errors) == 0, m.fatal_errors
    assert len(m.sync_errors) == 0, m.sync_errors


def assert_sync_error(
    m: Maestral,
    err_class: type[SyncError],
    local_path: str | None = None,
    dbx_path: str | None = None,
    direction: SyncDirection | None = None,
) -> None:
    assert len(m.sync_errors) == 1

    error = m.sync_errors[0]

    assert error.type == err_class.__name__

    if local_path is not None:
        assert error.local_path == local_path

    if dbx_path is not None:
        assert error.dbx_path == dbx_path

    if direction is not None:
        assert error.direction is direction


def assert_synced(m: Maestral, tree: DirTreeType | None = None) -> None:
    """
    Asserts that the local and remote folders are synced:

    local file system state == local sync index state == tree
    """

    listing = m.client.list_folder("/", recursive=True)

    remote_items_map = {md.path_lower: md for md in listing.entries}

    # Assert that all items from server are present locally with the same content hash.
    for md0 in listing.entries:

        if m.sync.is_excluded_by_user(md0.path_lower):
            continue

        local_path = m.to_local_path(md0.path_display)

        remote_hash = md0.content_hash if isinstance(md0, FileMetadata) else "folder"
        local_hash = m.sync.get_local_hash(local_path)
        local_symlink_target = get_symlink_target(local_path)

        assert local_hash, f"'{md0.path_display}' not found locally"
        assert local_hash == remote_hash, f'different content for "{md0.path_display}"'

        if isinstance(md0, FileMetadata):
            assert (
                md0.symlink_target == local_symlink_target
            ), f'different symlink targets for "{md0.path_display}"'

    # Assert that all local items are present on server.
    for path, _ in walk(m.dropbox_path, m.sync._scandir_with_ignore):
        dbx_path_lower = m.sync.to_dbx_path_lower(path)
        assert (
            dbx_path_lower in remote_items_map
        ), f'local item "{path}" does not exist on dbx'

    # Check each item in our index is on the server.
    for entry in m.sync.get_index():

        # Check that there is a match on the server.
        md1 = remote_items_map.get(entry.dbx_path_lower)
        assert md1, f'indexed item "{entry.dbx_path_lower}" does not exist on dbx'

        remote_rev = md1.rev if isinstance(md1, FileMetadata) else "folder"

        # Check if revs are equal on server and locally.
        assert entry.rev == remote_rev, f'different revs for "{entry.dbx_path_lower}"'

        # Check if casing on drive is the same as in index.
        local_path_expected_casing = m.dropbox_path + entry.dbx_path_cased
        local_path_actual_casing = to_existing_unnormalized_path(
            local_path_expected_casing
        )

        # Allow for unicode normalisation differences on macOS since the reported local
        # normalisation may vary depending on the version of macOS and APFS. Either will
        # be accepted since they are treated as the same path by macOS file system APIs.
        if platform.system() == "Darwin":
            local_path_expected_casing = normalize_unicode(local_path_expected_casing)
            local_path_actual_casing = normalize_unicode(local_path_actual_casing)

        assert (
            local_path_expected_casing == local_path_actual_casing
        ), "casing on drive does not match index"

    # Check that each server item is in our index.
    for md2 in listing.entries:
        if not m.sync.is_excluded_by_user(md2.path_lower):
            e1 = m.sync.get_index_entry(md2.path_lower)
            assert e1, f"{md2.path_lower} missing in index"

    # Check that local state corresponds to given tree.
    if tree is not None:
        assert_local_tree(m, tree)


def assert_local_tree(m: Maestral, tree: DirTreeType) -> None:

    actual_tree = {}  # type: ignore

    for dirpath, dirnames, filenames in os.walk(m.dropbox_path):

        relative_path = dirpath.replace(m.dropbox_path + "/", "")

        # Find node in tree.
        node = actual_tree
        if dirpath != m.dropbox_path:
            for component in relative_path.split("/"):
                node = node[component]

        # Prune excluded items.
        dirnames[:] = [d for d in dirnames if not m.sync.is_excluded(d)]
        filenames[:] = [d for d in filenames if not m.sync.is_excluded(d)]

        for dirname in dirnames:
            node[dirname] = {}

        for filename in filenames:
            with open(osp.join(dirpath, filename)) as f:
                node[filename] = f.read()

    # Allow for unicode normalisation differences on macOS since the reported local
    # normalisation may vary depending on the version of macOS and APFS. Either will be
    # accepted since they are treated as the same path by macOS file system APIs.
    if platform.system() == "Darwin":
        tree = tree_normalize_unicode(tree)
        actual_tree = tree_normalize_unicode(actual_tree)

    assert tree == actual_tree


# ==== helpers =========================================================================


def create_local_tree(prefix: str, tree: DirTreeType) -> None:
    for name, content in tree.items():
        local_path = osp.join(prefix, name)

        if isinstance(content, str):
            with open(local_path, "w") as f:
                f.write(content)

        elif isinstance(content, dict):
            os.makedirs(local_path, exist_ok=True)
            create_local_tree(local_path, content)


def create_remote_tree(m: Maestral, tree: DirTreeType, prefix: str = "/") -> None:
    for name, content in tree.items():
        dbx_path = osp.join(prefix, name)

        if isinstance(content, str):
            with convert_api_errors(dbx_path=dbx_path):
                m.client.dbx.files_upload(
                    content.encode(), dbx_path, mode=files.WriteMode.overwrite
                )

        elif isinstance(content, dict):
            try:
                m.client.make_dir(dbx_path)
            except FolderConflictError:
                pass
            create_remote_tree(m, content, dbx_path)


def tree_normalize_unicode(tree: T) -> T:
    """Recursively normalize all keys to the given unicode form."""
    new_tree: dict[str, str | T] = {}

    for key, value in tree.items():
        key_norm = normalize_unicode(key)
        if isinstance(value, str):
            new_tree[key_norm] = value
        else:
            new_tree[key_norm] = tree_normalize_unicode(value)

    return cast(T, new_tree)
