# -*- coding: utf-8 -*-

# -- Path setup --------------------------------------------------------------

import os
import sys
import time

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('..'))
sys.path.insert(0, os.path.abspath('../maestral'))

from maestral import __author__, __version__  # noqa: E402


# -- Project information -----------------------------------------------------

project = 'Maestral'
title = 'Maestral API Documentation'
copyright = '{}, {}'.format(time.localtime().tm_year, __author__)
author = __author__
version = __version__
release = __version__

# -- General configuration ---------------------------------------------------

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.coverage',
    'sphinx.ext.mathjax',
    'sphinx.ext.ifconfig',
    'sphinx.ext.viewcode',
    'sphinx.ext.githubpages',
    'sphinx_autodoc_typehints',
    'sphinx_click.ext',
    'm2r',
]

templates_path = ['_templates']

source_suffix = ['.rst', '.md']
master_doc = 'index'
language = None
pygments_style = None

# -- Options for HTML output -------------------------------------------------

html_theme = 'sphinx_rtd_theme'

html_logo = '../maestral/resources/maestral.png'

html_context = {
    'css_files': [
        'https://media.readthedocs.org/css/sphinx_rtd_theme.css',
        'https://media.readthedocs.org/css/readthedocs-doc-embed.css',
        '_static/custom.css',
    ],
}

# -- Options for LaTeX output ------------------------------------------------

latex_documents = [(master_doc, 'maestral.tex', title, author, 'manual'), ]

# -- Extension configuration -------------------------------------------------

autodoc_member_order = 'bysource'
# set_type_checking_flag = True

# -- Options for intersphinx extension ---------------------------------------

intersphinx_mapping = {'https://docs.python.org/': None}

# -- Options for todo extension ----------------------------------------------

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = True
