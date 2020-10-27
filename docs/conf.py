# -*- coding: utf-8 -*-

# -- Path setup ------------------------------------------------------------------------

import os
import sys
import time

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../src"))
sys.path.insert(0, os.path.abspath("../src/maestral"))

# -- Project information ---------------------------------------------------------------

author = "Sam Schott"
version = "1.2.2.dev0"
release = version
project = "Maestral"
title = "Maestral API Documentation"
copyright = "{}, {}".format(time.localtime().tm_year, author)

# -- General configuration -------------------------------------------------------------

extensions = [
    "sphinx.ext.napoleon",  # support numpy style docstrings in config module
    "sphinx.ext.todo",  # parse todo list
    "sphinx.ext.intersphinx",  # support for if-clauses in docs
    "sphinx.ext.ifconfig",  # support for linking between documentations
    "autoapi.extension",  # builds API docs from doc strings without importing module
    "sphinx_click.ext",  # support for click commands
    "m2r",  # convert markdown to rest
]
source_suffix = [".rst", ".md"]
master_doc = "index"
language = "en"
# html4_writer = True

# -- Options for HTML output -----------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_logo = "../src/maestral/resources/maestral.png"
html_context = {
    "css_files": [
        "https://media.readthedocs.org/css/sphinx_rtd_theme.css",
        "https://media.readthedocs.org/css/readthedocs-doc-embed.css",
        "_static/custom.css",
    ],
}

# -- Options for LaTeX output ----------------------------------------------------------

latex_documents = [
    (master_doc, "maestral.tex", title, author, "manual"),
]

# -- Extension configuration -----------------------------------------------------------

# autoapi
autoapi_type = "python"
autoapi_dirs = ["../src/maestral"]
autoapi_options = [
    "members",
    "inherited-members",
    "special-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]
autoapi_add_toctree_entry = False

# todo list support
todo_include_todos = True
