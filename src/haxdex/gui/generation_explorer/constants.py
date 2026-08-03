from PyQt6.QtGui import QColor
import enum

RUN_BTN_W = 64
RUN_BTN_H = 26
MODEL_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
THUMB = 228


class GenExplorerColumns(enum.Enum):
    COL_IMAGE = 0
    COL_POSITIVE = 1
    COL_NEGATIVE = 2
    COL_WIDTH = 3
    COL_HEIGHT = 4
    COL_STEPS = 5
    COL_CFG = 6
    COL_CHECKPOINT = 7
    COL_LORAS = 8
    COL_MD5 = 9
    COL_RUN = 10


HEADERS = [
    "Image",
    "Positive",
    "Negative",
    "Width",
    "Height",
    "Steps",
    "CFG",
    "Checkpoint",
    "LoRAs",
    "MD5",
    "",
]

EDITABLE_COLS = {
    GenExplorerColumns.COL_POSITIVE.value,
    GenExplorerColumns.COL_NEGATIVE.value,
    GenExplorerColumns.COL_WIDTH.value,
    GenExplorerColumns.COL_HEIGHT.value,
    GenExplorerColumns.COL_STEPS.value,
    GenExplorerColumns.COL_CFG.value,
    GenExplorerColumns.COL_CHECKPOINT.value,
}

COLOR_WARN = QColor(255, 165, 0)
COLOR_BAD = QColor(220, 80, 80)
COLOR_OK = QColor(70, 150, 90)
COLOR_REPLACED = QColor(150, 80, 220)

LORA_ROW_H = 22
LORA_ON_W = 34
LORA_WEIGHT_W = 84

EDITOR_H = 28
MIN_W_CHECKPOINT = 260
MIN_W_LORAS = 420
MIN_W_MD5 = 220

KIND_SUBDIRS = {
    "checkpoint": ["checkpoints", "diffusion_models"],
    "lora": ["loras"],
    "vae": ["vae"],
    "clip": ["text_encoders", "clip"],
}
