"""
Загрузчик для PDF файлов
"""
import os
import re
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally_with_metadata

_PDF_LIST_RE = re.compile(r'^\s*(?:\d+[\.\)]|[-*•])\s+')
_PDF_HEADING_RE = re.compile(r'^([IVX]{1,6}\.\s+[^\n]{3,120}|[A-ZА-Я][A-ZА-Я0-9\s,\-]{4,120}|(?:Раздел|Section)\s+\d+[:.\s].*)$')


def _detect_pdf_section(text: str) -> str:
    """Определить заголовок раздела/пункта в начале текста PDF-чанка."""
    t = text.strip()
    # Заголовок с римской цифрой: "I. Общие положения", "IV. Цели и задачи"
    m = re.match(r'^([IVX]{1,4}\.\s+[А-ЯA-Z][^\n]{3,80})', t)
    if m:
        return m.group(1).strip()
    # Номерной пункт в начале: "25. Целями развития..."
    m = re.match(r'^(\d+)\.\s', t)
    if m:
        return f"пункт {m.group(1)}"
    return ""


def _is_pdf_structural_line(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(_PDF_LIST_RE.match(stripped) or _PDF_HEADING_RE.match(stripped))


def _normalize_pdf_page_text(text: str) -> str:
    """Сжать шумные переносы строк PyPDF2, сохранив заголовки и списки как отдельные блоки."""
    lines = [line.strip() for line in (text or "").replace("\r", "").splitlines()]
    blocks: List[str] = []
    current = ""

    for line in lines:
        if not line:
            if current:
                blocks.append(current.strip())
                current = ""
            continue

        if _is_pdf_structural_line(line):
            if current:
                blocks.append(current.strip())
            current = line
            continue

        if not current:
            current = line
            continue

        if current.endswith("-"):
            current = current[:-1] + line
        else:
            current = current + " " + line

    if current:
        blocks.append(current.strip())

    return "\n\n".join(block for block in blocks if block).strip()


class PDFLoader(DocumentLoader):
    """Загрузчик для PDF файлов"""

    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить PDF файл с учетом структуры страниц (абзацы, списки)"""
        try:
            import PyPDF2
            chunks: List[Dict[str, str]] = []
            doc_title = os.path.splitext(os.path.basename(source))[0] or os.path.basename(source) or "PDF"
            global_chunk_no = 1

            max_chars = (options or {}).get("max_chars")
            overlap = (options or {}).get("overlap")
            try:
                max_chars = int(max_chars) if max_chars is not None else None
            except (ValueError, TypeError):
                max_chars = None
            try:
                overlap = int(overlap) if overlap is not None else None
            except (ValueError, TypeError):
                overlap = None

            with open(source, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                for i, page in enumerate(pdf_reader.pages):
                    raw_text = page.extract_text() or ""
                    text = _normalize_pdf_page_text(raw_text)
                    if not text:
                        continue

                    page_records = split_text_structurally_with_metadata(
                        text,
                        max_chars=max_chars,
                        overlap=overlap,
                    )
                    for page_chunk_no, record in enumerate(page_records, start=1):
                        part = record["content"]
                        section = _detect_pdf_section(part)
                        page_title = f"Страница {i + 1}"
                        title = section or page_title
                        if len(page_records) > 1 and not section:
                            title = f"{page_title} (фрагмент {page_chunk_no})"
                        section_path = f"{doc_title} > {section}" if section else f"{doc_title} > {page_title}"
                        metadata: Dict[str, object] = {
                            "type": "pdf",
                            "doc_title": doc_title,
                            "page": i + 1,
                            "page_no": i + 1,
                            "page_chunk_no": page_chunk_no,
                            "chunk_no": global_chunk_no,
                            "chunk_kind": record.get("chunk_kind") or "text",
                            "section_title": section or page_title,
                            "section_path": section_path,
                            "char_start": record.get("char_start"),
                            "char_end": record.get("char_end"),
                            "parser_profile": "loader:pdf:v2",
                        }
                        chunks.append({
                            "content": part,
                            "title": title,
                            "metadata": metadata,
                        })
                        global_chunk_no += 1
            
            return chunks if chunks else [
                {"content": "PDF файл пуст", "title": "", "metadata": {"type": "pdf", "doc_title": doc_title, "parser_profile": "loader:pdf:v2"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека PyPDF2 не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки PDF: {str(e)}", 'title': '', 'metadata': {}}]

