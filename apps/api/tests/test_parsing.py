"""M1-04 文档解析器单元测试：覆盖 parse() 分发、_parse_text 切分、ParsedSection、常量。"""

from __future__ import annotations

import pytest

from ekb_api.parsing import (
    CHUNK_STRATEGY_VERSION,
    PARSER_VERSION,
    ParsedSection,
    ParseError,
    _parse_text,
    parse,
)

# ---------------------------------------------------------------------------
# 1. _parse_text：Markdown 标题层级与 section_path 继承
# ---------------------------------------------------------------------------

def test_parse_text_heading_levels() -> None:
    md = "# Chapter\nchapter content\n## Section\nsection content"
    sections = _parse_text(md.encode(), "doc.md")
    assert len(sections) == 2
    assert sections[0].section_path == ["Chapter"]
    assert sections[0].content == "chapter content"
    assert sections[1].section_path == ["Chapter", "Section"]
    assert sections[1].content == "section content"


def test_parse_text_section_path_reset_on_same_level() -> None:
    """同级标题应替换路径中该层，不继续叠加。"""
    md = "# Chapter1\ncontent1\n# Chapter2\ncontent2"
    sections = _parse_text(md.encode(), "doc.md")
    assert sections[0].section_path == ["Chapter1"]
    assert sections[1].section_path == ["Chapter2"]


def test_parse_text_deep_nesting() -> None:
    """三级标题路径应完整保留。"""
    md = "# H1\n## H2\n### H3\nleaf content"
    sections = _parse_text(md.encode(), "doc.md")
    assert sections[-1].section_path == ["H1", "H2", "H3"]
    assert sections[-1].content == "leaf content"


# ---------------------------------------------------------------------------
# 2. _parse_text：无标题纯文本 → 单个 section，section_path=[filename]
# ---------------------------------------------------------------------------

def test_parse_text_no_heading_returns_single_section() -> None:
    """无标题纯文本：返回单个 section，section_path 为空列表（无层级信息）。"""
    content = "plain text without any heading"
    sections = _parse_text(content.encode(), "readme.txt")
    assert len(sections) == 1
    assert sections[0].section_path == []
    assert sections[0].content == content


# ---------------------------------------------------------------------------
# 3. _parse_text：空内容抛 ParseError
# ---------------------------------------------------------------------------

def test_parse_text_empty_bytes_raises() -> None:
    with pytest.raises(ParseError):
        _parse_text(b"", "empty.txt")


def test_parse_text_whitespace_only_raises() -> None:
    with pytest.raises(ParseError):
        _parse_text(b"   \n\n   ", "blank.txt")


# ---------------------------------------------------------------------------
# 4. parse() 分发：text/* → _parse_text
# ---------------------------------------------------------------------------

def test_parse_dispatches_text_plain() -> None:
    content = "# Title\nhello world"
    sections = parse(content.encode(), "text/plain", "file.txt")
    assert isinstance(sections, list)
    assert all(isinstance(s, ParsedSection) for s in sections)


def test_parse_dispatches_text_markdown() -> None:
    content = "# Heading\nsome content here"
    sections = parse(content.encode(), "text/markdown", "file.md")
    assert len(sections) >= 1
    assert sections[0].section_path == ["Heading"]


# ---------------------------------------------------------------------------
# 5. parse()：不支持的 mime_type 抛 ParseError
# ---------------------------------------------------------------------------

def test_parse_unsupported_mime_raises() -> None:
    with pytest.raises(ParseError, match="不支持的文件类型"):
        parse(b"data", "image/png", "photo.png")


def test_parse_unsupported_mime_unknown_raises() -> None:
    with pytest.raises(ParseError):
        parse(b"data", "application/unknown", "file.bin")


# ---------------------------------------------------------------------------
# 6. parse()：旧格式（msword/ms-powerpoint/ms-excel）抛 ParseError 且消息含"转换"
# ---------------------------------------------------------------------------

def test_parse_legacy_msword_raises_with_convert_hint() -> None:
    with pytest.raises(ParseError, match="转换"):
        parse(b"data", "application/msword", "old.doc")


def test_parse_legacy_mspowerpoint_raises_with_convert_hint() -> None:
    with pytest.raises(ParseError, match="转换"):
        parse(b"data", "application/vnd.ms-powerpoint", "old.ppt")


def test_parse_legacy_msexcel_raises_with_convert_hint() -> None:
    with pytest.raises(ParseError, match="转换"):
        parse(b"data", "application/vnd.ms-excel", "old.xls")


# ---------------------------------------------------------------------------
# 7. ParsedSection dataclass：字段正确赋值
# ---------------------------------------------------------------------------

def test_parsed_section_fields() -> None:
    path = ["doc", "chapter1"]
    content = "some paragraph text"
    section = ParsedSection(section_path=path, content=content)
    assert section.section_path == path
    assert section.content == content


def test_parsed_section_section_path_is_list() -> None:
    section = ParsedSection(section_path=["a", "b"], content="text")
    assert isinstance(section.section_path, list)


# ---------------------------------------------------------------------------
# 8. 版本常量
# ---------------------------------------------------------------------------

def test_parser_version_constant() -> None:
    assert PARSER_VERSION == "parser-v1.0"


def test_chunk_strategy_version_constant() -> None:
    assert CHUNK_STRATEGY_VERSION == "chunk-v1.0"
