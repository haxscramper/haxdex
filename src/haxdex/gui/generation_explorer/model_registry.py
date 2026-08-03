from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from beartype import beartype
from beartype.typing import Any
from PyQt6.QtGui import QColor

from .constants import COLOR_BAD, COLOR_REPLACED, COLOR_WARN, KIND_SUBDIRS, MODEL_EXTS
from .models import ExplorerSettings, ModelEntry


@beartype
def strip_ext(name: str) -> str:
    if name == "":
        return name
    path = Path(name)
    if path.suffix.lower() in MODEL_EXTS:
        return path.stem
    return name


@beartype
def scan_models(comfy_dir: Path, subdir: str) -> list[tuple[str, str, str]]:
    base = comfy_dir / "models" / subdir
    output: list[tuple[str, str, str]] = []
    if base.exists():
        for file_path in base.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in MODEL_EXTS:
                continue
            relative = file_path.relative_to(base).with_suffix("").as_posix()
            payload = file_path.stem
            arch_key = f"{subdir}/{relative}"
            output.append((relative, payload, arch_key))
    return output


@beartype
@dataclass
class ModelRegistry:
    comfy_dir: Path
    arch_info: dict[str, str | None]
    settings: ExplorerSettings

    def __post_init__(self) -> None:
        self.model_replacements = {
            self.norm_key(key): (self.normalize_payload(value) or value.strip())
            for key, value in self.settings.model_replacements.items()
        }
        self.arch_type_map = dict(self.settings.arch_type_map)
        self.model_arch_map = {
            self.norm_key(key): value
            for key, value in self.settings.model_arch_map.items()
        }
        self.replacement_overrides = {
            self.norm_key(key): value
            for key, value in self.settings.replacement_overrides.items()
        }

        self.entries_by_kind: dict[str, list[ModelEntry]] = {}
        self.payload_sets_by_kind: dict[str, set[str]] = {}
        self.display_to_payload_by_kind: dict[str, dict[str, str]] = {}
        self.payload_to_entry_by_kind: dict[str, dict[str, ModelEntry]] = {}

        for kind, subdirs in KIND_SUBDIRS.items():
            entries: list[ModelEntry] = []
            for subdir in subdirs:
                for relative, payload, arch_key in scan_models(self.comfy_dir, subdir):
                    arch = self.arch_info.get(arch_key) or self.model_arch_map.get(
                        payload)
                    model_type = self.arch_type_map.get(
                        arch) if arch is not None else None
                    entries.append(
                        ModelEntry(
                            display=relative,
                            payload=payload,
                            arch=arch,
                            model_type=model_type,
                        ))
            entries.sort(key=lambda item: item.display.casefold())
            self.entries_by_kind[kind] = entries
            self.payload_sets_by_kind[kind] = {item.payload for item in entries}
            self.display_to_payload_by_kind[kind] = {
                item.display: item.payload for item in entries
            }
            self.payload_to_entry_by_kind[kind] = {item.payload: item for item in entries}

        self.default_vae = {
            key: self.resolve_payload(value, "vae")
            for key, value in self.settings.default_vae.items()
        }
        self.default_clip = {
            key: self.resolve_payload(value, "clip")
            for key, value in self.settings.default_clip.items()
        }

    def norm_key(self, value: str) -> str:
        if value == "":
            return ""
        normalized = self.normalize_payload(value)
        return normalized or value.strip()

    def options(self, kind: str) -> list[tuple[str, str]]:
        return [(entry.display, entry.payload) for entry in self.entries_by_kind[kind]]

    def normalize_payload(self, value: str) -> str:
        if value == "":
            return ""
        stripped = strip_ext(value.strip())
        return Path(stripped).name

    def resolve_display_to_payload(self, value: str, kind: str) -> str:
        text = value.strip()
        if text == "":
            return ""
        mapping = self.display_to_payload_by_kind[kind]
        if text in mapping:
            return mapping[text]
        return self.normalize_payload(text)

    def resolve_with_replacement(self, value: str, kind: str) -> tuple[str, bool]:
        raw = value.strip()
        payload = self.resolve_display_to_payload(raw, kind)
        if payload != "" and payload in self.payload_sets_by_kind[kind]:
            return payload, False
        replacement = self.model_replacements.get(raw) or self.model_replacements.get(
            payload)
        if replacement is not None and replacement != "":
            replacement_payload = self.resolve_display_to_payload(
                replacement, kind) or self.normalize_payload(replacement)
            return replacement_payload, True
        return payload, False

    def resolve_payload(self, value: str, kind: str) -> str:
        payload, replaced = self.resolve_with_replacement(value, kind)
        return payload

    def display_for_payload(self, payload: str, kind: str) -> str:
        entry = self.payload_to_entry_by_kind[kind].get(payload)
        if entry is None:
            return payload
        return entry.display

    def type_for_payload(self, payload: str, kind: str) -> str | None:
        entry = self.payload_to_entry_by_kind[kind].get(payload)
        if entry is None:
            return None
        return entry.model_type

    def is_known_payload(self, payload: str, kind: str) -> bool:
        return payload in self.payload_sets_by_kind[kind]

    def color_for(self, value: str, kind: str) -> QColor | None:
        payload, replaced = self.resolve_with_replacement(value, kind)
        if payload == "":
            return None
        if replaced:
            return COLOR_REPLACED
        if payload in self.payload_sets_by_kind[kind]:
            return None
        best = max(
            (SequenceMatcher(None, payload, candidate).ratio()
             for candidate in self.payload_sets_by_kind[kind]),
            default=0.0,
        )
        if 0.7 <= best:
            return COLOR_WARN
        return COLOR_BAD

    def has(self, value: str, kind: str) -> bool:
        payload, replaced = self.resolve_with_replacement(value, kind)
        return payload in self.payload_sets_by_kind[kind]
