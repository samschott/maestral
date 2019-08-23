#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import logging

# external packages
from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtCore import QAbstractItemModel, QModelIndex, Qt, QVariant

# maestral modules
from maestral.sync.main import handle_disconnect, is_child
from maestral.gui.resources import FOLDERS_DIALOG_PATH, get_native_folder_icon
from maestral.gui.utils import MaestralBackgroundTask
from maestral.config.main import CONF

logger = logging.getLogger(__name__)


class TreeModel(QAbstractItemModel):

    def __init__(self, root, parent=None):
        super(TreeModel, self).__init__(parent)
        self._root_item = root
        self._root_item.done_loading.connect(self.reloadData)
        self._header = self._root_item.header()

    def reloadData(self, roles=None):

        if not roles:
            roles = [Qt.DisplayRole]

        self.dataChanged.emit(QModelIndex(), QModelIndex(), roles)
        self.layoutChanged.emit()

    def flags(self, index):
        flags = super().flags(index) | Qt.ItemIsUserCheckable
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
            return get_native_folder_icon()
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
        parent_item = child_item.parent()
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

    done_loading = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent=parent)
        self._children = []
        self._parent = parent
        self._children_update_started = False

        if self._parent:
            self.done_loading.connect(self._parent.done_loading)

    def header(self):
        # subclass this
        raise NotImplementedError(self.header)

    def column_count(self):
        # subclass this
        raise NotImplementedError(self.column_count)

    def parent(self):
        return self._parent

    def _async_done_loading(self, result):
        # subclass this to set the children, depending on the `result` of the async´ call
        self.done_loading.emit()

    def _create_children_async(self):
        pass

    def row(self):
        if self._parent:
            return self._parent._children.index(self)
        return 0

    def children_(self):
        if not self._children and not self._children_update_started:
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


class DropboxPathModel(AbstractTreeItem):

    def __init__(self, async_loader, root="/", parent=None):
        AbstractTreeItem.__init__(self, parent=parent)
        self._root = root
        self._async_loader = async_loader

        self._checkStateChanged = False

        # get info from our own excluded list
        excluded_folders = CONF.get("main", "excluded_folders")
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
        self._remote.sig_done.connect(self._async_done_loading)

    def _async_done_loading(self, result):
        for folder in result:
            self._children.append(self.__class__(self._async_loader, folder, self))
        self.done_loading.emit()

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


class AsyncLoad(QtCore.QObject):

    def __init__(self, m, parent=None):
        super(self.__class__, self).__init__(parent=parent)

        self.m = m

    def loadFolders(self, path):

        new_job = MaestralBackgroundTask(
            parent=self,
            target=self._loadFolders,
            args=(path, )
        )

        return new_job

    def _loadFolders(self, path):

        path = "" if path == "/" else path
        entries = self.m.list_folder(path, recursive=False)

        if not entries:
            folders = []
        else:
            folders = [os.path.join(path, e["path_display"]) for e in entries
                       if e["type"] == "FolderMetadata"]
        print("Loaded folders inside %s" % path)
        return folders


class FoldersDialog(QtWidgets.QDialog):

    def __init__(self, mdbx,  parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(FOLDERS_DIALOG_PATH, self)
        self.setModal(True)

        self.mdbx = mdbx
        self.accept_button = self.buttonBox.buttons()[0]
        self.accept_button.setText('Update')

        # connect callbacks
        self.buttonBox.accepted.connect(self.on_accepted)
        self.selectAllCheckBox.clicked.connect(self.on_select_all_clicked)

    @handle_disconnect
    def populate_folders_list(self, overload=None):
        self.async_loader = AsyncLoad(self.mdbx, self)
        self.dbx_root = DropboxPathModel(self.async_loader, "/")
        self.dbx_model = TreeModel(self.dbx_root)
        self.treeViewFolders.clicked.connect(self.update_select_all_checkbox)
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
            return

        self.apply_selection()

    def apply_selection(self, index=QModelIndex()):

        if index.isValid():
            item = index.internalPointer()
            item_dbx_path = item._root.lower()

            # Include items which have been checked.
            # Remove items which have been unchecked.
            # Do not touch items which are partially checked.
            if not item.isOriginalState():
                if item.checkState == 0:
                    logger.debug("Excluding: %s" % item_dbx_path)
                    self.mdbx.exclude_folder(item_dbx_path)
                elif item.checkState == 1:
                    pass
                elif item.checkState == 2:
                    logger.debug("Including: %s" % item_dbx_path)
                    self.mdbx.include_folder(item_dbx_path)
        else:
            item = self.dbx_model._root_item

        for row in range(item.child_count_loaded()):
            index_child = self.dbx_model.index(row, 0, index)
            self.apply_selection(index=index_child)

    def changeEvent(self, QEvent):

        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
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
