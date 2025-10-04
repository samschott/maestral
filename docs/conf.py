import os
import sys
import time

# -- Path setup ------------------------------------------------------------------------

sys.path.insert(0, os.path.abspath("../src"))

# -- Project information ---------------------------------------------------------------

author = "Sam Schott"
version = "1.9.5"
release = version
project = "Maestral"
title = "Maestral API Documentation"
copyright = f"{time.localtime().tm_year}, {author}"

# -- General configuration -------------------------------------------------------------

extensions = [
    "sphinx.ext.viewcode",
    "sphinxext.opengraph",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx.ext.intersphinx",
    "autoapi.extension",
    "sphinx_mdinclude",
]
source_suffix = [".rst", ".md"]
master_doc = "index"
language = "en"
# html4_writer = True

# -- Options for HTML output -----------------------------------------------------------

html_theme = "furo"
html_logo = "maestral.png"
html_static_path = ['_static']
html_css_files = [
    'css/custom.css',
]

# -- Options for LaTeX output ----------------------------------------------------------

latex_documents = [
    (master_doc, "maestral.tex", title, author, "manual"),
]

# -- Extension configuration -----------------------------------------------------------

# sphinx.ext.autodoc
autodoc_typehints = "description"
autoclass_content = "both"
autodoc_member_order = "bysource"
autodoc_inherit_docstrings = False

# autoapi.extension
autoapi_type = "python"
autoapi_dirs = ["../src/maestral"]
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
]
autoapi_add_toctree_entry = False

# sphinx.ext.todo
todo_include_todos = True

# sphinx.ext.intersphsinx
intersphinx_mapping = {
    "click": ("https://click.palletsprojects.com/en/latest/", None),
    "dropbox": ("https://dropbox-sdk-python.readthedocs.io/en/latest/", None),
    "fasteners": ("https://fasteners.readthedocs.io/en/latest/", None),
    "keyring": ("https://keyring.readthedocs.io/en/latest/", None),
    "Pyro5": ("https://pyro5.readthedocs.io/en/latest/", None),
    "python": ("https://docs.python.org/3/", None),
    "requests": ("https://docs.python-requests.org/en/master/", None),
    "watchdog": ("https://python-watchdog.readthedocs.io/en/latest/", None),
}
