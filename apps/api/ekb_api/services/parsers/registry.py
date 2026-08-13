"""Parser registry and stable error classification for PH3 ingestion.

Every parser raises :class:`ParserError` carrying a *stable* error code drawn
from the contract in `04-knowledge-base.md` section 8.  Error details are
restricted to a strict allowlist so no raw file content, secret or upstream
response leaks into the API envelope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional, Protocol, runtime_checkable

# ---- Stable error codes (04-knowledge-base.md §8) -------------------------

FILE_EMPTY = "FILE_EMPTY"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
PATH_INVALID = "PATH_INVALID"
PATH_TOO_DEEP = "PATH_TOO_DEEP"
BATCH_LIMIT_EXCEEDED = "BATCH_LIMIT_EXCEEDED"
MIME_UNSUPPORTED = "MIME_UNSUPPORTED"
OBJECT_CHECKSUM_MISMATCH = "OBJECT_CHECKSUM_MISMATCH"
CONVERSION_FAILED = "CONVERSION_FAILED"
PARSER_ENCRYPTED = "PARSER_ENCRYPTED"
PARSER_CORRUPT = "PARSER_CORRUPT"
OCR_FAILED = "OCR_FAILED"
EMBEDDING_RATE_LIMITED = "EMBEDDING_RATE_LIMITED"
EMBEDDING_DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
INDEX_ACTIVATION_FAILED = "INDEX_ACTIVATION_FAILED"
ACL_REVOKED = "ACL_REVOKED"

# Retryability is part of the contract; surfaced through the API envelope.
RETRYABLE_CODES = frozenset(
    {
        OBJECT_CHECKSUM_MISMATCH,
        OCR_FAILED,
        EMBEDDING_RATE_LIMITED,
        INDEX_ACTIVATION_FAILED,
        PATH_INVALID,
    }
)

# Only these keys may appear in a sanitized error detail (never file content,
# secrets or upstream payloads).
_ALLOWED_DETAIL_KEYS = frozenset(
    {
        "detected_mime",
        "byte_size",
        "path",
        "relative_path",
        "expected_sha256",
        "actual_sha256",
        "reason",
        "stage",
        "parser_id",
        "parser_version",
        "dimension",
        "limit",
        "actual",
    }
)


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_CODES


def sanitize_detail(detail: Optional[dict]) -> dict:
    """Keep only allowlisted, length-bounded keys in an error detail."""
    if not detail:
        return {}
    out: dict = {}
    for key in _ALLOWED_DETAIL_KEYS:
        if key not in detail:
            continue
        value = detail[key]
        if isinstance(value, str):
            out[key] = value[:512]
        elif isinstance(value, (int, float, bool)):
            out[key] = value
        else:
            out[key] = str(value)[:512]
    return out


class ParserError(Exception):
    """Parser failure carrying a stable, allowlisted error code."""

    def __init__(self, code: str, message: str, *, detail: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = sanitize_detail(detail)

    def to_sanitized_error(self) -> dict:
        return {
            "code": self.code,
            "message": self.message[:512],
            "retryable": is_retryable(self.code),
            "detail": self.detail,
        }


@dataclass
class ParsedSection:
    section_path: list[str]
    content: str
    meta: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    sections: list[ParsedSection]
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    parser_id: str
    parser_version: str

    def supports(self, mime: str) -> bool: ...

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult: ...


class TxtParser:
    """Text / Markdown parser with encoding detection and heading structure."""

    parser_id = "txt"
    parser_version = "txt-v1.0"

    def supports(self, mime: str) -> bool:
        return mime.startswith("text/") or mime in (
            "application/markdown",
            "text/markdown",
        )

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        if not raw_bytes:
            raise ParserError(
                FILE_EMPTY,
                "文件内容为空",
                detail={"detected_mime": mime, "byte_size": 0},
            )
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParserError(
                PARSER_CORRUPT,
                "文本解码失败，文件可能已损坏或非 UTF-8 编码",
                detail={"detected_mime": mime, "reason": "unicode-decode-error"},
            ) from exc
        text = text.strip()
        if not text:
            raise ParserError(
                FILE_EMPTY,
                "文件内容为空",
                detail={"detected_mime": mime, "byte_size": len(raw_bytes)},
            )

        sections: list[ParsedSection] = []
        current_path: list[str] = []
        current_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                if current_lines:
                    sections.append(
                        ParsedSection(
                            section_path=list(current_path),
                            content="\n".join(current_lines).strip(),
                        )
                    )
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped.lstrip("#").strip()
                if level > 0:
                    current_path = current_path[: level - 1] + [title]
                else:
                    current_path = [title]
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            sections.append(
                ParsedSection(
                    section_path=list(current_path),
                    content="\n".join(current_lines).strip(),
                )
            )
        if not sections:
            sections.append(ParsedSection(section_path=[filename], content=text))
        return ParseResult(sections=sections, metadata={"encoding": "utf-8"})


class _LibreOfficeBackedParser:
    """Base for binary office formats that may require LibreOffice conversion.

    Conversion is delegated to an injected conversion client; if none is
    available the parser fails closed with CONVERSION_FAILED rather than
    pretending success.
    """

    parser_id = "office"
    parser_version = "office-v1.0"
    _mimes: tuple[str, ...] = ()

    def supports(self, mime: str) -> bool:
        return mime in self._mimes

    def _read_bytes_pdf(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ParserError(
                MIME_UNSUPPORTED,
                "PDF 解析依赖不可用",
                detail={"detected_mime": mime, "reason": "dependency-missing"},
            ) from exc
        try:
            reader = PdfReader(raw_bytes)
        except Exception as exc:
            raise ParserError(
                PARSER_CORRUPT,
                "PDF 解析失败",
                detail={"detected_mime": mime, "reason": "pdf-read-error"},
            ) from exc
        if getattr(reader, "is_encrypted", False):
            raise ParserError(
                PARSER_ENCRYPTED,
                "PDF 已加密，暂不支持密码文档",
                detail={"detected_mime": mime},
            )
        sections: list[ParsedSection] = []
        for index, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            page_text = page_text.strip()
            if page_text:
                sections.append(
                    ParsedSection(
                        section_path=[filename, f"第 {index} 页"], content=page_text
                    )
                )
        if not sections:
            raise ParserError(
                PARSER_CORRUPT,
                "PDF 未提取到文本，可能是扫描件（需 OCR）",
                detail={"detected_mime": mime, "reason": "no-text"},
            )
        return ParseResult(sections=sections, metadata={"pages": len(reader.pages)})


class PdfParser(_LibreOfficeBackedParser):
    parser_id = "pdf"
    parser_version = "pdf-v1.0"
    _mimes = ("application/pdf",)


class DocxParser(_LibreOfficeBackedParser):
    parser_id = "docx"
    parser_version = "docx-v1.0"
    _mimes = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        if not raw_bytes:
            raise ParserError(FILE_EMPTY, "文件内容为空", detail={"byte_size": 0})
        try:
            from docx import Document as DocxDocument
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ParserError(
                MIME_UNSUPPORTED,
                "DOCX 解析依赖不可用",
                detail={"detected_mime": mime, "reason": "dependency-missing"},
            ) from exc
        try:
            doc = DocxDocument(raw_bytes)
        except Exception as exc:
            raise ParserError(
                PARSER_CORRUPT,
                "Word 解析失败",
                detail={"detected_mime": mime, "reason": "docx-read-error"},
            ) from exc
        sections: list[ParsedSection] = []
        current_path: list[str] = [filename]
        current_lines: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower()
            if "heading" in style:
                if current_lines:
                    sections.append(
                        ParsedSection(
                            section_path=list(current_path),
                            content="\n".join(current_lines).strip(),
                        )
                    )
                    current_lines = []
                current_path = current_path[:1] + [text]
            else:
                current_lines.append(text)
        if current_lines:
            sections.append(
                ParsedSection(
                    section_path=list(current_path),
                    content="\n".join(current_lines).strip(),
                )
            )
        if not sections:
            raise ParserError(
                PARSER_CORRUPT, "Word 文档内容为空", detail={"detected_mime": mime}
            )
        return ParseResult(sections=sections)


class XlsxParser:
    """Extract searchable, cell-addressed content from an OOXML workbook.

    ``data_only=True`` intentionally reads cached cell values and never
    evaluates formulas or macros.  Each non-empty row becomes a section so
    retrieval can preserve worksheet and row provenance without putting cell
    values into parser metadata.
    """

    parser_id = "xlsx"
    parser_version = "xlsx-v1.0"
    _mimes = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    def supports(self, mime: str) -> bool:
        return mime in self._mimes

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        if not raw_bytes:
            raise ParserError(
                FILE_EMPTY,
                "文件内容为空",
                detail={"detected_mime": mime, "byte_size": 0},
            )
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ParserError(
                MIME_UNSUPPORTED,
                "Excel 解析依赖不可用",
                detail={"detected_mime": mime, "reason": "dependency-missing"},
            ) from exc

        workbook = None
        try:
            # OOXML .xlsx is read-only here: formulas are not executed, and
            # keep_vba remains disabled so a macro project is never run.
            workbook = load_workbook(
                BytesIO(raw_bytes), read_only=True, data_only=True, keep_vba=False
            )
            sheet_count = len(workbook.worksheets)
            sections: list[ParsedSection] = []
            populated_sheets = 0

            for worksheet in workbook.worksheets:
                sheet_sections = 0
                for row_index, row in enumerate(worksheet.iter_rows(), start=1):
                    cells: list[tuple[str, str]] = []
                    for cell in row:
                        value = cell.value
                        if value is None:
                            continue
                        rendered = _render_office_value(value)
                        if not rendered:
                            continue
                        cells.append((cell.coordinate, rendered))
                    if not cells:
                        continue

                    sheet_sections += 1
                    sections.append(
                        ParsedSection(
                            section_path=[filename, worksheet.title, f"第 {row_index} 行"],
                            content="\n".join(
                                f"{address}={value}" for address, value in cells
                            ),
                            meta={
                                "format": "xlsx",
                                "sheet": worksheet.title,
                                "row": row_index,
                                "cell_count": len(cells),
                                "cell_addresses": [address for address, _ in cells],
                            },
                        )
                    )
                if sheet_sections:
                    populated_sheets += 1

            if not sections:
                raise ParserError(
                    PARSER_CORRUPT,
                    "Excel 工作簿未提取到内容",
                    detail={"detected_mime": mime, "reason": "no-cell-content"},
                )
            return ParseResult(
                sections=sections,
                metadata={
                    "format": "xlsx",
                    "sheets": sheet_count,
                    "populated_sheets": populated_sheets,
                    "sections": len(sections),
                },
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(
                PARSER_CORRUPT,
                "Excel 解析失败",
                detail={"detected_mime": mime, "reason": "xlsx-read-error"},
            ) from exc
        finally:
            if workbook is not None:
                workbook.close()


class PptxParser:
    """Extract text, tables and readable speaker notes from OOXML slides."""

    parser_id = "pptx"
    parser_version = "pptx-v1.0"
    _mimes = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    def supports(self, mime: str) -> bool:
        return mime in self._mimes

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        if not raw_bytes:
            raise ParserError(
                FILE_EMPTY,
                "文件内容为空",
                detail={"detected_mime": mime, "byte_size": 0},
            )
        try:
            from pptx import Presentation
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ParserError(
                MIME_UNSUPPORTED,
                "演示文稿解析依赖不可用",
                detail={"detected_mime": mime, "reason": "dependency-missing"},
            ) from exc

        try:
            presentation = Presentation(BytesIO(raw_bytes))
            sections: list[ParsedSection] = []
            slide_count = len(presentation.slides)
            for slide_index, slide in enumerate(presentation.slides, start=1):
                shape_lines, text_shape_count, table_shape_count = _extract_slide_shapes(slide)
                notes_text = _extract_slide_notes(slide)
                if notes_text:
                    shape_lines.append(("备注", notes_text))
                if not shape_lines:
                    continue

                title = _slide_title(slide) or f"幻灯片 {slide_index}"
                sections.append(
                    ParsedSection(
                        section_path=[filename, f"第 {slide_index} 页", title],
                        content="\n".join(
                            f"{label}: {text}" for label, text in shape_lines
                        ),
                        meta={
                            "format": "pptx",
                            "slide": slide_index,
                            "shape_count": len(slide.shapes),
                            "text_shape_count": text_shape_count,
                            "table_shape_count": table_shape_count,
                            "has_notes": bool(notes_text),
                        },
                    )
                )

            if not sections:
                raise ParserError(
                    PARSER_CORRUPT,
                    "演示文稿未提取到文本",
                    detail={"detected_mime": mime, "reason": "no-text-content"},
                )
            return ParseResult(
                sections=sections,
                metadata={
                    "format": "pptx",
                    "slides": slide_count,
                    "populated_slides": len(sections),
                    "sections": len(sections),
                },
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(
                PARSER_CORRUPT,
                "演示文稿解析失败",
                detail={"detected_mime": mime, "reason": "pptx-read-error"},
            ) from exc


def _render_office_value(value: object) -> str:
    """Render a cell value without putting it in an error or metadata field."""

    if isinstance(value, (bytes, bytearray)):
        return f"<binary:{len(value)} bytes>"
    return str(value).strip()


def _extract_slide_shapes(slide: object) -> tuple[list[tuple[str, str]], int, int]:
    lines: list[tuple[str, str]] = []
    text_shape_count = 0
    table_shape_count = 0

    def visit(shape: object) -> None:
        nonlocal text_shape_count, table_shape_count
        has_table = bool(getattr(shape, "has_table", False))
        if has_table:
            table_shape_count += 1
            table = shape.table  # type: ignore[attr-defined]
            for row_index, row in enumerate(table.rows, start=1):
                values = []
                for column_index, cell in enumerate(row.cells, start=1):
                    cell_text = cell.text.strip()
                    if cell_text:
                        values.append(f"R{row_index}C{column_index}={cell_text}")
                if values:
                    lines.append(("表格", " | ".join(values)))
        elif bool(getattr(shape, "has_text_frame", False)):
            text = shape.text_frame.text.strip()  # type: ignore[attr-defined]
            if text:
                text_shape_count += 1
                lines.append((getattr(shape, "name", "文本框"), text))

        nested_shapes = getattr(shape, "shapes", None)
        if nested_shapes is not None:
            for child in nested_shapes:
                visit(child)

    for shape in slide.shapes:  # type: ignore[attr-defined]
        visit(shape)
    return lines, text_shape_count, table_shape_count


def _extract_slide_notes(slide: object) -> str:
    """Read notes only through python-pptx's safe text-frame projection."""

    try:
        notes_frame = slide.notes_slide.notes_text_frame  # type: ignore[attr-defined]
        return notes_frame.text.strip()
    except Exception:
        # Notes are optional.  A malformed/unavailable notes part must not
        # expose a library exception or discard otherwise valid slide text.
        return ""


def _slide_title(slide: object) -> str:
    try:
        title_shape = slide.shapes.title  # type: ignore[attr-defined]
        if title_shape is not None and bool(getattr(title_shape, "has_text_frame", False)):
            return title_shape.text_frame.text.strip()  # type: ignore[attr-defined]
    except Exception:
        return ""
    return ""


class ParserRegistry:
    """Resolves a MIME type to a concrete parser implementation."""

    def __init__(self) -> None:
        self._parsers: list[Parser] = []
        self.register(TxtParser())
        self.register(PdfParser())
        self.register(DocxParser())
        self.register(XlsxParser())
        self.register(PptxParser())

    def register(self, parser: Parser) -> None:
        self._parsers.insert(0, parser)

    def resolve(self, mime: str) -> Parser:
        for parser in self._parsers:
            try:
                if parser.supports(mime):
                    return parser
            except Exception:  # pragma: no cover - defensive
                continue
        raise ParserError(
            MIME_UNSUPPORTED,
            f"不支持的文件类型: {mime}",
            detail={"detected_mime": mime},
        )

    def parse(self, raw_bytes: bytes, *, filename: str, mime: str) -> ParseResult:
        return self.resolve(mime).parse(raw_bytes, filename=filename, mime=mime)


_DEFAULT_REGISTRY = ParserRegistry()


def get_default_registry() -> ParserRegistry:
    return _DEFAULT_REGISTRY


# Heading/section splitter shared by text parsing (kept importable for tests).
_HEADING_RE = re.compile(r"^\s*#+\s")
