
## Guidelines

To start, install maestral with the `dev` extra to get all dependencies required for
development:

```
pip3 install maestral[dev]
```

### Checking the Format, Coding Style, and Type Hints

Code is formatted with [black](https://github.com/psf/black).
Coding style is checked with [flake8](http://flake8.pycqa.org).
Type hints, [PEP484](https://www.python.org/dev/peps/pep-0484/), are checked with
[mypy](http://mypy-lang.org/).

You can check the format, coding style, and type hints at the same time by running the
provided pre-commit hook from the git directory:

```bash
pre-commit run -a
```

You can also install the provided pre-commit hook to run checks on every commit. This
will however significantly slow down commits.

### Documentation

The documentation is built using [sphinx](https://www.sphinx-doc.org/en/master/) and a
few of its extensions. It is built from the develop and master branches and hosted on
[Read The Docs](https://maestral.readthedocs.io/en/latest/). If you want to build the 
documentation locally, install maestral with the `docs` extra to get all required
dependencies:

```
pip3 install maestral[docs]
```

The API documentation is mostly based on doc strings. Inline comments should be used 
whenever code may be difficult to understand for others.

## Tests

The test suite uses a mixture of [unittest](https://docs.python.org/3.8/library/unittest.html)
and [pytest](https://pytest-cov.readthedocs.io/en/latest/), depending on what is most
convenient for the actual test and the preference of the author. Pytest should be used
as the test runner.

Test are grouped into those which require a linked Dropbox account ("linked") and those
who can run by themselves ("offline"). The former tend to be integration test while the
latter are mostly unit tests. The current focus currently lies on integration tests,
especially for the sync engine, as they are easier to maintain when the implementation
and internal APIs change. Exceptions are made for performance tests, for instance for
indexing and cleaning up sync events, and for particularly complex functions that are
prone to regressions.

The current test suite uses a Dropbox access token provided by the environment variable
`DROPBOX_TOKEN` to connect to a real account. The GitHub action which is running the
tests will set this environment variable for you with a temporary access token that
expires after 4 hours. Tests are run on `ubuntu-latest` and `macos-latest` in parallel
on different accounts and you should acquire a "lock" on the account before running
tests. Fixtures to create and clean up a test config and to acquire a lock are provided
in the `tests/linked/conftest.py`. If you run the tests locally, you will need to
provide an access token for your own Dropbox account.
