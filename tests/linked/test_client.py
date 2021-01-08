# -*- coding: utf-8 -*-

import os

import pytest
from dropbox.files import FolderMetadata

from maestral.errors import NotFoundError, PathError

from .conftest import resources


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


# Client API unit tests: we explicitly test those method calls which are not covered
# by current integration tests, either because they are not used by the sync module or
# because niche cases require additional testing.


def test_upload_large_file(m):
    # not tested during during integration tests

    large_file = resources + "/large-file.pdf"
    md = m.client.upload(
        large_file, "/sync_tests/large-file.pdf", chunk_size=5 * 10 ** 5
    )

    assert md.content_hash == m.sync.get_local_hash(large_file)


@pytest.mark.parametrize("batch_size", [10, 30])
@pytest.mark.parametrize("force_async", [True, False])
def test_batch_methods(m, batch_size, force_async):
    # batch methods are not currently used by sync module

    folders = [f"/sync_tests/folder {i}" for i in range(20)]

    # create some test directories
    res = m.client.make_dir_batch(folders + ["/invalid\\"], force_async=force_async)

    for i in range(20):
        assert isinstance(res[i], FolderMetadata)
        assert res[i].path_lower == folders[i]

    assert isinstance(res[20], PathError)

    # remove them again
    res = m.client.remove_batch(
        [(folder, None) for folder in folders] + [("/not_a_folder", None)],
        batch_size=batch_size,
    )

    for i in range(20):
        assert isinstance(res[i], FolderMetadata)
        assert res[i].path_lower == folders[i]

    assert isinstance(res[20], NotFoundError)
