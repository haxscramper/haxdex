from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Literal

import pymupdf
import requests
from docling_core.types.doc.document import (
    ContentLayer,
    DoclingDocument,
    DocTagsDocument,
)
from haxdex_read_media.resources.pdf.page_model import BoundingBox, DocTag
from loguru import logger
from PIL import Image as PILImage
from pydantic import BaseModel, Field


class DoclingExtractedImage(BaseModel, extra="forbid"):
    tag_id: str
    image_blob: bytes


class DoclingPage(BaseModel, extra="forbid"):
    extractor: Literal["docling"] = "docling"
    page_number: int
    raw_docling_response: str
    spatial_tags: list[DocTag]
    document: DoclingDocument
    image_width: int
    image_height: int
    extracted_images: list[DoclingExtractedImage] = Field(
        default_factory=list,
    )


def _normalize_bbox(
    left: float,
    top: float,
    right: float,
    bottom: float,
    width: int,
    height: int,
) -> BoundingBox:
    x1, x2 = sorted(
        (
            min(1.0, max(0.0, left / width)),
            min(1.0, max(0.0, right / width)),
        )
    )
    y1, y2 = sorted(
        (
            min(1.0, max(0.0, top / height)),
            min(1.0, max(0.0, bottom / height)),
        )
    )

    return BoundingBox(
        x=x1,
        y=y1,
        width=x2 - x1,
        height=y2 - y1,
    )


def parse_spatial_tags(
    raw_text: str,
    page_num: int,
    page_image: PILImage.Image,
) -> tuple[list[DocTag], DoclingDocument]:
    content = raw_text.strip()

    if not content:
        raise RuntimeError(f"Model returned empty output for page {page_num}")

    doc_tags = DocTagsDocument.from_doctags_and_image_pairs(
        doctags=[content],
        images=[page_image],
    )
    document = DoclingDocument.load_from_doctags(
        doctag_document=doc_tags,
    )

    root = DocTag(
        id=f"page{page_num}-root",
        tag_name="root",
        text="",
    )
    stack: list[tuple[int, DocTag]] = []

    for index, (item, level) in enumerate(
        document.iterate_items(
            included_content_layers=set(ContentLayer),
            with_groups=True,
        ),
        start=1,
    ):
        label = getattr(item, "label", None)
        tag_name = str(label.value) if label else type(item).__name__

        bbox = None
        provenance = getattr(item, "prov", None)

        if provenance and provenance[0].bbox is not None:
            source_bbox = provenance[0].bbox.to_top_left_origin(
                page_height=page_image.height,
            )
            bbox = _normalize_bbox(
                left=source_bbox.l,
                top=source_bbox.t,
                right=source_bbox.r,
                bottom=source_bbox.b,
                width=page_image.width,
                height=page_image.height,
            )

        tag = DocTag(
            id=f"page{page_num}-{tag_name}-{index}",
            tag_name=tag_name,
            text=getattr(item, "text", "") or "",
            bbox=bbox,
        )

        while stack and stack[-1][0] >= level:
            stack.pop()

        container = stack[-1][1] if stack else root
        container.nested.append(tag)
        stack.append((level, tag))

    return [root], document


def _extract_images(
    image: PILImage.Image,
    tags: list[DocTag],
) -> list[DoclingExtractedImage]:
    extracted: list[DoclingExtractedImage] = []

    def visit(tag: DocTag) -> None:
        if (
            tag.tag_name.strip().casefold() in {"picture", "image"}
            and tag.bbox is not None
        ):
            bbox = tag.bbox

            # Docling boxes are normalized to [0, 1], not [0, 999].
            x1 = round(bbox.x * image.width)
            y1 = round(bbox.y * image.height)
            x2 = round((bbox.x + bbox.width) * image.width)
            y2 = round((bbox.y + bbox.height) * image.height)

            if x2 > x1 and y2 > y1:
                with image.crop((x1, y1, x2, y2)) as crop:
                    buffer = io.BytesIO()
                    crop.save(buffer, format="PNG")

                extracted.append(
                    DoclingExtractedImage(
                        tag_id=tag.id,
                        image_blob=buffer.getvalue(),
                    )
                )

        for nested in tag.nested:
            visit(nested)

    for tag in tags:
        visit(tag)

    return extracted


class DoclingExtractor:
    """Extracts PDF pages through an existing llama.cpp server."""

    def __init__(
        self,
        llama_server_url: str,
        model_id: str = "ibm/granite-docling",
        dpi: int = 300,
        request_timeout: int = 600,
    ) -> None:
        self.llama_server_url = llama_server_url.rstrip("/")
        self.model_id = model_id
        self.dpi = dpi
        self.request_timeout = request_timeout

    def check_server(self) -> None:
        response = requests.get(
            f"{self.llama_server_url}/health",
            timeout=self.request_timeout,
        )
        response.raise_for_status()

    def call_model(
        self,
        image: PILImage.Image,
        page_number: int,
    ) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        encoded_image = base64.b64encode(buffer.getvalue()).decode("ascii")

        response = requests.post(
            f"{self.llama_server_url}/v1/chat/completions",
            json={
                "model": self.model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Convert this page to docling.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (f"data:image/jpeg;base64,{encoded_image}"),
                                },
                            },
                        ],
                    },
                ],
                "temperature": 0,
                "stream": False,
            },
            timeout=self.request_timeout,
        )
        response.raise_for_status()

        raw_text = response.json()["choices"][0]["message"]["content"]

        if not isinstance(raw_text, str) or not raw_text.strip():
            raise RuntimeError(f"Model returned empty output for page {page_number}")

        return raw_text

    def extract_page(
        self,
        pdf_path: Path,
        page_num: int,
    ) -> DoclingPage:
        logger.info(f"Docling extracting page {page_num} from {pdf_path.name}")

        with pymupdf.open(pdf_path) as pdf:
            pixmap = pdf[page_num - 1].get_pixmap(
                dpi=self.dpi,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )

        with PILImage.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        ) as image:
            raw_text = self.call_model(
                image=image,
                page_number=page_num,
            )
            spatial_tags, document = parse_spatial_tags(
                raw_text=raw_text,
                page_num=page_num,
                page_image=image,
            )
            extracted_images = _extract_images(image, spatial_tags)

            return DoclingPage(
                page_number=page_num,
                raw_docling_response=raw_text,
                spatial_tags=spatial_tags,
                document=document,
                image_width=image.width,
                image_height=image.height,
                extracted_images=extracted_images,
            )
