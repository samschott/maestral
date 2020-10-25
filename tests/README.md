
The test suite uses a mixture of [unittest](https://docs.python.org/3.8/library/unittest.html)
and [pytest](https://pytest-cov.readthedocs.io/en/latest/), depending on what is most
convenient for the actual test. Tests are run with pytest.

Some commands require a linked Dropbox account for proper testing. The current test suite
uses a Dropbox access token provided by the environment variable `DROPBOX_TOKEN` to link
to a real account. The github action which is running the tests will set this environment
variable for you with a temporary access token that remains valid for 4 hours. If you run
the tests locally, you will need to provide a token for your own Dropbox account.

The current focus lies on integration tests, especially for the sync engine, as they are
easier maintain as the implementation and internal APIs change. Exceptions are made for
performance tests, for instance for indexing and cleaning up sync events, and for
particularly complex functions that are prone to regressions.
