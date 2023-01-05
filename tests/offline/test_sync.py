from datetime import datetime
from queue import Queue

from maestral.sync import ActivityTree, ActivityNode
from maestral.models import SyncEvent, SyncDirection, SyncStatus, ChangeType, ItemType


EVENT1 = SyncEvent(
    dbx_path="/d0/file1.txt",
    direction=SyncDirection.Up,
    status=SyncStatus.Syncing,
    local_path="/d0/file1.txt",
    dbx_path_lower="/d0/file1.txt",
    change_type=ChangeType.Added,
    completed=0,
    size=10,
    item_type=ItemType.File,
    sync_time=datetime.today(),
)
EVENT2 = SyncEvent(
    dbx_path="/d0/file2.txt",
    direction=SyncDirection.Up,
    status=SyncStatus.Syncing,
    local_path="/d0/file2.txt",
    dbx_path_lower="/d0/file2.txt",
    change_type=ChangeType.Added,
    completed=0,
    size=10,
    item_type=ItemType.File,
    sync_time=datetime.today(),
)
EVENT2_FAILED = SyncEvent(
    dbx_path="/d0/file2.txt",
    direction=SyncDirection.Up,
    status=SyncStatus.Failed,
    local_path="/d0/file2.txt",
    dbx_path_lower="/d0/file2.txt",
    change_type=ChangeType.Added,
    completed=0,
    size=10,
    item_type=ItemType.File,
    sync_time=datetime.today(),
)


def test_activity_tree_add_single() -> None:
    tree = ActivityTree()
    tree.add(EVENT1)

    assert_in_tree(tree, EVENT1)
    assert_tree_integrity(tree)


def test_activity_tree_add_multiple() -> None:
    tree = ActivityTree()
    tree.add(EVENT1)
    tree.add(EVENT2)

    assert_in_tree(tree, EVENT1)
    assert_in_tree(tree, EVENT2)

    assert_tree_integrity(tree)


def test_activity_tree_remove_single() -> None:
    tree = ActivityTree()
    tree.add(EVENT1)
    tree.remove(EVENT1)

    assert EVENT1 not in tree.sync_events
    assert len(tree.children) == 0
    assert not tree.has_path(EVENT1.dbx_path)

    assert_tree_integrity(tree)


def test_activity_tree_remove_multiple() -> None:
    tree = ActivityTree()
    tree.add(EVENT1)
    tree.add(EVENT2)

    tree.remove(EVENT1)

    assert_in_tree(tree, EVENT2)
    assert_not_in_tree(tree, EVENT1)

    tree.remove(EVENT2)

    assert_not_in_tree(tree, EVENT2)
    assert len(tree.children) == 0

    assert_tree_integrity(tree)


def test_activity_tree_failed_replaced() -> None:
    tree = ActivityTree()
    tree.add(EVENT1)
    tree.add(EVENT2_FAILED)

    assert_in_tree(tree, EVENT1)
    assert_in_tree(tree, EVENT2_FAILED)

    tree.add(EVENT2)

    assert_in_tree(tree, EVENT2)
    assert_not_in_tree(tree, EVENT2_FAILED)

    assert_tree_integrity(tree)


def assert_in_tree(tree: ActivityTree, event: SyncEvent) -> None:
    assert event in tree.sync_events
    assert tree.has_path(event.dbx_path)

    parts = event.dbx_path.lstrip("/").split("/")

    node = tree

    for part in parts:
        assert event in node.children[part].sync_events
        node = node.children[part]

    assert tree.get_node(event.dbx_path) is node


def assert_not_in_tree(tree: ActivityTree, event: SyncEvent) -> None:
    # Traverse tree, ensure that the event is not attached to any node.
    queue = Queue()
    queue.put(tree)

    while not queue.empty():
        node = queue.get()
        assert event not in node.sync_events

        for child in node.children.values():
            queue.put(child)


def assert_tree_integrity(node: ActivityNode) -> None:
    if node.parent:
        # The parent node should contain all of this node's sync events.
        assert node.sync_events.issubset(node.parent.sync_events)
        # Empty nodes with parent should be removed (apart from the root node).
        assert len(node.sync_events) > 0, node

    # Recurse.
    for child in node.children.values():
        assert_tree_integrity(child)
