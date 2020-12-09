# -*- coding: utf-8 -*-

import os

import pytest

from .conftest import resources


if not os.environ.get("DROPBOX_TOKEN"):
    pytest.skip("Requires auth token", allow_module_level=True)


# Client API unit tests


def test_upload_large_file(m):
    large_file = resources + "/large-file.pdf"
    md = m.client.upload(
        large_file, "/sync_tests/large-file.pdf", chunk_size=5 * 10 ** 5
    )

    assert md.content_hash == m.sync.get_local_hash(large_file)
