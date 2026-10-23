import io
import subprocess
import zipfile
from pathlib import Path

import fitz
import openpyxl
import pytest

from app.core.config import Settings
from app.modules.documents.service import MIME_BY_EXTENSION, OLE_MAGIC, OLE_REQUIRED_STREAM, _validate_file
from app.workers import document_pipeline
from app.workers.document_pipeline import convert_legacy_office, extract_document, split_chunks


def _settings(**overrides) -> Settings:
    values = {
        "environment": "test",
        "secret_key": "test-secret-key-that-is-long-enough",
        "embedding_provider": "local",
        "milvus_enabled": False,
        "llm_provider": "extractive",
    }
    values.update(overrides)
    return Settings(**values)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_upload_defaults_cover_common_document_formats():
    settings = _settings()
    assert settings.upload_extensions == [
        "pdf", "doc", "docx", "docm", "rtf", "xls", "xlsx", "xlsm", "xlsb", "csv",
        "ppt", "pptx", "pptm", "odt", "ods", "odp", "txt", "md",
    ]
    assert MIME_BY_EXTENSION["xlsx"].endswith("spreadsheetml.sheet")
    assert MIME_BY_EXTENSION["pptx"].endswith("presentationml.presentation")


@pytest.mark.parametrize("extension", ["doc", "xls", "ppt"])
def test_legacy_binary_office_uploads_are_verified_by_compound_stream(extension):
    marker = OLE_REQUIRED_STREAM[extension][0].encode("utf-16le")
    content = OLE_MAGIC + (b"\x00" * 128) + marker
    safe_name, mime_type = _validate_file(f"legacy.{extension}", content, _settings())
    assert safe_name == f"legacy.{extension}"
    assert mime_type == MIME_BY_EXTENSION[extension]


@pytest.mark.parametrize(
    ("extension", "required_member"),
    [("docm", "word/document.xml"), ("xlsm", "xl/workbook.xml"), ("xlsb", "xl/workbook.bin"), ("pptm", "ppt/presentation.xml")],
)
def test_macro_enabled_and_binary_modern_office_packages_are_accepted(extension, required_member):
    content = _zip_bytes({required_member: b"document-content"})
    _, mime_type = _validate_file(f"modern.{extension}", content, _settings())
    assert mime_type == MIME_BY_EXTENSION[extension]


@pytest.mark.parametrize("extension", ["odt", "ods", "odp"])
def test_open_document_packages_are_accepted(extension):
    content = _zip_bytes(
        {
            "mimetype": MIME_BY_EXTENSION[extension].encode("ascii"),
            "content.xml": b"<office-document />",
        }
    )
    _, mime_type = _validate_file(f"open.{extension}", content, _settings())
    assert mime_type == MIME_BY_EXTENSION[extension]


def test_rtf_is_accepted_for_word_compatibility():
    _, mime_type = _validate_file("document.rtf", b"{\\rtf1\\ansi document}", _settings())
    assert mime_type == MIME_BY_EXTENSION["rtf"]


@pytest.mark.parametrize(
    ("extension", "target_extension"),
    [("doc", "docx"), ("rtf", "docx"), ("xls", "xlsx"), ("xlsb", "xlsx"), ("ppt", "pptx"), ("odt", "docx"), ("ods", "xlsx"), ("odp", "pptx")],
)
def test_legacy_and_open_office_formats_convert_with_libreoffice(
    monkeypatch,
    extension,
    target_extension,
):
    expected_content = f"converted-{target_extension}".encode()

    def fake_run(command, **kwargs):
        output_directory = Path(command[command.index("--outdir") + 1])
        (output_directory / f"source.{target_extension}").write_bytes(expected_content)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(document_pipeline.subprocess, "run", fake_run)
    converted_name, converted_content = convert_legacy_office(
        f"document.{extension}", b"legacy-content", _settings()
    )
    assert converted_name == f"document.{target_extension}"
    assert converted_content == expected_content


def test_legacy_xls_conversion_reuses_structured_excel_splitting(monkeypatch):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "旧版名单"
    worksheet.append(["学号", "姓名"])
    worksheet.append(["20260001", "李明"])
    modern = io.BytesIO()
    workbook.save(modern)

    def fake_run(command, **kwargs):
        output_directory = Path(command[command.index("--outdir") + 1])
        (output_directory / "source.xlsx").write_bytes(modern.getvalue())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(document_pipeline.subprocess, "run", fake_run)
    units = extract_document(
        "旧版名单.xls",
        b"legacy-xls",
        settings=_settings(),
        ocr_enabled=False,
        chunk_size=500,
    )
    assert units[0].section.startswith("工作表：旧版名单")
    assert "学号: 20260001" in units[0].text


def test_excel_is_split_by_sheet_and_row_ranges_before_vector_chunking():
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "奖学金名单"
    worksheet.append(["学号", "姓名", "等级"])
    for index in range(1, 13):
        worksheet.append([f"2026{index:04d}", f"学生{index}", "一等奖" if index % 2 else "二等奖"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    content = buffer.getvalue()
    safe_name, mime_type = _validate_file("名单.xlsx", content, _settings())
    assert safe_name == "名单.xlsx"
    assert mime_type == MIME_BY_EXTENSION["xlsx"]

    units = extract_document(
        safe_name,
        content,
        settings=_settings(),
        ocr_enabled=False,
        chunk_size=180,
    )
    assert len(units) > 1
    assert all(unit.section and "工作表：奖学金名单" in unit.section for unit in units)
    assert "学号: 20260001" in units[0].text
    chunks = split_chunks(units, chunk_size=180, overlap=20)
    assert chunks[0]["section"].startswith("工作表：奖学金名单")


def test_word_document_text_is_extracted():
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>学生手册</w:t></w:r></w:p><w:p><w:r><w:t>请按时注册。</w:t></w:r></w:p></w:body>
    </w:document>""".encode()
    docx = _zip_bytes({"word/document.xml": document_xml})
    _, mime_type = _validate_file("学生手册.docx", docx, _settings())
    assert mime_type == MIME_BY_EXTENSION["docx"]
    units = extract_document(
        "学生手册.docx",
        docx,
        settings=_settings(),
        ocr_enabled=False,
        chunk_size=500,
    )
    assert units[0].text == "学生手册\n请按时注册。"


def test_ppt_images_are_ocrd_with_qwen_before_chunking(monkeypatch):
    slide_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>迎新安排</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>""".encode()
    rels_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
    </Relationships>"""
    pptx = _zip_bytes(
        {
            "ppt/presentation.xml": b"<presentation />",
            "ppt/slides/slide1.xml": slide_xml,
            "ppt/slides/_rels/slide1.xml.rels": rels_xml,
            "ppt/media/image1.png": b"not-a-real-png-but-qwen-receives-the-bytes",
        }
    )
    _, mime_type = _validate_file("迎新.pptx", pptx, _settings())
    assert mime_type == MIME_BY_EXTENSION["pptx"]
    calls = []

    def fake_ocr(content, mime_type, settings):
        calls.append((content, mime_type, settings.qwen_ocr_model))
        return "报到时间：9 月 1 日"

    monkeypatch.setattr(document_pipeline, "ocr_image", fake_ocr)
    units = extract_document(
        "迎新.pptx",
        pptx,
        settings=_settings(),
        ocr_enabled=True,
        chunk_size=500,
    )
    assert len(calls) == 1
    assert calls[0][1:] == ("image/png", "qwen-vl-ocr")
    assert "迎新安排" in units[0].text
    assert "[图片 OCR]" in units[0].text
    assert "报到时间：9 月 1 日" in units[0].text


def test_pdf_embedded_images_are_ocrd_and_merged_with_page_text(monkeypatch):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
    image.clear_with(255)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Native page text")
    page.insert_image(fitz.Rect(72, 100, 172, 200), stream=image.tobytes("png"))
    pdf = document.tobytes()
    document.close()

    monkeypatch.setattr(document_pipeline, "ocr_image", lambda *_: "Image notice 2026")
    units = extract_document(
        "notice.pdf",
        pdf,
        settings=_settings(),
        ocr_enabled=True,
        chunk_size=500,
    )
    assert units[0].page_number == 1
    assert "Native page text" in units[0].text
    assert "Image notice 2026" in units[0].text


def test_markdown_sections_are_preserved_as_chunk_sections():
    units = extract_document(
        "guide.md",
        "# 申请条件\n成绩合格\n\n## 所需材料\n成绩单".encode(),
        settings=_settings(),
        ocr_enabled=False,
        chunk_size=500,
    )
    chunks = split_chunks(units, chunk_size=500, overlap=20)
    assert [chunk["section"] for chunk in chunks] == ["申请条件", "所需材料"]
