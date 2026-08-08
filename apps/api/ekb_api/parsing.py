"""M1-04 文档解析器：按 MIME 类型分发到真实解析库，保留结构化内容。

支持格式：PDF、Word(.docx)、Excel(.xlsx)、PPT(.pptx)、Markdown、TXT。
解析失败抛出 ParseError，由上层捕获后标记文档 FAILED。

解析器版本随切分策略版本一起记录到 document，用于版本追踪和回归对比。
"""

from __future__ import annotations

from dataclasses import dataclass

PARSER_VERSION = "parser-v1.0"
CHUNK_STRATEGY_VERSION = "chunk-v1.0"


class ParseError(Exception):
    """文档解析失败。message 会写入 document.failure_reason。"""


@dataclass
class ParsedSection:
    """解析产出的结构化段落：标题层级 + 正文。"""

    section_path: list[str]
    content: str


def parse(raw_bytes: bytes, mime_type: str, filename: str) -> list[ParsedSection]:
    """按 MIME 类型分发到对应解析器，返回结构化段落列表。"""
    if mime_type.startswith("text/"):
        return _parse_text(raw_bytes, filename)
    if mime_type == "application/pdf":
        return _parse_pdf(raw_bytes, filename)
    if mime_type in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",):
        return _parse_docx(raw_bytes, filename)
    if mime_type in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",):
        return _parse_xlsx(raw_bytes, filename)
    if mime_type in ("application/vnd.openxmlformats-officedocument.presentationml.presentation",):
        return _parse_pptx(raw_bytes, filename)
    # .doc/.ppt/.xls 旧格式二进制解析器较重，M1 暂不支持，提示用户转新格式。
    legacy_types = (
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
    )
    if mime_type in legacy_types:
        raise ParseError(f"暂不支持旧格式 {mime_type}，请转换为 .docx/.pptx/.xlsx 后重试。")
    raise ParseError(f"不支持的文件类型: mime={mime_type}, filename={filename}")


def _parse_text(raw_bytes: bytes, filename: str) -> list[ParsedSection]:
    """Markdown/TXT：按标题行（# 或空行分隔）切分段落。"""
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        raise ParseError("文件内容为空")
    sections: list[ParsedSection] = []
    current_path: list[str] = []
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_lines:
                content = "\n".join(current_lines).strip()
                sections.append(ParsedSection(section_path=list(current_path), content=content))
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            current_path = current_path[: level - 1] + [title] if level > 0 else [title]
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(ParsedSection(section_path=list(current_path), content=content))

    if not sections:
        sections.append(ParsedSection(section_path=[filename], content=text))
    return sections


def _parse_pdf(raw_bytes: bytes, filename: str) -> list[ParsedSection]:
    """PDF：按页提取文本，保留页码。"""
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ParseError(f"PDF 解析失败: {exc}") from exc

    sections: list[ParsedSection] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            sections.append(ParsedSection(section_path=[filename, f"第 {index} 页"], content=text))
    if not sections:
        raise ParseError("PDF 未提取到文本，可能是扫描件（需 OCR，M1 暂不支持）")
    return sections


def _parse_docx(raw_bytes: bytes, filename: str) -> list[ParsedSection]:
    """Word(.docx)：按标题样式和段落提取，保留标题层级。"""
    import io

    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ParseError(f"Word 解析失败: {exc}") from exc

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
                content = "\n".join(current_lines).strip()
                sections.append(ParsedSection(section_path=list(current_path), content=content))
                current_lines = []
            current_path = current_path[:1] + [text]
        else:
            current_lines.append(text)

    if current_lines:
        sections.append(
            ParsedSection(section_path=list(current_path), content="\n".join(current_lines).strip())
        )

    if not sections:
        raise ParseError("Word 文档内容为空")
    return sections


def _parse_xlsx(raw_bytes: bytes, filename: str) -> list[ParsedSection]:
    """Excel(.xlsx)：按工作表提取，表头 + 行块。"""
    import io

    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"Excel 解析失败: {exc}") from exc

    sections: list[ParsedSection] = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(cell) if cell is not None else "" for cell in rows[0]]
        lines = [
            ", ".join(f"{h}={v}" for h, v in zip(header, row) if v is not None) for row in rows[1:]
        ]
        content = "表头: " + ", ".join(header) + "\n" + "\n".join(lines)
        sections.append(ParsedSection(section_path=[filename, ws.title], content=content.strip()))
    wb.close()
    if not sections:
        raise ParseError("Excel 工作表为空")
    return sections


def _parse_pptx(raw_bytes: bytes, filename: str) -> list[ParsedSection]:
    """PPT(.pptx)：按幻灯片提取标题 + 正文文本。"""
    import io

    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ParseError(f"PPT 解析失败: {exc}") from exc

    sections: list[ParsedSection] = []
    for index, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        title = ""
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    texts.append(text)
            if shape == slide.shapes.title and shape.text_frame.text.strip():
                title = shape.text_frame.text.strip()
        if texts:
            section_title = title or f"幻灯片 {index}"
            sections.append(
                ParsedSection(
                    section_path=[filename, f"第 {index} 页", section_title],
                    content="\n".join(texts),
                )
            )
    if not sections:
        raise ParseError("PPT 未提取到文本")
    return sections
