import os.path as osp

import pytest

from maestral.utils.path import (
    normalized_path_exists,
    get_existing_equivalent_paths,
    is_fs_case_sensitive,
    is_child,
)
from maestral.utils.appdirs import get_home_dir


def test_normalized_path_exists(tmp_path):

    # Assert that an existing path is found, even when a different casing is used.

    path = str(tmp_path)

    assert normalized_path_exists(path)
    assert normalized_path_exists(path.title())
    assert normalized_path_exists(path.upper())

    # Assert that a non-existent path is identified.
    path = str(tmp_path / "path_928")
    assert not normalized_path_exists(path)

    # Assert that specifying a non-existing root returns False.
    child_path = str(tmp_path / "path_928" / "content")
    assert not normalized_path_exists(child_path, root=path)


def test_cased_path_candidates(tmp_path):

    # Test that we can find a unique correctly cased path
    # starting from a candidate with scrambled casing.

    path = str(tmp_path)

    candidates = get_existing_equivalent_paths(path.upper())

    assert candidates == [path]

    candidates = get_existing_equivalent_paths("/test", root=path)

    assert len(candidates) == 1
    assert f"{path}/test" in candidates


@pytest.mark.skipif(
    not is_fs_case_sensitive(get_home_dir()),
    reason="requires case-sensitive file system",
)
def test_multiple_cased_path_candidates(tmp_path):

    # test that we can get multiple cased path
    # candidates on case-sensitive file systems

    # create two folders that differ only in casing

    dir0 = tmp_path / "test folder/subfolder"
    dir1 = tmp_path / "Test Folder/subfolder"

    dir0.mkdir(parents=True, exist_ok=True)
    dir1.mkdir(parents=True, exist_ok=True)

    dir0 = str(dir0)
    dir1 = str(dir1)

    # scramble the casing and check if we can find matches
    path = osp.join(dir0.lower(), "File.txt")

    # find matches for original path itself
    candidates = get_existing_equivalent_paths(path)

    assert len(candidates) == 2
    assert osp.join(dir0, "File.txt") in candidates
    assert osp.join(dir1, "File.txt") in candidates

    # find matches for children
    candidates = get_existing_equivalent_paths(
        "/test folder/subfolder/File.txt", root=str(tmp_path)
    )

    assert len(candidates) == 2
    assert osp.join(dir0, "File.txt") in candidates
    assert osp.join(dir1, "File.txt") in candidates


def test_is_child():
    assert is_child("/parent/path/child", "/parent/path/")
    assert is_child("/parent/path/child/", "/parent/path")
    assert not is_child("/parent/path", "/parent/path")
    assert not is_child("/path1", "/path2")
