"""
Загрузчик для Word файлов
"""
import os
import re
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally_with_metadata


def _heading_level(style_name: str) -> int:
    match = re.search(r"(\d+)", style_name or "")
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except (TypeError, ValueError):
        return 1


def _format_word_paragraph_text(text: str, style_name: str) -> str:
    style_lower = (style_name or "").lower()
    if "list bullet" in style_lower and not text.lstrip().startswith(("-", "*", "•")):
        return f"- {text}"
    if "list number" in style_lower and not re.match(r"^\s*\d+\.", text):
        return f"1. {text}"
    return text


class WordLoader(DocumentLoader):
    """Загрузчик для Word файлов"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить Word файл с учетом структуры (заголовки, абзацы, списки)"""
        try:
            from docx import Document
            chunks: List[Dict[str, str]] = []
            doc_title = os.path.splitext(os.path.basename(source))[0] or os.path.basename(source) or "Document"
            global_chunk_no = 1
            document_char_offset = 0

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
            
            doc = Document(source)
            current_section_lines: List[str] = []
            current_section_title = doc_title
            current_section_path = doc_title or "ROOT"
            current_heading_level = 0
            current_paragraph_numbers: List[int] = []
            heading_stack: List[tuple[int, str]] = []

            def flush_section() -> None:
                nonlocal global_chunk_no, document_char_offset
                if not current_section_lines:
                    return

                section_text = "\n".join(current_section_lines).strip()
                if not section_text:
                    return

                section_records = split_text_structurally_with_metadata(
                    section_text,
                    max_chars=max_chars,
                    overlap=overlap,
                )
                paragraph_start = current_paragraph_numbers[0] if current_paragraph_numbers else None
                paragraph_end = current_paragraph_numbers[-1] if current_paragraph_numbers else None

                for section_chunk_no, record in enumerate(section_records, start=1):
                    title = current_section_title or doc_title or "Document"
                    if len(section_records) > 1 and current_section_title:
                        title = f"{current_section_title} (фрагмент {section_chunk_no})"
                    metadata: Dict[str, object] = {
                        "type": "word",
                        "doc_title": doc_title,
                        "section_title": current_section_title,
                        "section_path": current_section_path,
                        "heading_level": current_heading_level,
                        "chunk_kind": record.get("chunk_kind") or "text",
                        "chunk_no": global_chunk_no,
                        "char_start": document_char_offset + int(record.get("char_start") or 0),
                        "char_end": document_char_offset + int(record.get("char_end") or 0),
                        "parser_profile": "loader:word:v2",
                        "paragraph_start": paragraph_start,
                        "paragraph_end": paragraph_end,
                        "paragraph_count": len(current_paragraph_numbers),
                    }
                    chunks.append({
                        "content": record["content"],
                        "title": title,
                        "metadata": metadata,
                    })
                    global_chunk_no += 1

                document_char_offset += len(section_text) + 2
            
            for paragraph_no, para in enumerate(doc.paragraphs, start=1):
                text = (para.text or "").strip()
                if not text:
                    continue
                
                style_name = getattr(getattr(para, "style", None), "name", "") or ""
                is_heading = style_name.startswith("Heading")
                
                if is_heading:
                    flush_section()

                    level = _heading_level(style_name)
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, text))

                    current_section_title = text
                    current_section_path = " > ".join([doc_title] + [item[1] for item in heading_stack if item[1]]) if doc_title else " > ".join([item[1] for item in heading_stack if item[1]])
                    current_heading_level = level
                    current_section_lines = [text]
                    current_paragraph_numbers = [paragraph_no]
                else:
                    formatted_text = _format_word_paragraph_text(text, style_name)
                    if not current_section_lines:
                        current_section_title = doc_title
                        current_section_path = doc_title or "ROOT"
                        current_heading_level = 0
                    current_section_lines.append(formatted_text)
                    current_paragraph_numbers.append(paragraph_no)
            
            flush_section()
            
            return chunks if chunks else [
                {"content": "Word файл пуст", "title": "", "metadata": {"type": "word", "doc_title": doc_title, "parser_profile": "loader:word:v2"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека python-docx не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки Word: {str(e)}", 'title': '', 'metadata': {}}]

