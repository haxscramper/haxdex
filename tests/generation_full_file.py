from __future__ import annotations

import base64
import hashlib
import random
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path

from beartype.typing import Literal, Sequence, Union
from PIL import Image, ImageDraw, PngImagePlugin
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

_MIME_SUFFIXES: dict[str, str] = {
    "image/png": ".png",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "text/plain": ".txt",
    "text/org": ".org",
    "text/markdown": ".md",
}


class PdfRenderKind(str, Enum):
    selectable_text = "selectable_text"
    raster_scan = "raster_scan"


class ImageKind(str, Enum):
    monotone = "monotone"
    geometric = "geometric"
    hf_image = "hf_image"


class SpokenLanguage(str, Enum):
    english = "english"
    non_english = "non_english"
    none = "none"


class FileSpecBase(BaseModel):
    mime_type: str
    empty_content: bool = False


class PdfSpec(FileSpecBase):
    mime_type: Literal["application/pdf"] = "application/pdf"
    page_count: int
    render_kind: PdfRenderKind
    include_headers: bool
    include_images: bool
    selectable_text_blocks: int


class PngSpec(FileSpecBase):
    mime_type: Literal["image/png"] = "image/png"
    kind: ImageKind
    width: int
    height: int
    include_text: bool
    text_value: str
    color_rgb: tuple[int, int, int]
    exif_metadata: dict[str, str]
    hf_repo_id: str | None = None
    hf_file_name: str | None = None


class AudioSpec(FileSpecBase):
    mime_type: Literal["audio/mpeg"] = "audio/mpeg"
    duration_seconds: float
    bitrate_kbps: int
    spoken_language: SpokenLanguage
    spoken_text: str


class VideoSpec(FileSpecBase):
    mime_type: Literal["video/mp4"] = "video/mp4"
    width: int
    height: int
    duration_seconds: float
    framerate: int
    bitrate_kbps: int
    has_audio: bool
    spoken_language: SpokenLanguage
    spoken_text: str


class TextSpec(FileSpecBase):
    mime_type: Literal["text/plain"] = "text/plain"
    random_base64: bool
    word_count: int


class MarkdownSpec(FileSpecBase):
    mime_type: Literal["text/markdown"] = "text/markdown"
    section_count: int
    include_code_blocks: bool
    include_lists: bool
    total_words: int


class OrgSpec(FileSpecBase):
    mime_type: Literal["text/org"] = "text/org"
    section_count: int
    include_src_blocks: bool
    include_lists: bool
    total_words: int


FileSpec = Union[PdfSpec, PngSpec, AudioSpec, VideoSpec, TextSpec, MarkdownSpec, OrgSpec]


class CorpusFileEntry(BaseModel):
    seed: int
    content_seed: int
    relative_path: Path
    file_spec: FileSpec = Field(discriminator="mime_type")


class CorpusManifest(BaseModel):
    version: int
    entries: list[CorpusFileEntry]


_WORDS: tuple[str, ...] = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
    "vector",
    "matrix",
    "tensor",
    "model",
    "signal",
    "sample",
    "index",
    "search",
    "token",
    "parse",
    "render",
    "packet",
    "stream",
    "frame",
    "batch",
    "query",
    "asset",
    "result",
    "schema",
    "field",
)


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _deterministic_words(seed: int, count: int) -> str:
    rng = random.Random(seed)
    tokens = [rng.choice(_WORDS) for _ in range(count)]
    return " ".join(tokens)


def _seed_to_content_seed(seed: int) -> int:
    return seed - (seed % 3)


def _seed_to_relative_path(seed: int, mime_type: str) -> Path:
    digest = hashlib.sha256(f"{seed}:{mime_type}".encode("utf-8")).hexdigest()
    return Path(digest[0:2], digest[2:4], f"{digest[4:12]}{_MIME_SUFFIXES[mime_type]}")


def _seed_to_file_spec(
    seed: int,
    mime_type: str,
    allow_empty: bool,
) -> FileSpec:
    rng = random.Random(seed)
    empty_content = allow_empty and seed % 11 == 0

    match mime_type:
        case "application/pdf":
            page_count = rng.randint(1, 10)
            render_kind = PdfRenderKind.selectable_text
            if seed % 2 == 0:
                render_kind = PdfRenderKind.raster_scan
            return PdfSpec(
                page_count=page_count,
                render_kind=render_kind,
                include_headers=seed % 2 == 1,
                include_images=seed % 3 == 0,
                selectable_text_blocks=rng.randint(1, 8),
                empty_content=empty_content,
            )

        case "image/png":
            kind = ImageKind.monotone
            if seed % 3 == 1:
                kind = ImageKind.geometric
            if seed % 3 == 2:
                kind = ImageKind.hf_image
            width = rng.choice([128, 256, 384, 512, 768, 1024])
            height = rng.choice([128, 256, 384, 512, 768, 1024])
            rgb = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
            hf_repo_id = None
            hf_file_name = None
            if kind == ImageKind.hf_image:
                hf_repo_id = "huggingface/documentation-images"
                hf_file_name = "coco_sample.png"
            return PngSpec(
                kind=kind,
                width=width,
                height=height,
                include_text=seed % 2 == 0,
                text_value=_deterministic_words(seed + 17, 8),
                color_rgb=rgb,
                exif_metadata={
                    "seed": str(seed),
                    "content_seed": str(_seed_to_content_seed(seed)),
                    "generator": "haxdex-corpus",
                },
                hf_repo_id=hf_repo_id,
                hf_file_name=hf_file_name,
                empty_content=empty_content,
            )

        case "audio/mpeg":
            duration = float(random.Random(seed + 1).randint(1, 25))
            bitrate = random.Random(seed + 2).choice([64, 96, 128])
            spoken_language = SpokenLanguage.none
            spoken_text = ""
            if seed % 2 == 0:
                spoken_language = SpokenLanguage.english
                spoken_text = _deterministic_words(seed + 41, 24)
            if seed % 5 == 0:
                spoken_language = SpokenLanguage.non_english
                spoken_text = "hola mundo prueba de voz sintetica"
            return AudioSpec(
                duration_seconds=duration,
                bitrate_kbps=bitrate,
                spoken_language=spoken_language,
                spoken_text=spoken_text,
                empty_content=empty_content,
            )

        case "video/mp4":
            width = random.Random(seed + 3).choice([320, 480, 640, 854])
            height = random.Random(seed + 4).choice([240, 360, 480])
            duration = float(random.Random(seed + 5).randint(2, 18))
            framerate = random.Random(seed + 6).choice([12, 15, 24, 30])
            bitrate = random.Random(seed + 7).choice([300, 500, 800, 1200])
            has_audio = seed % 2 == 0
            spoken_language = SpokenLanguage.none
            spoken_text = ""
            if has_audio and seed % 4 == 0:
                spoken_language = SpokenLanguage.english
                spoken_text = _deterministic_words(seed + 73, 20)
            if has_audio and seed % 9 == 0:
                spoken_language = SpokenLanguage.non_english
                spoken_text = "bonjour ceci est un test de parole"
            return VideoSpec(
                width=width,
                height=height,
                duration_seconds=duration,
                framerate=framerate,
                bitrate_kbps=bitrate,
                has_audio=has_audio,
                spoken_language=spoken_language,
                spoken_text=spoken_text,
                empty_content=empty_content,
            )

        case "text/plain":
            return TextSpec(
                random_base64=seed % 2 == 0,
                word_count=random.Random(seed + 8).randint(0, 1200),
                empty_content=empty_content,
            )

        case "text/markdown":
            return MarkdownSpec(
                section_count=random.Random(seed + 9).randint(1, 18),
                include_code_blocks=seed % 2 == 1,
                include_lists=seed % 3 == 0,
                total_words=random.Random(seed + 10).randint(50, 5000),
                empty_content=empty_content,
            )

        case "text/org":
            return OrgSpec(
                section_count=random.Random(seed + 11).randint(1, 18),
                include_src_blocks=seed % 2 == 0,
                include_lists=seed % 3 == 1,
                total_words=random.Random(seed + 12).randint(50, 5000),
                empty_content=empty_content,
            )

        case _:
            raise ValueError(
                f"Unsupported MIME type '{mime_type}', supported values are {sorted(_MIME_SUFFIXES)}"
            )


def seed_to_corpus_entry(
    seed: int,
    mime_types: Sequence[str] = tuple(_MIME_SUFFIXES),
    allow_empty: bool = False,
) -> CorpusFileEntry:
    unsupported = [mime for mime in mime_types if mime not in _MIME_SUFFIXES]
    if 0 < len(unsupported):
        raise ValueError(
            f"Unsupported MIME types {unsupported}, supported values are {sorted(_MIME_SUFFIXES)}"
        )

    mime_type = mime_types[seed % len(mime_types)]
    content_seed = _seed_to_content_seed(seed)
    spec = _seed_to_file_spec(content_seed, mime_type, allow_empty)
    return CorpusFileEntry(
        seed=seed,
        content_seed=content_seed,
        relative_path=_seed_to_relative_path(seed, mime_type),
        file_spec=spec,
    )


def _write_png(spec: PngSpec, output_path: Path, hf_cache_root: Path) -> None:
    if spec.empty_content:
        output_path.write_bytes(b"")
        return

    image: Image.Image
    match spec.kind:
        case ImageKind.monotone:
            image = Image.new("RGB", (spec.width, spec.height), spec.color_rgb)

        case ImageKind.geometric:
            image = Image.new("RGB", (spec.width, spec.height), (245, 245, 245))
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, spec.width - 10, spec.height - 10),
                           outline=(20, 20, 20),
                           width=3)
            draw.ellipse((20, 20, spec.width // 2, spec.height // 2), fill=spec.color_rgb)
            draw.polygon(
                (
                    spec.width // 2,
                    20,
                    spec.width - 20,
                    spec.height // 2,
                    spec.width // 2,
                    spec.height - 20,
                ),
                fill=(spec.color_rgb[2], spec.color_rgb[1], spec.color_rgb[0]),
            )
            if spec.include_text:
                draw.text((24, spec.height - 48), spec.text_value, fill=(0, 0, 0))

        case ImageKind.hf_image:
            if spec.hf_repo_id is None or spec.hf_file_name is None:
                raise ValueError(
                    f"Image kind '{spec.kind.value}' requires hf_repo_id and hf_file_name, got repo={spec.hf_repo_id}, file={spec.hf_file_name}"
                )
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(
                repo_id=spec.hf_repo_id,
                filename=spec.hf_file_name,
                cache_dir=str(hf_cache_root),
            )
            image = Image.open(downloaded).convert("RGB").resize(
                (spec.width, spec.height))
            if spec.include_text:
                draw = ImageDraw.Draw(image)
                draw.text((12, 12), spec.text_value, fill=(255, 0, 0))

    png_info = PngImagePlugin.PngInfo()
    for key, value in spec.exif_metadata.items():
        png_info.add_text(key, value)
    image.save(output_path, format="PNG", pnginfo=png_info)


def _write_pdf(spec: PdfSpec, output_path: Path) -> None:
    if spec.empty_content:
        output_path.write_bytes(b"")
        return

    canvas = Canvas(str(output_path), pagesize=A4)
    page_width, page_height = A4

    for page_index in range(spec.page_count):
        if spec.include_headers:
            canvas.setFont("Helvetica-Bold", 14)
            canvas.drawString(50, page_height - 40,
                              f"Corpus PDF page {page_index + 1}/{spec.page_count}")

        match spec.render_kind:
            case PdfRenderKind.selectable_text:
                canvas.setFont("Helvetica", 11)
                y = page_height - 80
                for block_index in range(spec.selectable_text_blocks):
                    text = _deterministic_words(page_index * 1000 + block_index + 1, 26)
                    canvas.drawString(50, y, text)
                    y -= 22
                    if y < 80:
                        break
                if spec.include_images:
                    image = Image.new("RGB", (280, 120), (220, 230, 245))
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((8, 8, 272, 112), outline=(20, 20, 20), width=2)
                    draw.text((16, 46), "embedded image", fill=(0, 0, 0))
                    canvas.drawImage(ImageReader(image), 50, 120, width=280, height=120)

            case PdfRenderKind.raster_scan:
                raster = Image.new("RGB", (1240, 1754), (255, 255, 255))
                draw = ImageDraw.Draw(raster)
                draw.rectangle((40, 40, 1200, 1710), outline=(0, 0, 0), width=4)
                draw.text((80, 80), f"scanned page {page_index + 1}", fill=(0, 0, 0))
                draw.text((80, 140),
                          _deterministic_words(page_index + 9000, 40),
                          fill=(0, 0, 0))
                canvas.drawImage(ImageReader(raster),
                                 0,
                                 0,
                                 width=page_width,
                                 height=page_height)

        canvas.showPage()

    canvas.save()


def _write_audio(spec: AudioSpec, output_path: Path) -> None:
    if spec.empty_content:
        output_path.write_bytes(b"")
        return

    with tempfile.TemporaryDirectory(prefix="haxdex_audio_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        speech_wav = tmp_dir / "speech.wav"

        match spec.spoken_language:
            case SpokenLanguage.none:
                _run_command([
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={spec.duration_seconds}",
                    "-b:a",
                    f"{spec.bitrate_kbps}k",
                    str(output_path),
                ])

            case SpokenLanguage.english:
                _run_command(
                    ["espeak-ng", "-v", "en", "-w",
                     str(speech_wav), spec.spoken_text])
                _run_command([
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(speech_wav),
                    "-t",
                    str(spec.duration_seconds),
                    "-b:a",
                    f"{spec.bitrate_kbps}k",
                    str(output_path),
                ])

            case SpokenLanguage.non_english:
                _run_command(
                    ["espeak-ng", "-v", "es", "-w",
                     str(speech_wav), spec.spoken_text])
                _run_command([
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(speech_wav),
                    "-t",
                    str(spec.duration_seconds),
                    "-b:a",
                    f"{spec.bitrate_kbps}k",
                    str(output_path),
                ])


def _write_video(spec: VideoSpec, output_path: Path) -> None:
    if spec.empty_content:
        output_path.write_bytes(b"")
        return

    with tempfile.TemporaryDirectory(prefix="haxdex_video_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        speech_audio = tmp_dir / "speech.mp3"

        if spec.has_audio:
            audio_spec = AudioSpec(
                duration_seconds=spec.duration_seconds,
                bitrate_kbps=96,
                spoken_language=spec.spoken_language,
                spoken_text=spec.spoken_text,
                empty_content=False,
            )
            _write_audio(audio_spec, speech_audio)

            _run_command([
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={spec.width}x{spec.height}:rate={spec.framerate}:duration={spec.duration_seconds}",
                "-i",
                str(speech_audio),
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                f"{spec.bitrate_kbps}k",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                str(output_path),
            ])
        else:
            _run_command([
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={spec.width}x{spec.height}:rate={spec.framerate}:duration={spec.duration_seconds}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                f"{spec.bitrate_kbps}k",
                str(output_path),
            ])


def _write_text(spec: TextSpec, output_path: Path, seed: int) -> None:
    if spec.empty_content:
        output_path.write_text("", encoding="utf-8")
        return

    if spec.random_base64:
        raw = hashlib.sha256(f"{seed}".encode("utf-8")).digest() * 30
        output_path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    else:
        output_path.write_text(_deterministic_words(seed + 101, spec.word_count),
                               encoding="utf-8")


def _write_markdown(spec: MarkdownSpec, output_path: Path, seed: int) -> None:
    if spec.empty_content:
        output_path.write_text("", encoding="utf-8")
        return

    rng = random.Random(seed)
    section_words = max(1, spec.total_words // spec.section_count)
    lines: list[str] = []

    for section_index in range(spec.section_count):
        lines.append(f"# Section {section_index + 1}")
        lines.append("")
        lines.append(_deterministic_words(rng.randint(0, 10_000_000), section_words))
        lines.append("")
        if spec.include_lists:
            lines.append("- item one")
            lines.append("- item two")
            lines.append("- item three")
            lines.append("")
        if spec.include_code_blocks:
            lines.append("```python")
            lines.append("value = 42")
            lines.append("print(value)")
            lines.append("```")
            lines.append("")

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_org(spec: OrgSpec, output_path: Path, seed: int) -> None:
    if spec.empty_content:
        output_path.write_text("", encoding="utf-8")
        return

    rng = random.Random(seed)
    section_words = max(1, spec.total_words // spec.section_count)
    lines: list[str] = []

    for section_index in range(spec.section_count):
        lines.append(f"* Section {section_index + 1}")
        lines.append(_deterministic_words(rng.randint(0, 10_000_000), section_words))
        lines.append("")
        if spec.include_lists:
            lines.append("- [ ] item one")
            lines.append("- [ ] item two")
            lines.append("")
        if spec.include_src_blocks:
            lines.append("#+begin_src python")
            lines.append("value = 42")
            lines.append("print(value)")
            lines.append("#+end_src")
            lines.append("")

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def write_corpus_file(
    entry: CorpusFileEntry,
    corpus_root: Path,
    hf_cache_root: Path,
) -> Path:
    output_path = corpus_root / entry.relative_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    spec = entry.file_spec
    match spec:
        case PdfSpec():
            _write_pdf(spec, output_path)
        case PngSpec():
            _write_png(spec, output_path, hf_cache_root)
        case AudioSpec():
            _write_audio(spec, output_path)
        case VideoSpec():
            _write_video(spec, output_path)
        case TextSpec():
            _write_text(spec, output_path, entry.content_seed)
        case MarkdownSpec():
            _write_markdown(spec, output_path, entry.content_seed)
        case OrgSpec():
            _write_org(spec, output_path, entry.content_seed)
        case _:
            raise ValueError(f"Unsupported corpus file spec type '{type(spec).__name__}'")

    return output_path


def initialize_persistent_corpus(
    corpus_root: Path,
    seeds: Sequence[int],
    mime_types: Sequence[str] = tuple(_MIME_SUFFIXES),
    allow_empty: bool = True,
) -> CorpusManifest:
    hf_cache_root = corpus_root / ".hf_cache"
    manifest_path = corpus_root / "manifest.json"

    entries = [
        seed_to_corpus_entry(seed=seed, mime_types=mime_types, allow_empty=allow_empty)
        for seed in seeds
    ]
    manifest = CorpusManifest(version=1, entries=entries)

    if manifest_path.exists():
        current = CorpusManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8"))
        if current.model_dump(mode="json") == manifest.model_dump(mode="json"):
            all_exist = all(
                (corpus_root / item.relative_path).exists() for item in current.entries)
            if all_exist:
                return current

    if corpus_root.exists():
        shutil.rmtree(corpus_root)
    corpus_root.mkdir(parents=True, exist_ok=True)

    for entry in manifest.entries:
        write_corpus_file(entry=entry,
                          corpus_root=corpus_root,
                          hf_cache_root=hf_cache_root)

    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest
