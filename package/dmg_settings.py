# -*- coding: utf-8 -*-
import plistlib
import os.path

# ---- Basics ----------------------------------------------------------------------------

# Volume format (see hdiutil create -help)
format = "UDBZ"

# Volume size
size = None

# Files to include
application = "Maestral.app"
files = [application]

# Symlinks to create
symlinks = {"Applications": "/Applications"}

# Where to put the icons
icon_locations = {os.path.basename(application): (75, 75), "Applications": (225, 75)}

# ---- Window configuration --------------------------------------------------------------

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 180

# Window position in ((x, y), (w, h)) format
window_rect = ((600, 600), (350, 150))
default_view = "icon-view"

# General view configuration
show_icon_preview = False

# Set these to True to force inclusion of icon/list view settings (otherwise
# we only include settings for the default view)
include_icon_view_settings = "auto"
include_list_view_settings = "auto"

# --- Icon view configuration ------------------------------------------------------------

arrange_by = None
grid_offset = (0, 0)
grid_spacing = 100
scroll_position = (0, 0)
label_pos = "bottom"
text_size = 12
icon_size = 64
