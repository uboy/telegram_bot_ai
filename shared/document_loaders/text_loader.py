"""
Загрузчик для текстовых файлов
"""
import os
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_into_chunks


class TextLoader(DocumentLoader):
    """Загрузчик для текстовых файлов"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить текстовый файл"""
        try:
            encodings = ['utf-8', 'utf-8-sig', 'cp1251', 'latin-1', 'windows-1251']
            content = None
            
            for encoding in encodings:
                try:
                    with open(source, 'r', encoding=encoding) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                with open(source, 'rb') as f:
                    raw_content = f.read()
                    for encoding in encodings:
                        try:
                            content = raw_content.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
            
            if content is None:
                return [{'content': 'Не удалось прочитать файл. Неподдерживаемая кодировка.', 'title': '', 'metadata': {'type': 'text'}}]
            
            chunking_mode = (options or {}).get("chunking_mode") or "fixed"
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

            if chunking_mode == "full":
                return [{
                    "content": content,
                    "title": "",
                    "metadata": {"type": "text", "chunk_kind": "full_doc"},
                }]

            if len(content) > 5000:
                chunks: List[Dict[str, str]] = []
                for idx, part in enumerate(
                    split_text_into_chunks(content, max_chars=max_chars, overlap=overlap),
                    start=1,
                ):
                    chunks.append({
                        "content": part,
                        "title": f"Фрагмент {idx}",
                        "metadata": {"type": "text", "chunk_kind": "text"},
                    })
                return chunks if chunks else [
                    {"content": content, "title": "", "metadata": {"type": "text", "chunk_kind": "text"}}
                ]
            else:
                return [{"content": content, "title": "", "metadata": {"type": "text", "chunk_kind": "text"}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки текстового файла: {str(e)}", 'title': '', 'metadata': {}}]

