from __future__ import annotations

import csv
import hashlib
import io
import mimetypes
import posixpath
import re
import subprocess
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.security import utcnow
from app.db.session import SessionLocal
from app.integrations.embedding import EmbeddingProviderError, embed_texts
from app.integrations.ocr import ocr_image
from app.integrations.storage import get_storage
from app.modules.documents.models import Document, DocumentAclEntry, DocumentChunk, DocumentVersion
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.tasks.models import ProcessingTask, VectorIndexOutbox
from app.modules.tasks.service import get_task_or_404, update_task


@dataclass(frozen=True)
class ExtractedUnit:
    page_number: int | None
    text: str
    section: str | None = None


LEGACY_OFFICE_TARGETS = {
    "doc": "docx",
    "rtf": "docx",
    "xls": "xlsx",
    "xlsb": "xlsx",
    "ppt": "pptx",
    "odt": "docx",
    "ods": "xlsx",
    "odp": "pptx",
}

LIBREOFFICE_FILTERS = {
    "docx": "docx:Office Open XML Text",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
}


class DocumentConversionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def convert_legacy_office(file_name: str, content: bytes, settings: Settings) -> tuple[str, bytes]:
    extension = Path(file_name).suffix.lower().lstrip(".")
    target_extension = LEGACY_OFFICE_TARGETS.get(extension)
    if not target_extension:
        return file_name, content
    try:
        with tempfile.TemporaryDirectory(prefix="edurag-office-") as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / f"source.{extension}"
            output_directory = workspace / "converted"
            profile_directory = workspace / "profile"
            output_directory.mkdir()
            profile_directory.mkdir()
            source.write_bytes(content)
            command = [
                settings.libreoffice_binary,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile_directory.as_uri()}",
                "--convert-to",
                LIBREOFFICE_FILTERS[target_extension],
                "--outdir",
                str(output_directory),
                str(source),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=settings.office_conversion_timeout_seconds,
            )
            converted = output_directory / f"source.{target_extension}"
            if result.returncode != 0 or not converted.is_file() or converted.stat().st_size == 0:
                raise DocumentConversionError(
                    "OFFICE_CONVERSION_FAILED",
                    f"{extension.upper()} 文档无法转换，请确认文件未损坏或未加密",
                )
            converted_name = f"{Path(file_name).stem}.{target_extension}"
            return converted_name, converted.read_bytes()
    except FileNotFoundError as exc:
        raise DocumentConversionError(
            "OFFICE_CONVERTER_NOT_CONFIGURED",
            "旧版 Office 文档转换服务不可用，请安装 LibreOffice 或配置 LIBREOFFICE_BINARY",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentConversionError(
            "OFFICE_CONVERSION_TIMEOUT",
            f"{extension.upper()} 文档转换超时",
        ) from exc


class _OcrSession:
    def __init__(self, settings: Settings, enabled: bool) -> None:
        self.settings = settings
        self.enabled = enabled
        self._cache: dict[str, str] = {}

    def recognize(self, content: bytes, mime_type: str) -> str:
        if not self.enabled or not content:
            return ""
        digest = hashlib.sha256(content).hexdigest()
        if digest in self._cache:
            return self._cache[digest]
        if len(self._cache) >= self.settings.qwen_ocr_max_images_per_document:
            raise RuntimeError(
                f"文档图片数量超过 OCR 安全上限（{self.settings.qwen_ocr_max_images_per_document} 张）"
            )
        text = ocr_image(content, mime_type, self.settings).strip()
        self._cache[digest] = text
        return text


def _merge_text(native_text: str, image_texts: list[str]) -> str:
    parts: list[str] = []
    native_text = native_text.strip()
    if native_text:
        parts.append(native_text)
    unique: set[str] = set()
    for text in image_texts:
        normalized = text.strip()
        if not normalized or normalized in unique or normalized == native_text:
            continue
        unique.add(normalized)
        parts.append(f"[图片 OCR]\n{normalized}")
    return "\n\n".join(parts)


def _extract_pdf(content: bytes, ocr: _OcrSession | None = None) -> list[ExtractedUnit]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF 解析依赖 PyMuPDF 未安装") from exc
    try:
        units: list[ExtractedUnit] = []
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise RuntimeError("不支持加密 PDF")
            for index, page in enumerate(pdf):
                native_text = page.get_text("text")
                image_texts: list[str] = []
                image_count = 0
                if ocr and ocr.enabled:
                    seen_xrefs: set[int] = set()
                    for image_info in page.get_images(full=True):
                        xref = int(image_info[0])
                        width, height = int(image_info[2]), int(image_info[3])
                        if xref in seen_xrefs or width < 32 or height < 32:
                            continue
                        seen_xrefs.add(xref)
                        extracted = pdf.extract_image(xref)
                        image_bytes = extracted.get("image", b"")
                        extension = str(extracted.get("ext", "png")).lower()
                        mime_type = mimetypes.guess_type(f"image.{extension}")[0] or f"image/{extension}"
                        image_texts.append(ocr.recognize(image_bytes, mime_type))
                        image_count += 1
                    # Some scanned PDFs do not expose a reusable image object. Render
                    # the page so Qwen still sees the visual contents.
                    if not native_text.strip() and image_count == 0:
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        image_texts.append(ocr.recognize(pixmap.tobytes("png"), "image/png"))
                units.append(
                    ExtractedUnit(index + 1, _merge_text(native_text, image_texts), f"第 {index + 1} 页")
                )
        return units
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("PDF 文件损坏或无法解析") from exc


def _xml_paragraphs(xml: bytes, paragraph_tag: str, text_tag: str) -> list[str]:
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(paragraph_tag):
        text = "".join(node.text or "" for node in paragraph.iter(text_tag)).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _media_mime_type(name: str) -> str | None:
    mime_type = mimetypes.guess_type(name)[0]
    return mime_type if mime_type and mime_type.startswith("image/") else None


def _extract_docx(content: bytes, ocr: _OcrSession | None = None) -> list[ExtractedUnit]:
    try:
        units: list[ExtractedUnit] = []
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            paragraphs = _xml_paragraphs(
                archive.read("word/document.xml"), f"{namespace}p", f"{namespace}t"
            )
            units.append(ExtractedUnit(None, "\n".join(paragraphs), "正文"))
            if ocr and ocr.enabled:
                media = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
                for index, name in enumerate(media, 1):
                    mime_type = _media_mime_type(name)
                    if not mime_type:
                        continue
                    text = ocr.recognize(archive.read(name), mime_type)
                    if text:
                        units.append(ExtractedUnit(None, f"[图片 OCR]\n{text}", f"嵌入图片 {index}"))
        return units
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("DOCX 文件损坏或无法解析") from exc


def _slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _extract_pptx(content: bytes, ocr: _OcrSession | None = None) -> list[ExtractedUnit]:
    try:
        units: list[ExtractedUnit] = []
        namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            slide_names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ),
                key=_slide_number,
            )
            for slide_name in slide_names:
                number = _slide_number(slide_name)
                paragraphs = _xml_paragraphs(
                    archive.read(slide_name), f"{namespace}p", f"{namespace}t"
                )
                image_texts: list[str] = []
                rel_name = f"ppt/slides/_rels/slide{number}.xml.rels"
                if ocr and ocr.enabled and rel_name in archive.namelist():
                    rel_root = ElementTree.fromstring(archive.read(rel_name))
                    for relationship in rel_root:
                        if not str(relationship.attrib.get("Type", "")).endswith("/image"):
                            continue
                        target = relationship.attrib.get("Target", "")
                        media_name = posixpath.normpath(posixpath.join(posixpath.dirname(slide_name), target))
                        if media_name not in archive.namelist():
                            continue
                        mime_type = _media_mime_type(media_name)
                        if mime_type:
                            image_texts.append(ocr.recognize(archive.read(media_name), mime_type))
                native_text = "\n".join(paragraphs)
                title = paragraphs[0][:200] if paragraphs else f"幻灯片 {number}"
                units.append(
                    ExtractedUnit(number, _merge_text(native_text, image_texts), f"幻灯片 {number}：{title}")
                )
        return units
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("PPTX 文件损坏或无法解析") from exc


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value).strip()


def _table_units(
    sheet_name: str,
    rows: list[tuple[int, list[object]]],
    chunk_size: int,
) -> list[ExtractedUnit]:
    populated = [(number, [_format_cell(value) for value in values]) for number, values in rows]
    populated = [(number, values) for number, values in populated if any(values)]
    if not populated:
        return []
    header_number, headers = populated[0]
    normalized_headers = [header or f"列{index + 1}" for index, header in enumerate(headers)]
    data_rows = populated[1:]
    prefix = f"工作表：{sheet_name}\n列：{' | '.join(normalized_headers)}"
    if not data_rows:
        return [ExtractedUnit(None, prefix, f"工作表：{sheet_name}（第 {header_number} 行）")]

    units: list[ExtractedUnit] = []
    buffered: list[tuple[int, str]] = []
    buffered_length = len(prefix)

    def flush() -> None:
        nonlocal buffered, buffered_length
        if not buffered:
            return
        start, end = buffered[0][0], buffered[-1][0]
        text = prefix + "\n" + "\n".join(line for _, line in buffered)
        units.append(ExtractedUnit(None, text, f"工作表：{sheet_name}（第 {start}-{end} 行）"))
        buffered = []
        buffered_length = len(prefix)

    for row_number, values in data_rows:
        pairs = [
            f"{normalized_headers[index] if index < len(normalized_headers) else f'列{index + 1}'}: {value}"
            for index, value in enumerate(values)
            if value
        ]
        line = f"第 {row_number} 行：" + " | ".join(pairs)
        if buffered and buffered_length + len(line) + 1 > chunk_size:
            flush()
        buffered.append((row_number, line))
        buffered_length += len(line) + 1
    flush()
    return units


def _extract_xlsx(content: bytes, chunk_size: int) -> list[ExtractedUnit]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("XLSX 解析依赖 openpyxl 未安装") from exc
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        units: list[ExtractedUnit] = []
        for worksheet in workbook.worksheets:
            rows = [(index, list(row)) for index, row in enumerate(worksheet.iter_rows(values_only=True), 1)]
            units.extend(_table_units(worksheet.title, rows, chunk_size))
        workbook.close()
        return units
    except Exception as exc:
        raise RuntimeError("XLSX 文件损坏或无法解析") from exc


def _extract_csv(content: bytes, chunk_size: int) -> list[ExtractedUnit]:
    try:
        text = content.decode("utf-8-sig")
        rows = [(index, list(row)) for index, row in enumerate(csv.reader(io.StringIO(text)), 1)]
        return _table_units("CSV", rows, chunk_size)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError("CSV 文件损坏或无法解析") from exc


def _extract_markdown(content: bytes) -> list[ExtractedUnit]:
    text = content.decode("utf-8")
    units: list[ExtractedUnit] = []
    current_section = "正文"
    lines: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if heading and lines:
            units.append(ExtractedUnit(None, "\n".join(lines), current_section))
            lines = []
        if heading:
            current_section = heading.group(1).strip()
        lines.append(line)
    if lines:
        units.append(ExtractedUnit(None, "\n".join(lines), current_section))
    return units


def extract_document(
    file_name: str,
    content: bytes,
    *,
    settings: Settings,
    ocr_enabled: bool,
    chunk_size: int,
) -> list[ExtractedUnit]:
    extension = file_name.rsplit(".", 1)[-1].lower()
    if extension in LEGACY_OFFICE_TARGETS:
        file_name, content = convert_legacy_office(file_name, content, settings)
        extension = file_name.rsplit(".", 1)[-1].lower()
    ocr = _OcrSession(settings, ocr_enabled)
    if extension == "pdf":
        return _extract_pdf(content, ocr)
    if extension in {"docx", "docm"}:
        return _extract_docx(content, ocr)
    if extension in {"xlsx", "xlsm"}:
        return _extract_xlsx(content, chunk_size)
    if extension in {"pptx", "pptm"}:
        return _extract_pptx(content, ocr)
    if extension == "csv":
        return _extract_csv(content, chunk_size)
    if extension == "md":
        return _extract_markdown(content)
    if extension == "txt":
        return [ExtractedUnit(None, content.decode("utf-8"), "正文")]
    raise RuntimeError("不支持该文件格式")


def extract_pages(file_name: str, content: bytes) -> list[tuple[int | None, str]]:
    """Compatibility wrapper for callers that only need page/text pairs."""
    units = extract_document(
        file_name,
        content,
        settings=get_settings(),
        ocr_enabled=False,
        chunk_size=800,
    )
    return [(unit.page_number, unit.text) for unit in units]


def split_chunks(pages: list[ExtractedUnit | tuple[int | None, str]], chunk_size: int, overlap: int):
    chunks: list[dict[str, object]] = []
    for unit in pages:
        if isinstance(unit, ExtractedUnit):
            page_number, text, unit_section = unit.page_number, unit.text, unit.section
        else:
            page_number, text = unit
            unit_section = None
        normalized = re.sub(r"[ \t]+", " ", text).strip()
        if not normalized:
            continue
        cursor = 0
        while cursor < len(normalized):
            upper = min(len(normalized), cursor + chunk_size)
            if upper < len(normalized):
                boundary = max(normalized.rfind("\n", cursor, upper), normalized.rfind("。", cursor, upper))
                if boundary > cursor + chunk_size // 2:
                    upper = boundary + 1
            part = normalized[cursor:upper].strip()
            if part:
                heading = unit_section or (part.splitlines()[0][:200] if "\n" in part else None)
                chunks.append(
                    {
                        "content": part,
                        "page_start": page_number,
                        "page_end": page_number,
                        "char_start": cursor,
                        "char_end": upper,
                        "section": heading,
                    }
                )
            if upper >= len(normalized):
                break
            cursor = max(cursor + 1, upper - overlap)
    return chunks


def _entries_to_tokens(entries) -> set[str]:
    values: set[str] = set()
    for entry in entries:
        if not entry.can_search:
            continue
        if entry.is_public:
            values.add("public")
        elif entry.role_id:
            values.add(f"role:{entry.role_id}")
        elif entry.department_id:
            values.add(f"department:{entry.department_id}")
        elif entry.user_id:
            values.add(f"user:{entry.user_id}")
    return values


def _search_acl_tokens(db: Session, kb: KnowledgeBase, document: Document) -> list[str]:
    values: set[str] = {f"user:{kb.created_by}"}
    values.update(f"user:{owner.id}" for owner in kb.owners)
    values.update(_entries_to_tokens(kb.acl_entries))
    document_entries = db.scalars(
        select(DocumentAclEntry).where(DocumentAclEntry.document_id == document.id)
    ).all()
    if document_entries:
        # The KB id filter proves KB access; document tokens apply the narrowing layer.
        values = _entries_to_tokens(document_entries)
    return sorted(values)


def _load_objects(db: Session, task_id) -> tuple[ProcessingTask, DocumentVersion, Document, KnowledgeBase]:
    task = get_task_or_404(db, task_id)
    version = db.scalar(
        select(DocumentVersion)
        .options(selectinload(DocumentVersion.file), selectinload(DocumentVersion.document))
        .where(DocumentVersion.id == task.document_version_id)
    )
    if not version:
        raise RuntimeError("文档版本不存在")
    document = db.scalar(
        select(Document).where(Document.id == version.document_id, Document.deleted_at.is_(None))
    )
    if not document:
        raise RuntimeError("文档不存在或已删除")
    kb = db.scalar(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
        .where(KnowledgeBase.id == document.knowledge_base_id)
    )
    if not kb:
        raise RuntimeError("知识库不存在")
    return task, version, document, kb


def run_document_pipeline(task_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with SessionLocal() as db:
        task = None
        version = None
        document = None
        try:
            task, version, document, kb = _load_objects(db, uuid.UUID(task_id))
            if task.cancel_requested_at:
                update_task(db, task, status="cancelled", progress_text="任务已取消")
                db.commit()
                return
            update_task(db, task, status="running", stage="parsing", progress_percent=10, progress_text="正在解析文档")
            version.process_status = "processing"
            version.process_stage = "parsing"
            db.commit()

            with get_storage(settings).open(version.file.storage_key) as stream:
                content = stream.read()
            processing_name = version.file.original_name
            extension = Path(processing_name).suffix.lower().lstrip(".")
            if extension in LEGACY_OFFICE_TARGETS:
                update_task(
                    db,
                    task,
                    status="running",
                    stage="converting",
                    progress_percent=15,
                    progress_text=f"正在将 {extension.upper()} 转换为现代 Office 格式",
                )
                version.process_stage = "converting"
                db.commit()
                processing_name, content = convert_legacy_office(processing_name, content, settings)
            if version.ocr_enabled:
                update_task(
                    db,
                    task,
                    status="running",
                    stage="ocr",
                    progress_percent=20,
                    progress_text="正在使用 Qwen OCR 识别文档图片",
                )
                version.process_stage = "ocr"
                db.commit()
            pages = extract_document(
                processing_name,
                content,
                settings=settings,
                ocr_enabled=version.ocr_enabled,
                chunk_size=version.chunk_size,
            )
            chunks_data = split_chunks(pages, version.chunk_size, version.chunk_overlap)
            if not chunks_data:
                raise RuntimeError("文档中没有可用文本；图片或扫描件请启用 Qwen OCR")

            update_task(db, task, status="running", stage="chunking", progress_percent=35, progress_text="正在切分文本")
            version.process_stage = "chunking"
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            chunks: list[DocumentChunk] = []
            for index, item in enumerate(chunks_data):
                text = str(item["content"])
                chunk = DocumentChunk(
                    document_version_id=version.id,
                    chunk_index=index,
                    title=document.display_name,
                    section=item["section"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    char_start=item["char_start"],
                    char_end=item["char_end"],
                    content=text,
                    token_count=max(1, len(text) // 2),
                    search_tokens=text,
                    embedding_model=settings.embedding_model,
                    embedding_dimension=settings.embedding_dimension,
                    vector_status="pending",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                )
                db.add(chunk)
                chunks.append(chunk)
            db.flush()

            update_task(db, task, status="running", stage="embedding", progress_percent=55, progress_text="正在调用 Qwen 生成向量")
            version.process_stage = "embedding"
            db.commit()
            vectors = embed_texts([chunk.content for chunk in chunks], settings)

            collection = f"{settings.milvus_collection_prefix}_{re.sub(r'[^a-zA-Z0-9_]', '_', settings.embedding_model)}"
            tokens = _search_acl_tokens(db, kb, document)
            expires_at = int(datetime.combine(document.expires_at, datetime.min.time(), tzinfo=UTC).timestamp()) if document.expires_at else 0
            vector_rows = []
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk.vector_id = str(chunk.id)
                outbox = VectorIndexOutbox(
                    operation="upsert",
                    chunk_id=chunk.id,
                    document_version_id=version.id,
                    collection_name=collection,
                    payload={"contentHash": chunk.content_hash},
                )
                db.add(outbox)
                vector_rows.append(
                    {
                        "chunk_id": str(chunk.id),
                        "embedding": vector,
                        "knowledge_base_id": str(kb.id),
                        "document_id": str(document.id),
                        "document_version_id": str(version.id),
                        "is_active": True,
                        "expires_at": expires_at,
                        "search_acl_tokens": tokens,
                        "embedding_model": settings.embedding_model,
                    }
                )
            db.commit()

            update_task(db, task, status="running", stage="indexing", progress_percent=80, progress_text="正在发布 Milvus 索引")
            version.process_stage = "indexing"
            db.commit()
            if settings.milvus_enabled:
                from app.integrations.milvus import MilvusIndex

                MilvusIndex(settings).upsert(collection, vector_rows)
            for chunk in chunks:
                chunk.vector_status = "ready"
            for event in db.scalars(
                select(VectorIndexOutbox).where(
                    VectorIndexOutbox.document_version_id == version.id,
                    VectorIndexOutbox.status == "pending",
                )
            ):
                event.status = "processed"
            for old in document.versions:
                old.is_current = False
            version.is_current = True
            version.process_status = "ready"
            version.process_stage = "published"
            version.processed_at = utcnow()
            document.current_version_id = version.id
            document.status = "ready"
            document.enabled = True
            update_task(db, task, status="succeeded", stage="published", progress_percent=100, progress_text="文档已入库")
            db.commit()
        except Exception as exc:
            db.rollback()
            if task is not None:
                task = db.get(ProcessingTask, task.id)
                version = db.get(DocumentVersion, version.id) if version else None
                document = db.get(Document, document.id) if document else None
                code = getattr(exc, "code", "DOCUMENT_PROCESSING_FAILED")
                message = str(exc)[:1000] or "文档处理失败"
                if version:
                    version.process_status = "failed"
                    version.failure_code = code
                    version.failure_message = message
                if document and document.current_version_id is None:
                    document.status = "failed"
                update_task(db, task, status="failed", stage=task.stage, progress_text="文档处理失败", error_code=code, error_message=message)
                db.commit()


def dispatch_document_pipeline(task_id: str, settings: Settings) -> None:
    if settings.task_dispatch_mode == "inline":
        run_document_pipeline(task_id, settings)
        return
    process_document.delay(task_id)


try:
    from app.workers.celery_app import celery_app

    @celery_app.task(name="edurag.process_document", autoretry_for=(), max_retries=0)
    def process_document(task_id: str) -> None:
        run_document_pipeline(task_id)
except ImportError:
    class _UnavailableTask:
        def delay(self, task_id: str) -> None:
            raise RuntimeError("Celery 未安装，无法派发后台任务")

    process_document = _UnavailableTask()
