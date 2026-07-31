from pathlib import Path

from PyQt6.QtCore import QModelIndex

from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode
from haxdex.gui.file_tree.columns.size_column import EntrySizeColumnSpec
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.services.indexers.file_size import FileSizeIndexer
from tests.utils import init_index_service, init_file_tree_config, sub_row_by_name, init_file_tree_columns


def test_e2e_tree_build(stable_test_dir: Path):
    index = init_index_service(stable_test_dir)

    dir = index.root_dirs[0]
    dir.joinpath("a.txt").write_text("random_content")
    dir.joinpath("b.txt").write_text("whatever")

    sub = dir / "sub"
    sub.mkdir(exist_ok=True, parents=True)
    sub.joinpath("sub_a.txt").write_text("nested")
    sub.joinpath("sub_b.txt").write_text("whatever 1123")

    index.service.run_index()

    tree_config = init_file_tree_config(index)

    assert tree_config.cfg.file_tree_view
    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=init_file_tree_columns(index),
    )

    assert core.model.rowCount() == 1
    root_index: QModelIndex = core.model.index(0, 0, QModelIndex())
    root_node: FileTreeNode = root_index.internalPointer()
    assert root_node
    assert root_node.root == "data"
    assert root_node.root_relative == ""
    assert str(root_node.path).endswith("data")

    a_txt_index = sub_row_by_name(root_index, core.model, "a.txt")
    b_txt_index = sub_row_by_name(root_index, core.model, "b.txt")
    sub_index = sub_row_by_name(root_index, core.model, "sub")

    assert a_txt_index.isValid()
    assert b_txt_index.isValid()
    assert sub_index.isValid()

    a_txt_node: FileTreeNode = a_txt_index.internalPointer()
    b_txt_node: FileTreeNode = b_txt_index.internalPointer()
    sub_node: FileTreeNode = sub_index.internalPointer()

    assert sub_node.is_directory
    assert not a_txt_node.is_directory
    assert not b_txt_node.is_directory

    assert EntrySizeColumnSpec.column_name in a_txt_node.columns
    assert EntrySizeColumnSpec.column_name in b_txt_node.columns
    assert EntrySizeColumnSpec.column_name in sub_node.columns
