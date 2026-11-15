from beartype import beartype
from beartype.typing import Optional, Sequence, cast
from haxdex_action.action_cfg import ActionConfig
from haxdex_action.action_execute import ActionExecutor
from haxdex_action.action_list_model import ActionListModel
from haxdex_action.action_list_view import ActionListView
from haxdex_core.core_config import IndexServiceConfig
from haxdex_core.db import IndexDatabase
from haxdex_core.job_types import BaseIndexer, RunContext
from haxdex_core.types import FileHash
from haxdex_view_results.builder import WidgetBuilder
from haxdex_view_results.preview_pane import FilePreviewPane
from haxdex_view_tree.columns.file_duplicate_column import FileDuplicateColumnSpec
from haxdex_view_tree.columns.file_mime_column import FileMimeColumnSpec
from haxdex_view_tree.columns.file_name_column import FileNameColumnSpec
from haxdex_view_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex_view_tree.columns.known_actions_column import KnownActionColumnSpec
from haxdex_view_tree.columns.size_column import EntrySizeColumnSpec
from haxdex_view_tree.columns.size_share_column import SizeShareColumnSpec
from haxdex_view_tree.columns.video_bitrate_columns import VideoBitrateColumnSpec
from haxdex_view_tree.columns.video_convert_column import VideoConvertColumnSpec
from haxdex_view_tree.columns.video_framerate_column import VideoFramerateColumnSpec
from haxdex_view_tree.columns.video_resolution_column import VideoResolutionColumnSpec
from haxdex_view_tree.file_tree_config import FileTreeViewConfig
from haxdex_view_tree.model.tree_model_build import build_file_tree
from haxdex_view_tree.model.tree_model_user_edits import store_user_column_edit
from haxdex_view_tree.python_code_editor import QueryError
from haxdex_view_tree.qt_tree_model import FileTreeModel
from haxdex_view_tree.qt_tree_region import FileTreeRegion
from hstd_py_qt.file_view.column_model import AbstractColumnItemModel
from loguru import logger
from PyQt6.QtCore import QCoreApplication, QModelIndex, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.common.qt_utils import get_settings


@beartype
class FileTreeQueryCore:
    @staticmethod
    def build_reference_tree(
        ctx: RunContext,
        file_tree_cfg: FileTreeViewConfig,
        db: IndexDatabase,
        indexer_instances: Sequence[BaseIndexer],
    ) -> list[FileTreeNode] | None:
        if file_tree_cfg.drop_cache_files:
            if file_tree_cfg.reference_tree_cache_path.exists():
                file_tree_cfg.reference_tree_cache_path.unlink()

            if file_tree_cfg.visual_tree_cache_path.exists():
                file_tree_cfg.visual_tree_cache_path.unlink()

        if file_tree_cfg.reference_dir:
            return build_file_tree(
                ctx=ctx,
                db=db,
                root_directories=[file_tree_cfg.reference_dir],
                indexers=indexer_instances,
                columns=[FileDuplicateColumnSpec("file_duplicate", None)],
                cache_path=file_tree_cfg.reference_tree_cache_path,
                user_edit_path=file_tree_cfg.user_edit_path,
            )

        return None

    @staticmethod
    def build_default_columns(
        ctx: RunContext,
        file_tree_cfg: FileTreeViewConfig,
        db: IndexDatabase,
        indexer_instances: Sequence[BaseIndexer],
        action_cfg: Optional[ActionConfig] = None,
    ) -> list[FileTreeColumnSpec]:
        assert file_tree_cfg
        columns: list[FileTreeColumnSpec] = [
            FileNameColumnSpec(),
            FileMimeColumnSpec("mime"),
            EntrySizeColumnSpec("size"),
            SizeShareColumnSpec(
                "share",
                [directory.path for directory in file_tree_cfg.root_dirs],
            ),
        ]

        if file_tree_cfg.reference_dir:
            reference_tree = FileTreeQueryCore.build_reference_tree(
                ctx=ctx,
                file_tree_cfg=file_tree_cfg,
                db=db,
                indexer_instances=indexer_instances,
            )
            columns.append(
                FileDuplicateColumnSpec(
                    "file_duplicates",
                    reference_tree=reference_tree[0],
                )
            )

        columns.extend(
            [
                VideoBitrateColumnSpec("bitrate"),
                VideoResolutionColumnSpec("resolution"),
                VideoFramerateColumnSpec("framerate"),
                VideoConvertColumnSpec("convert"),
            ]
        )

        if action_cfg and action_cfg.execution.sqlite_path.exists():
            executor = ActionExecutor(action_cfg.execution)
            columns.append(KnownActionColumnSpec("actions", executor))

        return columns

    def __init__(
        self,
        ctx: RunContext,
        index_cfg: IndexServiceConfig,
        file_tree_cfg: FileTreeViewConfig,
        columns: Sequence[FileTreeColumnSpec],
        db: IndexDatabase,
        indexer_instances: Sequence[BaseIndexer],
    ) -> None:
        self.columns = columns
        self.cfg = index_cfg
        self.file_tree_cfg = file_tree_cfg

        assert file_tree_cfg

        nodes = build_file_tree(
            ctx=ctx,
            db=db,
            root_directories=file_tree_cfg.root_dirs,
            indexers=indexer_instances,
            columns=self.columns,
            cache_path=file_tree_cfg.visual_tree_cache_path,
            user_edit_path=file_tree_cfg.user_edit_path,
        )

        logger.debug(f"build file tree with {len(nodes)}")

        self.model = FileTreeModel(
            columns=self.columns,
            nodes=nodes,
        )
        self.model.dataChanged.connect(self.user_data_changed)

    def first_hash(self) -> FileHash | None:
        first_index = self.model.first_index_with_hash()
        if not first_index.isValid():
            return None
        return FileHash(hash=first_index.data(CustomModelRole.HashRole.value))

    def user_data_changed(
        self,
        topLeft: QModelIndex,
        bottomRight: QModelIndex,
        roles: list[int],
    ) -> None:
        column = self.columns[topLeft.column()]
        node = cast(FileTreeNode, topLeft.internalPointer())
        logger.info(
            f"user entered custom data for column {column.getColumnData(topLeft)} for {node.root} {node.root_relative}"
        )

        if node.root is None:
            raise ValueError(
                f"Cannot store user edit for path {node.path.as_posix()} because root name is missing"
            )

        store_user_column_edit(
            user_edit_path=self.file_tree_cfg.user_edit_path,
            root=node.root,
            relative=node.root_relative,
            column=column,
            data=node.columns[column.column_name],
        )


@beartype
class FileTreeQueryWindow(QMainWindow):
    def __init__(
        self,
        *,
        ctx: RunContext,
        db: IndexDatabase,
        index_cfg: IndexServiceConfig,
        indexer_instances: Sequence[BaseIndexer],
        builders: Sequence[WidgetBuilder],
        file_tree_cfg: FileTreeViewConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.cfg = index_cfg

        QCoreApplication.setOrganizationName("haxscramper")
        QCoreApplication.setApplicationName("haxdex-tree-view")

        self.core = FileTreeQueryCore(
            ctx=ctx,
            db=db,
            index_cfg=index_cfg,
            file_tree_cfg=file_tree_cfg,
            indexer_instances=indexer_instances,
            columns=FileTreeQueryCore.build_default_columns(
                ctx=ctx,
                db=db,
                file_tree_cfg=file_tree_cfg,
                indexer_instances=indexer_instances,
            ),
        )

        self.columns = self.core.columns
        self.regions: list[FileTreeRegion] = []
        self.region_widgets: list[QWidget] = []

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.main_splitter)

        self.region_splitter = QSplitter(
            Qt.Orientation.Horizontal,
            self.main_splitter,
        )

        self.preview_pane = FilePreviewPane(
            db=db,
            collection_names=[instance.asset_name for instance in indexer_instances],
            builders=builders,
            parent=self.main_splitter,
        )

        self.main_splitter.addWidget(self.region_splitter)
        self.main_splitter.addWidget(self.preview_pane)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 1)

        self.add_region(self.core.model)

        self.restore_ui_state()
        first_hash = self.core.first_hash()
        if first_hash is not None:
            self.preview_pane.show_hash(first_hash, None)

    def refresh_named_queries(self) -> None:
        for region in self.regions:
            region.refresh_named_queries()

    def add_action_region(self, actions: ActionListModel) -> ActionListView:
        logger.info("add action list view")
        view = ActionListView(
            actions,
            parent=self.region_splitter,
            action_file=self.cfg.action_file,
        )
        view.file_hash_activated.connect(self.preview_pane.show_hash)
        self.region_splitter.addWidget(view)
        self.region_widgets.append(view)
        return view

    def add_region(self, model: AbstractColumnItemModel) -> FileTreeRegion:
        region = FileTreeRegion(
            model=model,
            columns=self.columns,
            parent=self.region_splitter,
            region_id=f"region_{len(self.regions)}",
        )

        region.query_submitted.connect(self.on_query_submitted)
        region.named_queries_changed.connect(self.refresh_named_queries)
        region.file_hash_activated.connect(self.preview_pane.show_hash)

        self.region_splitter.addWidget(region)
        self.regions.append(region)
        self.region_widgets.append(region)
        return region

    def on_query_submitted(self, source_region: FileTreeRegion) -> None:
        try:
            result = source_region.compute_filtered()
            logger.info("compute filtered OK")
        except QueryError as error:
            source_region.queryEdit.show_query_error(error)
            return
        except Exception as error:
            logger.exception("query failed")
            QMessageBox.warning(self, "Query error", str(error))
            return

        source_index = self.region_widgets.index(source_region)
        while source_index + 1 < len(self.region_widgets):
            stale = self.region_widgets.pop()
            if isinstance(stale, FileTreeRegion):
                self.regions.remove(stale)
            stale.setParent(None)
            stale.deleteLater()

        if result.is_action:
            self.add_action_region(result.model)

        else:
            self.add_region(result.model)

    def restore_ui_state(self) -> None:
        settings = get_settings()

        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        window_state = settings.value("window/state")
        if window_state is not None:
            self.restoreState(window_state)

        tree_header_state = settings.value("tree/headerState")
        if tree_header_state is not None and self.regions:
            self.regions[0].modelViewer.treeViewer.treeView.header().restoreState(
                tree_header_state
            )

        table_header_state = settings.value("table/headerState")
        if table_header_state is not None and self.regions:
            self.regions[
                0
            ].modelViewer.tableViewer.tableView.horizontalHeader().restoreState(
                table_header_state
            )

        main_splitter_state = settings.value("splitter/main")
        if (
            main_splitter_state is not None
            and not self.main_splitter.restoreState(main_splitter_state)
            or main_splitter_state is None
        ):
            self.main_splitter.setSizes([800, 400])

        region_splitter_state = settings.value("splitter/region")
        if region_splitter_state is not None:
            self.region_splitter.restoreState(region_splitter_state)

    def save_ui_state(self) -> None:
        settings = get_settings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/state", self.saveState())
        settings.setValue("splitter/main", self.main_splitter.saveState())
        settings.setValue("splitter/region", self.region_splitter.saveState())

        if self.regions:
            settings.setValue(
                "tree/headerState",
                self.regions[0].modelViewer.treeViewer.treeView.header().saveState(),
            )
            settings.setValue(
                "table/headerState",
                self.regions[0]
                .modelViewer.tableViewer.tableView.horizontalHeader()
                .saveState(),
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_ui_state()
        super().closeEvent(event)
