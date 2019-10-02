#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import logging
import threading

# external packages
import Pyro4
from PyQt5 import QtCore, QtWidgets, QtGui, uic
from PyQt5.QtCore import QAbstractItemModel, QModelIndex, Qt, QVariant

# maestral modules
from maestral.sync.main import handle_disconnect, is_child
from maestral.gui.resources import FOLDERS_DIALOG_PATH, get_native_folder_icon
from maestral.gui.utils import BackgroundTask

logger = logging.getLogger(__name__)


class TreeModel(QAbstractItemModel):
    """A QAbstractItemModel which loads items and their children on-demand and
    asynchronously. It is useful for displaying a item hierarchy from a source which is
    slow to load (remote server, slow file system, etc)."""

    loading_failed = QtCore.pyqtSignal()
    loading_done = QtCore.pyqtSignal()

    def __init__(self, root, parent=None):
        super(TreeModel, self).__init__(parent)
        self._root_item = root
        self.display_message("Loading your folders...")
        self._root_item.loading_done.connect(self.reloadData)
        self._root_item.loading_failed.connect(self.on_loading_failed)
        self._header = self._root_item.header()
        self._flags = Qt.ItemIsUserCheckable

    def on_loading_failed(self):

        self.display_message("Could not connect to Dropbox. Please check "
                             "your internet connection.")

    def display_message(self, message):

        self._root_item._children = [MessageTreeItem(self._root_item, message=message)]

        self.loading_failed.emit()
        self.modelReset.emit()

    def reloadData(self, roles=None):

        if not roles:
            roles = [Qt.DisplayRole]

        self.dataChanged.emit(QModelIndex(), QModelIndex(), roles)
        self.layoutChanged.emit()
        self.loading_done.emit()

    def flags(self, index):
        flags = super().flags(index) | self._flags
        return flags

    def columnCount(self, parent=None):
        if parent and parent.isValid():
            return parent.internalPointer().column_count()
        else:
            return len(self._header)

    def checkState(self, index):
        if not index.isValid():
            return QVariant()
        item = index.internalPointer()
        return item.checkState

    def setCheckState(self, index, value):
        if index.isValid():
            item = index.internalPointer()
            item.checkState = value
            self.dataChanged.emit(index, index)
            self.layoutChanged.emit()
            return True
        return False

    def setData(self, index, value, role):
        if role == Qt.CheckStateRole and index.column() == 0:
            self.setCheckState(index, value)
            return True

        return super().setData(index, value, role)

    def data(self, index, role):
        if not index.isValid():
            return QVariant()
        item = index.internalPointer()
        if role == Qt.DisplayRole:
            return item.data(index.column())
        if role == Qt.CheckStateRole:
            return item.checkState
        if role == Qt.DecorationRole:
            return item.icon
        return QVariant()

    def headerData(self, column, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            try:
                return QVariant(self._header[column])
            except IndexError:
                pass
        return QVariant()

    def index(self, row, column, parent):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            parent_item = self._root_item
        else:
            parent_item = parent.internalPointer()
        child_item = parent_item.child_at(row)
        if child_item:
            return self.createIndex(row, column, child_item)
        else:
            return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        child_item = index.internalPointer()
        if not child_item:
            return QModelIndex()
        parent_item = child_item.parent_()
        if parent_item == self._root_item:
            return QModelIndex()
        return self.createIndex(parent_item.row(), 0, parent_item)

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            parent_item = self._root_item
        else:
            parent_item = parent.internalPointer()
        return parent_item.child_count()


class AbstractTreeItem(QtCore.QObject):
    """An abstract item for `TreeModel`. To be subclassed depending on the application."""

    loading_done = QtCore.pyqtSignal()
    loading_failed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent=parent)
        self._children = []
        self._parent = parent
        self._children_update_started = False

        if self._parent:
            self.loading_done.connect(self._parent.loading_done)
            self.loading_failed.connect(self._parent.loading_failed)

        self.icon = QtGui.QIcon()
        self._checkState = 0

    @property
    def checkState(self):
        return self._checkState

    @checkState.setter
    def checkState(self, state):
        self._checkState = state

    def header(self):
        # subclass this
        raise NotImplementedError(self.header)

    def column_count(self):
        # subclass this
        raise NotImplementedError(self.column_count)

    def parent_(self):
        return self._parent

    def _async_loading_done(self, result):
        # subclass this to set the children, depending on the `result` of the async call
        # self.loading_done.emit()
        # self.loading_failed.emit()
        raise NotImplementedError(self._async_loading_done)

    def _create_children_async(self):
        raise NotImplementedError(self._create_children_async)

    def row(self):
        if self._parent:
            return self._parent._children.index(self)
        return 0

    def children_(self):
        if not self._children_update_started:
            self._create_children_async()
            self._children_update_started = True
        return self._children

    def child_at(self, row):
        return self.children_()[row]

    def data(self, column):
        # subclass this
        raise NotImplementedError(self.data)

    def child_count(self):
        return len(self.children_())

    def child_count_loaded(self):
        return len(self._children)


class MessageTreeItem(AbstractTreeItem):
    """A tree item to display a message instead of contents."""

    def __init__(self, parent=None, message=""):
        AbstractTreeItem.__init__(self, parent=parent)
        self._parent = parent
        self._message = message
        self._checkState = QVariant()

    def _async_loading_done(self, result):
        pass

    def _create_children_async(self):
        pass

    def child_at(self, row):
        return QVariant()

    def data(self, column):
        return self._message

    def header(self):
        return ["name"]

    def column_count(self):
        return 1


class DropboxPathModel(AbstractTreeItem):
    """A Dropbox folder item. It lists its children asynchronously, only when asked to by
    `TreeModel`."""

    def __init__(self, mdbx, async_loader, root="/", parent=None):
        AbstractTreeItem.__init__(self, parent=parent)
        self.icon = get_native_folder_icon()
        self._root = root
        self._mdbx = mdbx
        self._async_loader = async_loader

        self._checkStateChanged = False

        # get info from our own excluded list
        excluded_folders = self._mdbx.get_conf("main", "excluded_folders")
        if root.lower() in excluded_folders:
            # item is excluded
            self._originalCheckState = 0
        elif any(is_child(root.lower(), f) for f in excluded_folders):
            # item's parent is excluded
            self._originalCheckState = 0
        elif any(is_child(f, root.lower()) for f in excluded_folders):
            # some of item's children are excluded
            self._originalCheckState = 1
        else:
            # item is fully included
            self._originalCheckState = 2

        # overwrite original state if the parent was modified
        if self._parent and self._parent._checkStateChanged and not \
                self._parent.checkState == 1:
            # inherit from parent
            self._checkState = self._parent.checkState
            self._checkStateChanged = self._parent._checkStateChanged
        else:
            self._checkStateChanged = False
            self._checkState = int(self._originalCheckState)

    def _create_children_async(self):
        self._remote = self._async_loader.loadFolders(self._root)
        self._remote.sig_done.connect(self._async_loading_done)

    def _async_loading_done(self, result):
        if result is False:
            self.loading_failed.emit()
        else:
            self._children = [self.__class__(self._mdbx, self._async_loader, folder, self)
                              for folder in result]
            self.loading_done.emit()

    def data(self, column):
        return os.path.basename(self._root)

    def header(self):
        return ["name"]

    def column_count(self):
        return 1

    @property
    def checkState(self):
        return self._checkState

    @checkState.setter
    def checkState(self, state):
        self._checkStateChanged = True
        self._checkState = state

        self._checkStatePropagateToChildren(state)
        self._checkStatePropagateToParent(state)

    def _checkStatePropagateToChildren(self, state):

        # propagate to children if checked or unchecked
        if state in (0, 2) and self.child_count_loaded() > 0:
            for child in self.children_():
                child._checkStateChanged = True
                child._checkState = state
                child._checkStatePropagateToChildren(state)

    def _checkStatePropagateToParent(self, state):
        # propagate to parent if checked or unchecked
        if self._parent:
            self._parent._checkStateChanged = True
            # get minimum of all other children's check state
            checkstate_other_children = min(c.checkState for c in self._parent._children)
            # set parent's state to that minimum, if it >= 1 (there always could be
            # included files)
            new_parent_state = max([checkstate_other_children, 1])
            self._parent._checkState = new_parent_state
            # tell the parent to propagate its own state upwards
            self._parent._checkStatePropagateToParent(state)

    @property
    def checkStateChanged(self):
        return self._checkStateChanged

    def isOriginalState(self):
        return self._checkState == self._originalCheckState


class AsyncLoadFolders(QtCore.QObject):

    _lock = threading.BoundedSemaphore(10)  # do not list more than 10 folders in parallel

    def __init__(self, m, parent=None):
        """
        A helper which creates instances of :class:`BackgroundTask` to
        asynchronously list Dropbox folders

        :param Maestral m: Instance of :class:`maestral.sync.main.Maestral`.
        :param parent: QObject. Defaults to None.
        """
        super(self.__class__, self).__init__(parent=parent)
        self.m = m

    def loadFolders(self, path):
        """
        Returns a running instance of :class:`maestral.gui.utils.BackgroundTask` which
        will emit `sig_done` once it has a result.
        :param str path: Dropbox path to list.
        :returns: Running background task.
        :rtype: :class:`maestral.gui.utils.BackgroundTask`
        """

        new_job = BackgroundTask(
            parent=self,
            target=self._loadFolders,
            args=(path, )
        )

        return new_job

    def _loadFolders(self, path):
        """The actual function which does the listing. Returns a list of Dropbox folder
        paths or ``False`` if the listing fails."""

        with self._lock:

            path = "" if path == "/" else path

            if isinstance(self.m, Pyro4.Proxy):
                # use a duplicate proxy to prevent blocking of the main connection
                with Pyro4.Proxy(self.m._pyroUri) as m:
                    entries = m.list_folder(path, recursive=False)
            else:
                entries = self.m.list_folder(path, recursive=False)

            if entries is False:
                folders = False
            else:
                folders = [os.path.join(path, e["path_display"]) for e in entries
                           if e["type"] == "FolderMetadata"]
            return folders


class FoldersDialog(QtWidgets.QDialog):

    def __init__(self, mdbx,  parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(FOLDERS_DIALOG_PATH, self)
        self.setModal(True)

        self.mdbx = mdbx
        self.dbx_model = None
        self.accept_button = self.buttonBox.buttons()[0]
        self.accept_button.setText("Update")

        self.ui_failed()

        # connect callbacks
        self.buttonBox.accepted.connect(self.on_accepted)
        self.selectAllCheckBox.clicked.connect(self.on_select_all_clicked)

    @handle_disconnect
    def populate_folders_list(self, overload=None):
        self.excluded_folders = self.mdbx.excluded_folders
        self.async_loader = AsyncLoadFolders(self.mdbx, self)
        self.dbx_root = DropboxPathModel(self.mdbx, self.async_loader, "/")
        self.dbx_model = TreeModel(self.dbx_root)
        self.dbx_model.loading_done.connect(self.ui_loaded)
        self.dbx_model.loading_failed.connect(self.ui_failed)
        self.dbx_model.dataChanged.connect(self.update_select_all_checkbox)
        self.treeViewFolders.setModel(self.dbx_model)

    def update_select_all_checkbox(self):
        check_states = []
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            check_states.append(self.dbx_model.data(index, Qt.CheckStateRole))
        if all(cs == 2 for cs in check_states):
            self.selectAllCheckBox.setChecked(True)
        else:
            self.selectAllCheckBox.setChecked(False)

    def on_select_all_clicked(self, checked):
        checked_state = 2 if checked else 0
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            self.dbx_model.setCheckState(index, checked_state)

    @handle_disconnect
    def on_accepted(self, overload=None):
        """
        Apply changes to local Dropbox folder.
        """

        if not self.mdbx.connected:
            self.dbx_model.on_loading_failed()
            return

        self.apply_selection()
        self.mdbx.set_excluded_folders(self.excluded_folders)

    def apply_selection(self, index=QModelIndex()):

        if index.isValid():
            item = index.internalPointer()
            item_dbx_path = item._root.lower()

            # Include items which have been checked / partially checked.
            # Remove items which have been unchecked.
            # The list will be cleaned up later.
            if item.checkState == 0:
                logger.debug("Excluding: %s" % item_dbx_path)
                self.excluded_folders.append(item_dbx_path)
            elif item.checkState in (1, 2):
                logger.debug("Including: %s" % item_dbx_path)
                self.excluded_folders = [f for f in self.excluded_folders
                                         if not f == item_dbx_path]
        else:
            item = self.dbx_model._root_item

        for row in range(item.child_count_loaded()):
            index_child = self.dbx_model.index(row, 0, index)
            self.apply_selection(index=index_child)

    def ui_failed(self):
        self.accept_button.setEnabled(False)
        self.selectAllCheckBox.setEnabled(False)

    def ui_loaded(self):
        self.accept_button.setEnabled(True)
        self.selectAllCheckBox.setEnabled(True)

    def changeEvent(self, QEvent):

        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
        if self.dbx_model:
            self.dbx_model.reloadData([Qt.DecorationRole])  # reload folder icons


if __name__ == "__main__":

    from maestral.sync.main import Maestral
    mdbx = Maestral(run=False)

    app = QtWidgets.QApplication(["test"])
    app.setAttribute(Qt.AA_UseHighDpiPixmaps)
    fd = FoldersDialog(mdbx)
    fd.show()
    fd.populate_folders_list()
    app.exec_()
