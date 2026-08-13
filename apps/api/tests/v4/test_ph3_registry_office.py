"""Real OOXML parser coverage for the PH3 parser registry."""

from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from ekb_api.services.parsers.registry import (
    CONVERSION_FAILED,
    FILE_EMPTY,
    PARSER_CORRUPT,
    DocxParser,
    ParsedSection,
    ParserError,
    ParseResult,
    ParserRegistry,
)

DOC_MIME = "application/msword"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
LEGACY_MIMES = {
    DOC_MIME: ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.ms-excel": (
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "application/vnd.ms-powerpoint": (
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
}


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("旧版文档", level=1)
    document.add_paragraph("转换后由现有 DOCX parser 读取")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "销售数据"
    sheet.append(["客户", "金额"])
    sheet.append(["甲公司", 1200])
    second_sheet = workbook.create_sheet("说明")
    second_sheet["A1"] = "本表由业务团队维护"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _pptx_bytes() -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "季度报告"
    slide.placeholders[1].text = "收入保持增长"
    slide.notes_slide.notes_text_frame.text = "演讲者备注"

    table_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    table = table_slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(5), Inches(2)).table
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "数值"
    table.cell(1, 0).text = "订单"
    table.cell(1, 1).text = "42"

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def test_registry_resolves_and_parses_real_xlsx() -> None:
    result = ParserRegistry().parse(_xlsx_bytes(), filename="sales.xlsx", mime=XLSX_MIME)

    assert result.metadata == {
        "format": "xlsx",
        "sheets": 2,
        "populated_sheets": 2,
        "sections": 3,
    }
    assert any("B2=1200" in section.content for section in result.sections)
    sales_row = next(
        section
        for section in result.sections
        if section.meta.get("sheet") == "销售数据" and section.meta.get("row") == 2
    )
    assert sales_row.meta["row"] == 2
    assert sales_row.meta["cell_addresses"] == ["A2", "B2"]
    assert sales_row.section_path == ["sales.xlsx", "销售数据", "第 2 行"]


def test_registry_resolves_and_parses_real_pptx_with_notes_and_tables() -> None:
    registry = ParserRegistry()
    parser = registry.resolve(PPTX_MIME)
    assert parser.parser_id == "pptx"

    result = registry.parse(_pptx_bytes(), filename="quarterly.pptx", mime=PPTX_MIME)

    assert result.metadata["format"] == "pptx"
    assert result.metadata["slides"] == 2
    assert result.metadata["populated_slides"] == 2
    assert any("演讲者备注" in section.content for section in result.sections)
    table_section = next(
        section for section in result.sections if section.meta["table_shape_count"]
    )
    assert "R2C2=42" in table_section.content
    assert table_section.meta["slide"] == 2


@pytest.mark.parametrize(
    ("mime", "filename"),
    [(XLSX_MIME, "broken.xlsx"), (PPTX_MIME, "broken.pptx")],
)
def test_corrupt_ooxml_is_stable_and_does_not_leak_library_errors(
    mime: str, filename: str
) -> None:
    with pytest.raises(ParserError) as caught:
        ParserRegistry().parse(b"not-an-ooxml-document", filename=filename, mime=mime)

    error = caught.value
    assert error.code == PARSER_CORRUPT
    assert error.to_sanitized_error()["detail"]["reason"] in {
        "xlsx-read-error",
        "pptx-read-error",
    }
    assert "BadZipFile" not in error.message
    assert "not-an-ooxml-document" not in error.message


@pytest.mark.parametrize(
    "mime", [XLSX_MIME, PPTX_MIME]
)
def test_empty_ooxml_is_file_empty(mime: str) -> None:
    with pytest.raises(ParserError) as caught:
        ParserRegistry().parse(b"", filename="empty.office", mime=mime)
    assert caught.value.code == FILE_EMPTY


@pytest.mark.parametrize("mime", LEGACY_MIMES)
def test_registry_resolves_legacy_office_mimes(mime: str) -> None:
    parser = ParserRegistry().resolve(mime)
    assert parser.parser_id == "legacy-office"
    assert parser.supports(mime)


@pytest.mark.parametrize(
    ("reason", "failure"),
    [
        ("soffice-not-found", FileNotFoundError("soffice")),
        (
            "conversion-timeout",
            subprocess.TimeoutExpired(cmd=["soffice"], timeout=30, output=b"secret"),
        ),
        (
            "conversion-nonzero-exit",
            subprocess.CompletedProcess(["soffice"], 1, stdout=b"secret", stderr=b"secret"),
        ),
    ],
)
def test_legacy_conversion_failures_are_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch, reason: str, failure: BaseException
) -> None:
    def fail_run(*args: object, **kwargs: object) -> object:
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(ParserError) as caught:
        ParserRegistry().parse(
            b"legacy-secret-content",
            filename="../../private/secret.doc",
            mime=DOC_MIME,
        )
    error = caught.value
    assert error.code == CONVERSION_FAILED
    assert error.to_sanitized_error()["detail"]["reason"] == reason
    assert set(error.detail) <= {
        "reason",
        "detected_mime",
        "parser_id",
        "parser_version",
        "byte_size",
    }
    assert "legacy-secret-content" not in error.message
    assert "secret.doc" not in str(error.detail)


def test_legacy_conversion_calls_target_parser_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, "kwargs": kwargs})
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "quarterly.docx").write_bytes(b"converted-ooxml")
        return subprocess.CompletedProcess(command, 0, stdout="source", stderr="")

    def fake_docx_parse(
        self: DocxParser, raw_bytes: bytes, *, filename: str, mime: str
    ) -> ParseResult:
        calls.append(
            {
                "target": self.parser_id,
                "bytes": raw_bytes,
                "filename": filename,
                "mime": mime,
            }
        )
        return ParseResult(
            sections=[ParsedSection(section_path=[filename], content="真实转换内容")],
            metadata={"format": "docx", "sections": 1},
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(DocxParser, "parse", fake_docx_parse)

    result = ParserRegistry().parse(
        b"legacy-secret-content", filename="../../quarterly.doc", mime=DOC_MIME
    )

    assert result.metadata == {
        "format": "legacy-converted",
        "sections": 1,
        "source_mime": DOC_MIME,
        "converter": "libreoffice",
        "target_mime": LEGACY_MIMES[DOC_MIME][1],
    }
    assert result.sections[0].content == "真实转换内容"
    target_call = next(call for call in calls if call.get("target") == "docx")
    assert target_call["bytes"] == b"converted-ooxml"
    assert target_call["filename"] == "quarterly.doc"
    assert target_call["mime"] == LEGACY_MIMES[DOC_MIME][1]

    process_call = next(call for call in calls if "command" in call)
    command = process_call["command"]
    kwargs = process_call["kwargs"]
    assert isinstance(command, list)
    assert command[0] == "soffice"
    assert "--safe-mode" in command
    assert command[command.index("--convert-to") + 1] == "docx"
    assert Path(command[-1]).name == "quarterly.doc"
    assert Path(command[-1]).parent.name == "input"
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 30


def test_legacy_target_parser_error_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "broken.docx").write_bytes(b"not-an-ooxml-document")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ParserError) as caught:
        ParserRegistry().parse(b"legacy-bytes", filename="broken.doc", mime=DOC_MIME)
    assert caught.value.code == PARSER_CORRUPT
    assert caught.value.to_sanitized_error()["detail"]["reason"] == "docx-read-error"
