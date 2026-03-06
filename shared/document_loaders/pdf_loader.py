"""
Загрузчик для PDF файлов
"""
import re
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally


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


class PDFLoader(DocumentLoader):
    """Загрузчик для PDF файлов"""

    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить PDF файл с учетом структуры страниц (абзацы, списки)"""
        try:
            import PyPDF2
            chunks: List[Dict[str, str]] = []

            with open(source, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                for i, page in enumerate(pdf_reader.pages):
                    text = page.extract_text() or ""
                    text = text.strip()
                    if not text:
                        continue

                    # Используем структурный чанкинг для каждой страницы
                    page_chunks = split_text_structurally(text)
                    for idx, part in enumerate(page_chunks, start=1):
                        title = f"Страница {i + 1}"
                        if len(page_chunks) > 1:
                            title = f"{title} (фрагмент {idx})"
                        metadata: Dict[str, object] = {"type": "pdf", "page": i + 1}
                        section = _detect_pdf_section(part)
                        if section:
                            metadata["section_title"] = section
                        chunks.append({
                            "content": part,
                            "title": title,
                            "metadata": metadata,
                        })
            
            return chunks if chunks else [
                {"content": "PDF файл пуст", "title": "", "metadata": {"type": "pdf"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека PyPDF2 не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки PDF: {str(e)}", 'title': '', 'metadata': {}}]

