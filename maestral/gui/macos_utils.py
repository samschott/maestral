# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import enum

from PyQt5 import QtCore, QtWidgets
from AppKit import *
import objc

from maestral.gui.resources import APP_ICON_PATH


# -- vibrant window widget ---------------------------------------------------------------

def icon_from_path(icon_path):
    return NSImage.alloc().initWithContentsOfFile_(str(icon_path))


def nsview_from_qwidget(widget):
    return objc.objc_object(c_void_p=widget.winId().__int__())


def nswindow_from_qwidget(widget):
    return nsview_from_qwidget(widget).window()


class Materials(enum.Enum):
    Titlebar = NSVisualEffectMaterialTitlebar  # The material for a window’s titlebar
    Menu = NSVisualEffectMaterialMenu  # The material for menus.
    Popover = NSVisualEffectMaterialPopover  # The material for the background of popover windows
    Sidebar = NSVisualEffectMaterialSidebar  # The material for the background of window sidebars
    HeaderView = NSVisualEffectMaterialHeaderView  # The material for in-line header or footer views
    Sheet = NSVisualEffectMaterialSheet  # The material for the background of sheet windows
    WindowBackground = NSVisualEffectMaterialWindowBackground  # The material for the background of opaque windows
    HUDWindow = NSVisualEffectMaterialHUDWindow,  # The material for the background of heads-up display (HUD) windows
    FullScreenUI = NSVisualEffectMaterialFullScreenUI  # The material for the background of a full-screen modal interface
    ToolTip = NSVisualEffectMaterialToolTip  # The material for the background of a tool tip
    ContentBackground = NSVisualEffectMaterialContentBackground  # The material for the background of opaque content
    UnderWindowBackground = NSVisualEffectMaterialUnderWindowBackground  # The material for under a window's background
    UnderPageBackground = NSVisualEffectMaterialUnderPageBackground  # The material for the area behind the pages of a document
    UltraDark = NSVisualEffectMaterialUltraDark  # Deprecated: use target instead


class Appearances(enum.Enum):
    DarkAqua = NSAppearanceNameDarkAqua  # The standard dark system appearance
    VibrantLight = NSAppearanceNameVibrantLight  # A light vibrant appearance, available only in visual effect views
    VibrantDark = NSAppearanceNameVibrantDark  # A dark vibrant appearance, available only in visual effect views.
    HighContrastAqua = NSAppearanceNameAccessibilityHighContrastAqua  # A high-contrast version of the standard light system appearance. 
    HighContrastDarkAqua = NSAppearanceNameAccessibilityHighContrastDarkAqua  # A high-contrast version of the standard dark system appearance. 
    HighContrastVibrantLight = NSAppearanceNameAccessibilityHighContrastVibrantLight  # A high-contrast version of the light vibrant appearance. 
    HighContrastVibrantDark = NSAppearanceNameAccessibilityHighContrastVibrantDark  # A high-contrast version of the dark vibrant appearance.


class VibrantWidget(QtWidgets.QWidget):

    _material = Materials.WindowBackground
    _appearance = None

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=None)

        frame = NSMakeRect(0, 0, self.width(), self.height())
        view = nsview_from_qwidget(self)

        self._visualEffectView = NSVisualEffectView.new()
        self._visualEffectView.setAutoresizingMask_(NSViewWidthSizable|NSViewHeightSizable)
        self._visualEffectView.setWantsLayer_(True)
        self._visualEffectView.setFrame_(frame)
        self._visualEffectView.setState_(NSVisualEffectStateActive)
        self._visualEffectView.setMaterial_(self._material.value)
        self._visualEffectView.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)

        self._native_window = view.window()
        content = self._native_window.contentView()

        container = QtWidgets.QMacCocoaViewContainer(0, self)
        content.addSubview_positioned_relativeTo_(self._visualEffectView, NSWindowBelow, container)

        self._native_window.setTitlebarAppearsTransparent_(True)
        self._native_window.setStyleMask_(self._native_window.styleMask() | NSFullSizeContentViewWindowMask)

    def setMaterial(self, material):
        if not material in Materials:
            raise ValueError('Invalid material')
        self._material = material
        self._visualEffectView.setMaterial_(self._material.value)

    def material(self):
        return self._material

    # this is required to maintain the appearance

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def close(self):
        self.hide()


# -- native AppKit sheet alerts ----------------------------------------------------------

# `window` should be a QWidget with a corresponding window
# `icon_path` should be the path to a resource

def native_dialog_sheet(window, title, message, details=None, callback=print, icon_path=APP_ICON_PATH,
                        button_names=('Ok',), checkbox_text=None, level="info"):
    alert = NSAlert.alloc().init()
    if icon_path:
        alert.setIcon_(icon_from_path(icon_path))
    if level == "info":
        alert.setAlertStyle_(NSInformationalAlertStyle)
    elif level == "warning":
        alert.setAlertStyle_(NSWarningAlertStyle)
    elif level == "error":
        alert.setAlertStyle_(NSCriticalAlertStyle)
    alert.setMessageText_(title)
    alert.setInformativeText_(message)

    if checkbox_text:
        alert.setShowsSuppressionButton_(True)
        alert.suppressionButton().setTitle_(checkbox_text)

    if details:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 500, 200))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(False)
        scroll.setBorderType_(NSBezelBorder)

        trace = NSTextView.alloc().init()
        trace.insertText_(details)
        trace.setEditable_(False)
        trace.setVerticallyResizable_(True)
        trace.setHorizontallyResizable_(True)

        scroll.setDocumentView_(trace)
        alert.setAccessoryView_(scroll)

    for name in button_names:
        alert.addButtonWithTitle_(name)

    def completionHandler(r: int) -> None:
        callback(r == NSAlertFirstButtonReturn)

    alert.beginSheetModalForWindow_completionHandler_(nswindow_from_qwidget(window), completionHandler)


# -- native AppKit dialogs ---------------------------------------------------------------

def native_dialog(title, message, details=None, icon_path=APP_ICON_PATH, button_names=('Ok',), checkbox_text=None, level="info"):
    alert = NSAlert.alloc().init()
    if icon_path:
        alert.setIcon_(icon_from_path(icon_path))
    if level == "info":
        alert.setAlertStyle_(NSInformationalAlertStyle)
    elif level == "warning":
        alert.setAlertStyle_(NSWarningAlertStyle)
    elif level == "error":
        alert.setAlertStyle_(NSCriticalAlertStyle)
    alert.setMessageText_(title)
    alert.setInformativeText_(message)

    if details:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 500, 200))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(False)
        scroll.setBorderType_(NSBezelBorder)

        trace = NSTextView.alloc().init()
        trace.insertText_(details)
        trace.setEditable_(False)
        trace.setVerticallyResizable_(True)
        trace.setHorizontallyResizable_(True)

        scroll.setDocumentView_(trace)
        alert.setAccessoryView_(scroll)

    if checkbox_text:
        alert.setShowsSuppressionButton_(True)
        alert.suppressionButton().setTitle_(checkbox_text)

    for name in button_names:
        alert.addButtonWithTitle_(name)

    result = alert.runModal()

    if checkbox_text:
        return result == NSAlertFirstButtonReturn, alert.suppressionButton().state() == NSOnState
    else:
        return result == NSAlertFirstButtonReturn
