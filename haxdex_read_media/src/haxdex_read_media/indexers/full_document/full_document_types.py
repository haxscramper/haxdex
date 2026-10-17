import enum

from beartype.typing import Annotated, Any, Literal, Sequence, Union
from pydantic import BaseModel, Field, TypeAdapter, field_serializer, field_validator

TextAlignment = Literal["left", "center", "right", "justify"]


class Markup(str, enum.Enum):
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    STRIKE = "strike"
    CODE = "code"
    MATH = "math"
    RAW = "raw"


class TextStyles(BaseModel, extra="forbid"):
    markup: set[Markup] = Field(default_factory=set)
    textColor: str | None = None
    backgroundColor: str | None = None

    @field_serializer("markup")
    def serialize_markup(self, value: set[Markup]) -> list[str]:
        return sorted(item.value for item in value)

    @field_validator("markup", mode="before")
    @classmethod
    def deserialize_markup(cls, value):
        if isinstance(value, list):
            return {Markup(item) for item in value}
        return value

    def extend(self, value: Markup) -> "TextStyles":
        return self.model_copy(update={"markup": self.markup | {value}})


class StyledText(BaseModel, extra="forbid"):
    type: Literal["text"] = "text"
    text: str
    styles: TextStyles = Field(default_factory=TextStyles)


class Link(BaseModel, extra="forbid"):
    type: Literal["link"] = "link"
    href: str
    content: list[StyledText] = Field(default_factory=list)


InlineNode = Annotated[Union[StyledText, Link], Field(discriminator="type")]
InlineStructure = Annotated[Union[Link], Field(discriminator="type")]
_inline_structure_adapter = TypeAdapter(InlineStructure)


class SliceValue(BaseModel):
    start: int
    stop: int
    step: int | None = None

    @staticmethod
    def FromSlice(v: slice) -> "SliceValue":
        return SliceValue(
            start=v.start,
            stop=v.stop,
            step=v.step,
        )


class InlineContent(BaseModel, extra="forbid"):
    text: str = ""
    plaintext: str = ""
    annotations: list[tuple[SliceValue, InlineStructure]] = Field(default_factory=list)


class CaptionProps(BaseModel, extra="forbid"):
    caption: str | None = None


class DefaultProps(CaptionProps, extra="forbid"):
    textColor: str = "default"
    backgroundColor: str = "default"
    textAlignment: TextAlignment = "left"


class HeadingProps(DefaultProps):
    level: int = 1
    extra: dict[str, Any] = Field(default_factory=dict)


class CodeBlockProps(CaptionProps, extra="forbid"):
    language: str = "text"


class DivProps(CaptionProps, extra="forbid"):
    identifier: str = ""
    classes: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


class DocumentBlock(BaseModel, extra="forbid"):
    nested: list["DocumentBlock"] = Field(default_factory=list)


class File(DocumentBlock):
    type: Literal["file"] = "file"


class Document(DocumentBlock):
    type: Literal["document"] = "document"


class Paragraph(DocumentBlock):
    type: Literal["paragraph"] = "paragraph"
    props: DefaultProps = Field(default_factory=DefaultProps)
    content: InlineContent = Field(default_factory=InlineContent)


class Math(DocumentBlock):
    type: Literal["math"] = "math"
    content: InlineContent = Field(default_factory=InlineContent)


class Heading(DocumentBlock):
    type: Literal["heading"] = "heading"
    props: HeadingProps = Field(default_factory=HeadingProps)
    content: InlineContent = Field(default_factory=InlineContent)


class Code(DocumentBlock):
    type: Literal["codeBlock"] = "codeBlock"
    props: CodeBlockProps = Field(default_factory=CodeBlockProps)
    content: str = ""


class RawBlock(DocumentBlock, extra="forbid"):
    type: Literal["rawBlock"] = "rawBlock"
    content: str
    lang: str


class BulletListItem(DocumentBlock):
    type: Literal["bulletListItem"] = "bulletListItem"
    props: DefaultProps = Field(default_factory=DefaultProps)
    content: InlineContent = Field(default_factory=InlineContent)


class NumberedListItem(DocumentBlock):
    type: Literal["numberedListItem"] = "numberedListItem"
    props: DefaultProps = Field(default_factory=DefaultProps)
    content: InlineContent = Field(default_factory=InlineContent)


class Quote(DocumentBlock):
    type: Literal["quote"] = "quote"
    props: DefaultProps = Field(default_factory=DefaultProps)
    content: InlineContent = Field(default_factory=InlineContent)


class Div(DocumentBlock):
    type: Literal["div"] = "div"
    props: DivProps = Field(default_factory=DivProps)


class TableCellProps(DefaultProps, extra="forbid"):
    rowSpan: int = 1
    colSpan: int = 1
    isHeader: bool = False


class TableCell(DocumentBlock, extra="forbid"):
    type: Literal["tableCell"] = "tableCell"
    props: TableCellProps = Field(default_factory=TableCellProps)
    content: InlineContent = Field(default_factory=InlineContent)


class TableRow(DocumentBlock, extra="forbid"):
    type: Literal["tableRow"] = "tableRow"


class TableProps(CaptionProps, extra="forbid"):
    pass


class Table(DocumentBlock):
    type: Literal["table"] = "table"
    props: TableProps = Field(default_factory=TableProps)


for _cls in (
    File,
    Document,
    Paragraph,
    Math,
    Heading,
    Code,
    RawBlock,
    BulletListItem,
    NumberedListItem,
    Quote,
    Div,
    TableCell,
    TableRow,
    Table,
):
    _cls.model_rebuild()


def build[T: DocumentBlock](
    cls: type[T],
    *,
    nested: Sequence[DocumentBlock] = (),
    **kwargs: Any,
) -> T:
    return cls.model_validate(
        {
            "nested": list(nested),
            **kwargs,
        }
    )


def merge_text(nodes: list[InlineNode]) -> list[InlineNode]:
    merged: list[InlineNode] = []

    for node in nodes:
        if (
            isinstance(node, StyledText)
            and merged
            and isinstance(merged[-1], StyledText)
            and merged[-1].styles == node.styles
        ):
            merged[-1] = StyledText(
                text=merged[-1].text + node.text,
                styles=node.styles,
            )
        else:
            merged.append(node)

    return merged


def styled_text_to_markdown(node: StyledText) -> str:
    text = node.text
    markup = node.styles.markup

    if Markup.CODE in markup:
        text = f"`{text}`"
    if Markup.MATH in markup:
        text = f"${text}$"
    if Markup.BOLD in markup:
        text = f"**{text}**"
    if Markup.ITALIC in markup:
        text = f"*{text}*"
    if Markup.STRIKE in markup:
        text = f"~~{text}~~"
    if Markup.UNDERLINE in markup:
        text = f"<u>{text}</u>"

    return text


def inline_nodes_to_content(nodes: Sequence[InlineNode]) -> InlineContent:
    text_parts: list[str] = []
    plaintext_parts: list[str] = []
    annotations: list[tuple[SliceValue, InlineStructure]] = []
    text_length = 0

    for node in merge_text(list(nodes)):
        if isinstance(node, StyledText):
            rendered = styled_text_to_markdown(node)
            text_parts.append(rendered)
            plaintext_parts.append(node.text)
            text_length += len(rendered)
            continue

        if isinstance(node, Link):
            start = text_length
            link_text = "".join(
                styled_text_to_markdown(segment) for segment in node.content
            )
            text_parts.append(link_text)
            plaintext_parts.extend(segment.text for segment in node.content)
            text_length += len(link_text)

            annotations.append(
                (
                    SliceValue.FromSlice(slice(start, text_length)),
                    node,
                )
            )

    return InlineContent(
        text="".join(text_parts),
        plaintext="".join(plaintext_parts),
        annotations=annotations,
    )
