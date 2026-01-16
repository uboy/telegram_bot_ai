"""
Загрузчик для PDF файлов
"""
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally


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
                        chunks.append({
                            "content": part,
                            "title": title,
                            "metadata": {"type": "pdf", "page": i + 1},
                        })
            
            return chunks if chunks else [
                {"content": "PDF файл пуст", "title": "", "metadata": {"type": "pdf"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека PyPDF2 не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки PDF: {str(e)}", 'title': '', 'metadata': {}}]

