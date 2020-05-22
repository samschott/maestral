# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import tempfile
from maestral.utils.path import (
    path_exists_case_insensitive, cased_path_candidates, to_cased_path,
    is_fs_case_sensitive, is_child, delete
)
from maestral.utils.appdirs import get_home_dir


def test_path_exists_case_insensitive():
    # choose a path which exists on all Unix systems
    path = '/usr/local/share'

    assert to_cased_path(path) == path
    assert to_cased_path(path.title()) == path
    assert to_cased_path(path.upper()) == path

    # choose a random path that likely does not exist
    path = '/usr/local/share/test_folder/path_928'
    if not osp.exists(path):
        assert not path_exists_case_insensitive(path)

    # choose a random parent that likely does not exist
    path = '/test_folder/path_928'
    root = '/usr'
    if not osp.exists(root):
        assert not path_exists_case_insensitive(path, root)


def test_cased_path_candidates():

    # choose a path which exists on all Unix systems
    path = '/usr/local/share'.upper()

    assert len(cased_path_candidates(path)) == 1
    assert '/usr/local/share' in cased_path_candidates(path)

    home = get_home_dir()

    if is_fs_case_sensitive(home):

        parent0 = osp.join(home, 'test folder/subfolder')
        parent1 = osp.join(home, 'Test Folder/subfolder')

        os.makedirs(parent0)
        os.makedirs(parent1)

        path = osp.join(parent0.lower(), 'File.txt')

        try:
            candidates = cased_path_candidates(path)

            assert len(candidates) == 2
            assert osp.join(parent0, 'File.txt') in candidates
            assert osp.join(parent1, 'File.txt') in candidates

            candidates = cased_path_candidates('/test folder/subfolder/File.txt',
                                               root=home)

            assert len(candidates) == 2
            assert osp.join(parent0, 'File.txt') in candidates
            assert osp.join(parent1, 'File.txt') in candidates

        finally:
            delete(parent0)
            delete(parent1)


def test_is_child():
    assert is_child('/parent/path/child', '/parent/path/')
    assert is_child('/parent/path/child/', '/parent/path')
    assert not is_child('/parent/path', '/parent/path')
    assert not is_child('/path1', '/path2')


def test_delete():
    # test deleting file
    test_file = tempfile.NamedTemporaryFile()
    assert osp.isfile(test_file.name)
    delete(test_file.name)
    assert not osp.exists(test_file.name)

    # test deleting directory
    test_dir = tempfile.TemporaryDirectory()
    assert osp.isdir(test_dir.name)
    delete(test_dir.name)
    assert not osp.exists(test_dir.name)
