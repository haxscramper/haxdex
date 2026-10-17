import json
import random
import urllib.request

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ....haxdex_read_media.src.haxdex_read_media.indexers.image_generation import (
    GenerationParamsIndexer,
)
from ....haxdex_read_media.src.haxdex_read_media.indexers.safetensor_indexer import (
    SafetensorIndexer,
)
from ....haxdex_read_media.src.haxdex_read_media.indexers.wd_indexer import (
    WdTagIndexer,
)
from ....src.haxdex.services.core.db import IndexDatabase
from .constants import (
    EDITOR_H,
    MIN_W_CHECKPOINT,
    MIN_W_LORAS,
    MIN_W_MD5,
    THUMB,
    GenExplorerColumns,
)
from .delegates import (
    ButtonDelegate,
    ImageDelegate,
    ImagePreviewDialog,
    LoraDelegate,
    ModelComboDelegate,
    NumberDelegate,
    PromptDelegate,
)
from .model_registry import ModelRegistry
from .models import ExplorerSettings
from .repository import ExplorerRepository
from .table_model import GenerationTableModel, RunFriendlyTableView


class GenerationExplorerWindow(QWidget):
    def __init__(
        self,
        settings: ExplorerSettings,
        index_database: IndexDatabase,
        generation_params_indexer: GenerationParamsIndexer,
        safetensor_indexer: SafetensorIndexer,
        wd_tag_indexer: WdTagIndexer,
    ):
        super().__init__()
        self.settings = settings
        self.index_database = index_database
        self.generation_params_indexer = generation_params_indexer
        self.safetensor_indexer = safetensor_indexer
        self.wd_tag_indexer = wd_tag_indexer

        repository = ExplorerRepository(
            index_database=self.index_database,
            generation_params_indexer=self.generation_params_indexer,
            safetensor_indexer=self.safetensor_indexer,
            wd_tag_indexer=self.wd_tag_indexer,
        )

        indexed_files = repository.load_indexed_files()
        rows = repository.build_generation_rows(indexed_files)
        arch_info = repository.build_arch_info(indexed_files)
        registry = ModelRegistry(self.settings.comfy_dir, arch_info, self.settings)

        self.setWindowTitle("Generation Params Explorer")
        self.registry = registry
        self.model = GenerationTableModel(rows, self.registry)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(GenExplorerColumns.COL_POSITIVE.value)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by positive prompt…")
        self.search.returnPressed.connect(self.apply_filter)

        apply_button = QPushButton("Apply filter")
        apply_button.setFixedHeight(EDITOR_H)
        apply_button.clicked.connect(self.apply_filter)

        search_row.addWidget(self.search)
        search_row.addWidget(apply_button)
        layout.addLayout(search_row)

        self.view = RunFriendlyTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setEditTriggers(
            QTableView.EditTrigger.DoubleClicked | QTableView.EditTrigger.EditKeyPressed
        )
        self.view.setWordWrap(True)
        self.view.verticalHeader().setDefaultSectionSize(THUMB + 8)
        self.view.doubleClicked.connect(self.on_double_clicked)
        layout.addWidget(self.view)

        self.setup_delegates()

        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)

        self.view.setColumnWidth(GenExplorerColumns.COL_IMAGE.value, THUMB + 12)
        self.view.setColumnWidth(GenExplorerColumns.COL_RUN.value, 64 + 16)

        for column in [
            GenExplorerColumns.COL_WIDTH.value,
            GenExplorerColumns.COL_HEIGHT.value,
            GenExplorerColumns.COL_STEPS.value,
            GenExplorerColumns.COL_CFG.value,
            GenExplorerColumns.COL_CHECKPOINT.value,
            GenExplorerColumns.COL_LORAS.value,
        ]:
            self.view.resizeColumnToContents(column)

        self.view.setColumnWidth(
            GenExplorerColumns.COL_POSITIVE.value,
            max(self.view.columnWidth(GenExplorerColumns.COL_POSITIVE.value), 320),
        )
        self.view.setColumnWidth(
            GenExplorerColumns.COL_NEGATIVE.value,
            max(self.view.columnWidth(GenExplorerColumns.COL_NEGATIVE.value), 320),
        )
        self.view.setColumnWidth(
            GenExplorerColumns.COL_CHECKPOINT.value,
            max(
                self.view.columnWidth(GenExplorerColumns.COL_CHECKPOINT.value),
                MIN_W_CHECKPOINT,
            ),
        )
        self.view.setColumnWidth(
            GenExplorerColumns.COL_LORAS.value,
            max(self.view.columnWidth(GenExplorerColumns.COL_LORAS.value), MIN_W_LORAS),
        )
        self.view.setColumnWidth(GenExplorerColumns.COL_MD5.value, MIN_W_MD5)

        self.resize(1400, 800)

    def apply_filter(self) -> None:
        self.proxy.setFilterFixedString(self.search.text())

    def setup_delegates(self) -> None:
        image_delegate = ImageDelegate(self.view)
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_IMAGE.value, image_delegate
        )

        checkpoint_delegate = ModelComboDelegate(
            self.registry.options("checkpoint"),
            allow_empty=False,
            registry=self.registry,
            kind="checkpoint",
            filter_by_type=True,
            parent=self,
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_CHECKPOINT.value, checkpoint_delegate
        )

        lora_delegate = LoraDelegate(self.registry, self)
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_LORAS.value, lora_delegate
        )

        run_delegate = ButtonDelegate(self.view, self)
        run_delegate.clicked.connect(self.on_run)
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_RUN.value, run_delegate
        )

        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_WIDTH.value, NumberDelegate(False, self)
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_HEIGHT.value, NumberDelegate(False, self)
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_STEPS.value, NumberDelegate(False, self)
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_CFG.value, NumberDelegate(True, self)
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_POSITIVE.value, PromptDelegate(self)
        )
        self.view.setItemDelegateForColumn(
            GenExplorerColumns.COL_NEGATIVE.value, PromptDelegate(self)
        )

    def on_double_clicked(self, proxy_index: QModelIndex) -> None:
        if proxy_index.column() != GenExplorerColumns.COL_IMAGE.value:
            return
        source = self.proxy.mapToSource(proxy_index)
        path = self.model.index(source.row(), GenExplorerColumns.COL_IMAGE.value).data(
            Qt.ItemDataRole.DisplayRole
        )
        if not path:
            return
        dialog = ImagePreviewDialog(path, self)
        dialog.exec()

    def on_run(self, proxy_index: QModelIndex) -> None:
        self.view.commit_active_editor()
        source = self.proxy.mapToSource(proxy_index)
        row = self.model.rows[source.row()]

        payload, replaced = self.registry.resolve_with_replacement(
            row.checkpoint, "checkpoint"
        )
        if replaced and payload in self.registry.replacement_overrides:
            override = self.registry.replacement_overrides[payload]
            if "steps" in override:
                index = self.model.index(
                    source.row(), GenExplorerColumns.COL_STEPS.value
                )
                self.model.setData(
                    index, int(override["steps"]), Qt.ItemDataRole.EditRole
                )
            if "cfg" in override:
                index = self.model.index(source.row(), GenExplorerColumns.COL_CFG.value)
                self.model.setData(
                    index, float(override["cfg"]), Qt.ItemDataRole.EditRole
                )

        self.send_to_comfy(source.row())

    def send_to_comfy(self, row_index: int) -> None:
        row = self.model.rows[row_index]
        checkpoint_payload, replaced = self.registry.resolve_with_replacement(
            row.checkpoint, "checkpoint"
        )
        checkpoint_type = self.registry.type_for_payload(
            checkpoint_payload, "checkpoint"
        )

        vae_payload, replaced = self.registry.resolve_with_replacement(
            row.vae or "", "vae"
        )
        if (
            vae_payload == ""
            and checkpoint_type is not None
            and checkpoint_type in self.registry.default_vae
        ):
            vae_payload = self.registry.default_vae[checkpoint_type]

        clip_payload = ""
        clip_value = row.clip or ""
        if clip_value != "":
            clip_payload, replaced = self.registry.resolve_with_replacement(
                clip_value, "clip"
            )
        if (
            clip_payload == ""
            and checkpoint_type is not None
            and checkpoint_type in self.registry.default_clip
        ):
            clip_payload = self.registry.default_clip[checkpoint_type]

        missing: list[str] = []
        if checkpoint_payload != "" and not self.registry.has(
            row.checkpoint, "checkpoint"
        ):
            missing.append(f"checkpoint: {row.checkpoint}")

        normalized_loras: list[dict[str, float | str]] = []
        for lora in row.loras:
            if not lora.enabled:
                continue
            payload_name, replaced = self.registry.resolve_with_replacement(
                lora.model, "lora"
            )
            if payload_name == "":
                continue
            if not self.registry.has(lora.model, "lora"):
                missing.append(f"lora: {lora.model}")
            normalized_loras.append(
                {"model": payload_name, "weight": float(lora.weight)}
            )

        if vae_payload != "" and not self.registry.is_known_payload(vae_payload, "vae"):
            missing.append(f"vae: {row.vae or vae_payload}")

        if clip_payload != "" and not self.registry.is_known_payload(
            clip_payload, "clip"
        ):
            missing.append(f"clip: {row.clip or clip_payload}")

        if missing != []:
            QMessageBox.critical(
                self,
                "Missing models",
                "These models are not present in the ComfyUI directory:\n\n"
                + "\n".join(missing),
            )
            return

        result_payload = {
            "positive": row.positive,
            "negative": row.negative,
            "width": row.width,
            "height": row.height,
            "sampler": "",
            "scheduler": "",
            "steps": row.steps,
            "cfg": row.cfg,
            "checkpoint": checkpoint_payload,
            "loras": normalized_loras,
            "vae": vae_payload or None,
            "clip": clip_payload or None,
            "seed": random.randint(0, 2**32 - 1),
        }

        payload = {
            "md5": row.md5,
            "indexer_id": row.indexer_id,
            "result": result_payload,
        }

        with open(self.settings.workflow_path, "r", encoding="utf-8") as handle:
            workflow = json.load(handle)

        target_node = None
        for node in workflow.values():
            if node.get("_meta", {}).get("title") == "input_json_parameter":
                target_node = node
                break

        if target_node is None:
            raise RuntimeError(
                f"Could not find node titled input_json_parameter in workflow {self.settings.workflow_path}"
            )

        target_node["inputs"]["value"] = json.dumps(payload, indent=2)

        request = urllib.request.Request(
            f"{self.settings.comfy_url}/prompt",
            data=json.dumps({"prompt": workflow}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request):
            pass
