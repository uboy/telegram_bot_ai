"""
Загрузчик для текстовых файлов
"""
import os
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_into_chunks


class TextLoader(DocumentLoader):
    """Загрузчик для текстовых файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
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
            
            if len(content) > 5000:
                chunks: List[Dict[str, str]] = []
                for idx, part in enumerate(split_text_into_chunks(content), start=1):
                    chunks.append({
                        "content": part,
                        "title": f"Фрагмент {idx}",
                        "metadata": {"type": "text"},
                    })
                return chunks if chunks else [
                    {"content": content, "title": "", "metadata": {"type": "text"}}
                ]
            else:
                return [{"content": content, "title": "", "metadata": {"type": "text"}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки текстового файла: {str(e)}", 'title': '', 'metadata': {}}]

