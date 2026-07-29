from __future__ import annotations

import base64
import hashlib
import random
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path

import mistletoe
from beartype.typing import Literal, Sequence, Union
from dominate import document as html_document
from dominate import tags
from ebooklib import epub
from PIL import Image, ImageDraw, PngImagePlugin
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from haxdex.services.indexers.full_document import full_document_types as doc_types

_MIME_SUFFIXES: dict[str, str] = {
    "image/png": ".png",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "text/plain": ".txt",
    "text/org": ".org",
    "text/markdown": ".md",
    "text/html": ".html",
    "application/epub+zip": ".epub",
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


class HtmlSpec(FileSpecBase):
    mime_type: Literal["text/html"] = "text/html"
    section_count: int
    include_code_blocks: bool
    include_lists: bool
    total_words: int
    include_title: bool


class EpubSpec(FileSpecBase):
    mime_type: Literal["application/epub+zip"] = "application/epub+zip"
    section_count: int
    include_code_blocks: bool
    include_lists: bool
    total_words: int
    title: str


FileSpec = Union[
    PdfSpec,
    PngSpec,
    AudioSpec,
    VideoSpec,
    TextSpec,
    MarkdownSpec,
    OrgSpec,
    HtmlSpec,
    EpubSpec,
]


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
) -> FileSpec:
    rng = random.Random(seed)

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
            )

        case "text/plain":
            return TextSpec(
                random_base64=seed % 2 == 0,
                word_count=random.Random(seed + 8).randint(0, 1200),
            )

        case "text/markdown":
            return MarkdownSpec(
                section_count=random.Random(seed + 9).randint(1, 18),
                include_code_blocks=seed % 2 == 1,
                include_lists=seed % 3 == 0,
                total_words=random.Random(seed + 10).randint(50, 5000),
            )

        case "text/org":
            return OrgSpec(
                section_count=random.Random(seed + 11).randint(1, 18),
                include_src_blocks=seed % 2 == 0,
                include_lists=seed % 3 == 1,
                total_words=random.Random(seed + 12).randint(50, 5000),
            )

        case "text/html":
            return HtmlSpec(
                section_count=random.Random(seed + 13).randint(1, 18),
                include_code_blocks=seed % 2 == 0,
                include_lists=seed % 3 == 2,
                total_words=random.Random(seed + 14).randint(50, 5000),
                include_title=seed % 2 == 1,
            )

        case "application/epub+zip":
            return EpubSpec(
                section_count=random.Random(seed + 15).randint(1, 18),
                include_code_blocks=seed % 2 == 1,
                include_lists=seed % 3 == 0,
                total_words=random.Random(seed + 16).randint(50, 5000),
                title=f"Corpus EPUB {seed}",
            )

        case _:
            raise ValueError(
                f"Unsupported MIME type '{mime_type}', supported values are {sorted(_MIME_SUFFIXES)}"
            )


def seed_to_corpus_entry(
        seed: int,
        mime_types: Sequence[str] = tuple(_MIME_SUFFIXES),
) -> CorpusFileEntry:
    unsupported = [mime for mime in mime_types if mime not in _MIME_SUFFIXES]
    if 0 < len(unsupported):
        raise ValueError(
            f"Unsupported MIME types {unsupported}, supported values are {sorted(_MIME_SUFFIXES)}"
        )

    mime_type = mime_types[seed % len(mime_types)]
    content_seed = _seed_to_content_seed(seed)
    spec = _seed_to_file_spec(content_seed, mime_type)
    return CorpusFileEntry(
        seed=seed,
        content_seed=content_seed,
        relative_path=_seed_to_relative_path(seed, mime_type),
        file_spec=spec,
    )


def _inline_content(text: str) -> doc_types.InlineContent:
    return doc_types.InlineContent(text=text)


def _paragraph_block(text: str) -> doc_types.Paragraph:
    return doc_types.Paragraph.build(content=_inline_content(text))


def _heading_block(text: str, level: int = 1) -> doc_types.Heading:
    return doc_types.Heading.build(
        props=doc_types.HeadingProps(level=level),
        content=_inline_content(text),
    )


def _code_block(language: str, content: str) -> doc_types.Code:
    return doc_types.Code.build(
        props=doc_types.CodeBlockProps(language=language),
        content=content,
    )


def _bullet_item(text: str) -> doc_types.BulletListItem:
    return doc_types.BulletListItem.build(content=_inline_content(text))


def _build_structured_document(
    *,
    seed: int,
    section_count: int,
    total_words: int,
    include_lists: bool,
    include_code_blocks: bool,
) -> doc_types.Document:
    rng = random.Random(seed)
    words_per_section = max(1, total_words // section_count)
    blocks: list[doc_types.DocumentBlock] = []

    for section_index in range(section_count):
        blocks.append(_heading_block(f"Section {section_index + 1}", level=1))
        blocks.append(
            _paragraph_block(
                _deterministic_words(rng.randint(0, 10_000_000), words_per_section)))
        if include_lists:
            blocks.append(_bullet_item("item one"))
            blocks.append(_bullet_item("item two"))
            blocks.append(_bullet_item("item three"))
        if include_code_blocks:
            blocks.append(_code_block("python", "value = 42\nprint(value)"))

    return doc_types.Document.build(nested=tuple(blocks))


def _build_pdf_documents(seed: int, spec: PdfSpec) -> list[doc_types.Document]:
    rng = random.Random(seed)
    pages: list[doc_types.Document] = []

    for page_index in range(spec.page_count):
        blocks: list[doc_types.DocumentBlock] = []
        if spec.include_headers:
            blocks.append(
                _heading_block(
                    f"Corpus PDF page {page_index + 1}/{spec.page_count}",
                    level=1,
                ))

        for block_index in range(spec.selectable_text_blocks):
            blocks.append(
                _paragraph_block(
                    _deterministic_words(
                        rng.randint(0, 10_000_000) + page_index * 97 + block_index, 26)))

        if spec.include_images:
            blocks.append(
                doc_types.Div.build(props=doc_types.DivProps(
                    identifier="embedded-image-placeholder",
                    classes=["pdf-image"],
                )))

        pages.append(doc_types.Document.build(nested=tuple(blocks)))

    return pages


def _block_text(block: doc_types.DocumentBlock) -> str:
    if isinstance(block, doc_types.Heading):
        return block.content.text
    if isinstance(block, doc_types.Paragraph):
        return block.content.text
    if isinstance(block, doc_types.BulletListItem):
        return f"- {block.content.text}"
    if isinstance(block, doc_types.NumberedListItem):
        return f"1. {block.content.text}"
    if isinstance(block, doc_types.Quote):
        return f"> {block.content.text}"
    if isinstance(block, doc_types.Code):
        return block.content
    return ""


def _render_pdf_from_documents(
    documents: Sequence[doc_types.Document],
    spec: PdfSpec,
    output_path: Path,
) -> None:
    canvas = Canvas(str(output_path), pagesize=A4)
    page_width, page_height = A4

    for page_index, document in enumerate(documents):
        match spec.render_kind:
            case PdfRenderKind.selectable_text:
                y = page_height - 40
                for block in document.nested:
                    if isinstance(block, doc_types.Heading):
                        canvas.setFont("Helvetica-Bold", 14)
                        canvas.drawString(50, y, _block_text(block))
                        y -= 28
                    elif isinstance(block, doc_types.Paragraph):
                        canvas.setFont("Helvetica", 11)
                        canvas.drawString(50, y, _block_text(block))
                        y -= 20
                    elif isinstance(block, doc_types.BulletListItem):
                        canvas.setFont("Helvetica", 11)
                        canvas.drawString(60, y, _block_text(block))
                        y -= 18
                    elif isinstance(block, doc_types.Code):
                        canvas.setFont("Courier", 10)
                        for line in block.content.splitlines():
                            canvas.drawString(50, y, line)
                            y -= 14
                    elif isinstance(
                            block, doc_types.Div
                    ) and block.props.identifier == "embedded-image-placeholder":
                        image = Image.new("RGB", (280, 120), (220, 230, 245))
                        draw = ImageDraw.Draw(image)
                        draw.rectangle((8, 8, 272, 112), outline=(20, 20, 20), width=2)
                        draw.text((16, 46), "embedded image", fill=(0, 0, 0))
                        canvas.drawImage(ImageReader(image),
                                         50,
                                         max(80, y - 130),
                                         width=280,
                                         height=120)
                        y -= 140

                    if y < 80:
                        break

            case PdfRenderKind.raster_scan:
                raster = Image.new("RGB", (1240, 1754), (255, 255, 255))
                draw = ImageDraw.Draw(raster)
                draw.rectangle((40, 40, 1200, 1710), outline=(0, 0, 0), width=4)

                y = 90
                for block in document.nested:
                    text = _block_text(block)
                    if not text:
                        continue

                    if isinstance(block, doc_types.Code):
                        for line in block.content.splitlines():
                            draw.text((80, y), line, fill=(0, 0, 0))
                            y += 24
                    else:
                        draw.text((80, y), text, fill=(0, 0, 0))
                        y += 34

                    if y > 1640:
                        break

                draw.text((80, 1668), f"scanned page {page_index + 1}", fill=(0, 0, 0))
                canvas.drawImage(ImageReader(raster),
                                 0,
                                 0,
                                 width=page_width,
                                 height=page_height)

        canvas.showPage()

    canvas.save()


def _document_to_markdown_source(document: doc_types.Document) -> str:
    lines: list[str] = []

    for block in document.nested:
        if isinstance(block, doc_types.Heading):
            level = max(1, block.props.level)
            lines.append("{} {}".format("#" * level, block.content.text))
            lines.append("")
        elif isinstance(block, doc_types.Paragraph):
            lines.append(block.content.text)
            lines.append("")
        elif isinstance(block, doc_types.BulletListItem):
            lines.append("- {}".format(block.content.text))
        elif isinstance(block, doc_types.NumberedListItem):
            lines.append("1. {}".format(block.content.text))
        elif isinstance(block, doc_types.Code):
            lines.append("```{}".format(block.props.language))
            lines.extend(block.content.splitlines())
            lines.append("```")
            lines.append("")

    source = "\n".join(lines).strip() + "\n"
    parsed = mistletoe.Document(source)
    return source if parsed is not None else source


def _write_markdown_from_document(document: doc_types.Document,
                                  output_path: Path) -> None:
    output_path.write_text(_document_to_markdown_source(document), encoding="utf-8")


def _write_org_from_document(document: doc_types.Document, output_path: Path) -> None:
    lines: list[str] = []

    for block in document.nested:
        if isinstance(block, doc_types.Heading):
            level = max(1, block.props.level)
            lines.append("{} {}".format("*" * level, block.content.text))
        elif isinstance(block, doc_types.Paragraph):
            lines.append(block.content.text)
            lines.append("")
        elif isinstance(block, doc_types.BulletListItem):
            lines.append("- [ ] {}".format(block.content.text))
        elif isinstance(block, doc_types.NumberedListItem):
            lines.append("1. {}".format(block.content.text))
        elif isinstance(block, doc_types.Code):
            lines.append("#+begin_src {}".format(block.props.language))
            lines.extend(block.content.splitlines())
            lines.append("#+end_src")
            lines.append("")

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _append_html_block(parent: tags.html_tag, block: doc_types.DocumentBlock) -> None:
    if isinstance(block, doc_types.Heading):
        level = min(6, max(1, block.props.level))
        tag_name = "h{}".format(level)
        parent.add(getattr(tags, tag_name)(block.content.text))
        return

    if isinstance(block, doc_types.Paragraph):
        parent.add(tags.p(block.content.text))
        return

    if isinstance(block, doc_types.BulletListItem):
        parent.add(tags.ul(tags.li(block.content.text)))
        return

    if isinstance(block, doc_types.NumberedListItem):
        parent.add(tags.ol(tags.li(block.content.text)))
        return

    if isinstance(block, doc_types.Quote):
        parent.add(tags.blockquote(block.content.text))
        return

    if isinstance(block, doc_types.Code):
        parent.add(tags.pre(tags.code(block.content)))
        return

    if isinstance(block, doc_types.Div):
        wrapper = tags.div()
        if block.props.identifier:
            wrapper["id"] = block.props.identifier
        if block.props.classes:
            wrapper["class"] = " ".join(block.props.classes)
        for key, value in block.props.attributes.items():
            wrapper[key] = value
        parent.add(wrapper)
        return


def _document_to_html_string(document: doc_types.Document, title: str) -> str:
    dom = html_document(title=title)
    with dom:
        main = tags.main()
        for block in document.nested:
            _append_html_block(main, block)
    return dom.render()


def _write_html_from_document(
    *,
    document: doc_types.Document,
    output_path: Path,
    include_title: bool,
    title: str,
) -> None:
    html_title = title if include_title else ""
    output_path.write_text(
        _document_to_html_string(document, html_title),
        encoding="utf-8",
    )


def _split_document_for_epub(document: doc_types.Document) -> list[doc_types.Document]:
    chapters: list[list[doc_types.DocumentBlock]] = []
    current: list[doc_types.DocumentBlock] = []

    for block in document.nested:
        if isinstance(block,
                      doc_types.Heading) and block.props.level == 1 and len(current) > 0:
            chapters.append(current)
            current = [block]
        else:
            current.append(block)

    if len(current) > 0:
        chapters.append(current)

    return [doc_types.Document.build(nested=tuple(chunk)) for chunk in chapters]


def _write_epub_from_document(
    *,
    document: doc_types.Document,
    output_path: Path,
    title: str,
) -> None:
    book = epub.EpubBook()
    identifier = hashlib.sha256(str(output_path).encode("utf-8")).hexdigest()[:16]

    book.set_identifier(identifier)
    book.set_title(title)
    book.set_language("en")

    chapters: list[epub.EpubHtml] = []
    for index, chapter_document in enumerate(_split_document_for_epub(document), start=1):
        chapter_title = "Chapter {}".format(index)
        chapter_file = "chapter-{}.xhtml".format(index)
        chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_file, lang="en")
        chapter.content = _document_to_html_string(chapter_document, chapter_title)
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]

    epub.write_epub(str(output_path), book, {})


def _write_png(spec: PngSpec, output_path: Path, hf_cache_root: Path) -> None:
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


def _write_audio(spec: AudioSpec, output_path: Path) -> None:
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
    with tempfile.TemporaryDirectory(prefix="haxdex_video_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        speech_audio = tmp_dir / "speech.mp3"

        if spec.has_audio:
            audio_spec = AudioSpec(
                duration_seconds=spec.duration_seconds,
                bitrate_kbps=96,
                spoken_language=spec.spoken_language,
                spoken_text=spec.spoken_text,
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
    if spec.random_base64:
        raw = hashlib.sha256(f"{seed}".encode("utf-8")).digest() * 30
        output_path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    else:
        output_path.write_text(_deterministic_words(seed + 101, spec.word_count),
                               encoding="utf-8")


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
            docs = _build_pdf_documents(entry.content_seed, spec)
            _render_pdf_from_documents(docs, spec, output_path)

        case PngSpec():
            _write_png(spec, output_path, hf_cache_root)

        case AudioSpec():
            _write_audio(spec, output_path)

        case VideoSpec():
            _write_video(spec, output_path)

        case TextSpec():
            _write_text(spec, output_path, entry.content_seed)

        case MarkdownSpec():
            document = _build_structured_document(
                seed=entry.content_seed,
                section_count=spec.section_count,
                total_words=spec.total_words,
                include_lists=spec.include_lists,
                include_code_blocks=spec.include_code_blocks,
            )
            _write_markdown_from_document(document, output_path)

        case OrgSpec():
            document = _build_structured_document(
                seed=entry.content_seed,
                section_count=spec.section_count,
                total_words=spec.total_words,
                include_lists=spec.include_lists,
                include_code_blocks=spec.include_src_blocks,
            )
            _write_org_from_document(document, output_path)

        case HtmlSpec():
            document = _build_structured_document(
                seed=entry.content_seed,
                section_count=spec.section_count,
                total_words=spec.total_words,
                include_lists=spec.include_lists,
                include_code_blocks=spec.include_code_blocks,
            )
            _write_html_from_document(
                document=document,
                output_path=output_path,
                include_title=spec.include_title,
                title=f"Corpus HTML {entry.seed}",
            )

        case EpubSpec():
            document = _build_structured_document(
                seed=entry.content_seed,
                section_count=spec.section_count,
                total_words=spec.total_words,
                include_lists=spec.include_lists,
                include_code_blocks=spec.include_code_blocks,
            )
            _write_epub_from_document(
                document=document,
                output_path=output_path,
                title=spec.title,
            )

        case _:
            raise ValueError(f"Unsupported corpus file spec type '{type(spec).__name__}'")

    return output_path


def initialize_persistent_corpus(
        corpus_root: Path,
        seeds: Sequence[int],
        mime_types: Sequence[str] = tuple(_MIME_SUFFIXES),
) -> CorpusManifest:
    hf_cache_root = corpus_root / ".hf_cache"
    manifest_path = corpus_root / "manifest.json"

    entries = [seed_to_corpus_entry(seed=seed, mime_types=mime_types) for seed in seeds]
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
