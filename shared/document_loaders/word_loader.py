"""
Загрузчик для Word файлов
"""
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally


class WordLoader(DocumentLoader):
    """Загрузчик для Word файлов"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить Word файл с учетом структуры (заголовки, абзацы, списки)"""
        try:
            from docx import Document
            chunks: List[Dict[str, str]] = []
            
            doc = Document(source)
            current_section = ""
            current_title = ""
            
            for para in doc.paragraphs:
                text = (para.text or "").strip()
                if not text:
                    if current_section:
                        current_section += "\n"
                    continue
                
                is_heading = para.style and para.style.name.startswith("Heading")
                
                if is_heading:
                    if current_section:
                        sec_chunks = split_text_structurally(current_section)
                        for idx, part in enumerate(sec_chunks, start=1):
                            title = current_title or ""
                            if len(sec_chunks) > 1:
                                title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                            chunks.append({
                                "content": part,
                                "title": title,
                                "metadata": {"type": "word", "section_title": current_title},
                            })
                    current_title = text
                    current_section = text + "\n"
                else:
                    current_section += text + "\n"
            
            if current_section:
                sec_chunks = split_text_structurally(current_section)
                for idx, part in enumerate(sec_chunks, start=1):
                    title = current_title or ""
                    if len(sec_chunks) > 1:
                        title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                    chunks.append({
                        "content": part,
                        "title": title,
                        "metadata": {"type": "word", "section_title": current_title},
                    })
            
            return chunks if chunks else [
                {"content": "Word файл пуст", "title": "", "metadata": {"type": "word"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека python-docx не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки Word: {str(e)}", 'title': '', 'metadata': {}}]

