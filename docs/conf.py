# -*- coding: utf-8 -*-

import os
import sys
import time

# -- Path setup ------------------------------------------------------------------------

sys.path.insert(0, os.path.abspath("../src"))

# -- Project information ---------------------------------------------------------------

author = "Sam Schott"
version = "1.4.1.dev0"
release = version
project = "Maestral"
title = "Maestral API Documentation"
copyright = "{}, {}".format(time.localtime().tm_year, author)

# -- General configuration -------------------------------------------------------------

extensions = [
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx.ext.intersphinx",
    "sphinx.ext.autodoc",
    "autoapi.extension",
    "m2r2",
]
source_suffix = [".rst", ".md"]
master_doc = "index"
language = "en"
# html4_writer = True

# -- Options for HTML output -----------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_logo = "maestral-symbolic.svg"

# -- Options for LaTeX output ----------------------------------------------------------

latex_documents = [
    (master_doc, "maestral.tex", title, author, "manual"),
]

# -- Extension configuration -----------------------------------------------------------

# sphinx.ext.autodoc
autodoc_typehints = "description"

# autoapi.extension
autoapi_type = "python"
autoapi_dirs = ["../src/maestral"]
autoapi_options = [
    "members",
    "show-inheritance",
    "undoc-members",
]
autoapi_add_toctree_entry = False

# sphinx.ext.todo
todo_include_todos = True

# sphinx.ext.intersphinx
intersphinx_mapping = {
    "click": ("https://click.palletsprojects.com/en/master/", None),
    "dropbox": ("https://dropbox-sdk-python.readthedocs.io/en/latest/", None),
    "fasteners": ("https://fasteners.readthedocs.io/en/latest/", None),
    "Pyro5": ("https://pyro5.readthedocs.io/en/latest/", None),
    "python": ("https://docs.python.org/3/", None),
    "requests": ("https://requests.readthedocs.io/en/master/", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/latest/", None),
    "watchdog": ("https://python-watchdog.readthedocs.io/en/latest/", None),
}
