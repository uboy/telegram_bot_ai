"""
Модули для загрузки документов различных форматов
"""
import os
import re
import requests
from typing import List, Dict, Optional
from pathlib import Path
import tempfile


def _split_text_into_chunks(
    text: str,
    max_chars: int = None,
    overlap: int = None,
) -> List[str]:
    """
    Универсальный разбиенийщик текста на чанки.

    Приблизительно соответствует 300–800 токенам при средней длине токена ~3–4 символа.
    overlap используется, чтобы сохранить связность между соседними фрагментами.
    """
    # Получить настройки из конфига, если не указаны явно
    if max_chars is None:
        try:
            from shared.config import RAG_CHUNK_SIZE
            max_chars = RAG_CHUNK_SIZE
        except ImportError:
            max_chars = 2000
    
    if overlap is None:
        try:
            from shared.config import RAG_CHUNK_OVERLAP
            overlap = RAG_CHUNK_OVERLAP
        except ImportError:
            overlap = 400
    
    text = text or ""
    if not text:
        return []

    chunks: List[str] = []
    start = 0
    length = len(text)

    # Гарантируем валидные параметры
    if max_chars <= 0:
        return [text]
    if overlap < 0:
        overlap = 0
    if overlap >= max_chars:
        overlap = max_chars // 4

    while start < length:
        end = min(start + max_chars, length)
        chunk = text[start:end]
        chunks.append(chunk.strip())

        if end == length:
            break

        # Следующий старт с учётом overlap
        start = end - overlap
        if start < 0:
            start = 0

    return [c for c in chunks if c]


class DocumentLoader:
    """Базовый класс для загрузчиков документов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить документ и вернуть список фрагментов"""
        raise NotImplementedError


class MarkdownLoader(DocumentLoader):
    """Загрузчик для Markdown файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить markdown файл"""
        try:
            with open(source, 'r', encoding='utf-8') as f:
                content = f.read()

            # Очистить markdown-разметку до более "плоского" текста,
            # но СОХРАНИТЬ code blocks для индексации команд
            def _extract_code_blocks(text: str) -> tuple[str, list[dict]]:
                """Извлечь code blocks из текста и заменить их на плейсхолдеры"""
                code_blocks = []
                code_counter = 0
                
                def replace_code_block(match):
                    nonlocal code_counter
                    language = match.group(1) or ""  # Язык (если указан)
                    code_content = match.group(2)  # Содержимое между ```
                    code_counter += 1
                    placeholder = f"__CODE_BLOCK_{code_counter}__"
                    code_blocks.append({
                        'content': code_content,
                        'language': language,
                        'placeholder': placeholder
                    })
                    return placeholder
                
                # Извлекаем блоки кода ```language\ncode\n``` или ```\ncode\n```
                pattern = r"```(\w+)?\n(.*?)```"
                text_with_placeholders = re.sub(pattern, replace_code_block, text, flags=re.DOTALL)
                
                return text_with_placeholders, code_blocks
            
            def _markdown_to_plain(text: str) -> str:
                """Очистить markdown-разметку"""
                # Убрать inline-код `...` (но сохранить содержимое)
                text = re.sub(r"`([^`]+)`", r"\1", text)
                # Картинки ![alt](url) -> alt (url) или просто alt
                text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", text)
                # Ссылки [text](url) -> text (url)
                text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
                # Жирный/курсив **text**, *text*, __text__, _text_ -> text
                text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
                text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
                # Убрать заголовочные маркеры #, ##, ### в начале строки
                text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
                # Убрать лишние markdown-символы списков, сохранив текст
                text = re.sub(r"^[>\-\*+]\s+", "", text, flags=re.MULTILINE)
                # Упростить таблицы: убрать вертикальные разделители
                text = re.sub(r"\|", " ", text)
                # Удалить горизонтальные линии
                text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
                # Свести множественные пробелы/переносы
                text = re.sub(r"\s+\n", "\n", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()

            # Сначала разбиение по заголовкам в оригинальном markdown (до очистки)
            sections: List[Dict[str, str]] = []
            current_section_lines = []
            current_title = ""
            
            for line in content.split("\n"):
                # Проверяем, является ли строка заголовком (начинается с #)
                header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
                if header_match:
                    # Сохраняем предыдущую секцию
                    if current_section_lines:
                        section_content = "\n".join(current_section_lines).strip()
                        if section_content:  # Только если есть содержимое
                            sections.append(
                                {
                                    "content": section_content,
                                    "title": current_title,
                                }
                            )
                    # Начинаем новую секцию
                    current_title = header_match.group(2).strip()
                    current_section_lines = [line]  # Сохраняем заголовок в секции
                else:
                    current_section_lines.append(line)
            
            # Добавить последнюю секцию
            if current_section_lines:
                section_content = "\n".join(current_section_lines).strip()
                if section_content:  # Только если есть содержимое
                    sections.append(
                        {
                            "content": section_content,
                            "title": current_title,
                        }
                    )
            
            # Если не было заголовков, весь документ - одна секция
            if not sections:
                sections = [{"content": content, "title": ""}]

            # Затем более тонкий чанкинг внутри каждой секции (300–800 токенов, overlap)
            # Сохраняем code blocks для индексации команд
            chunks: List[Dict[str, str]] = []
            for sec in sections:
                sec_text_raw = sec.get("content") or ""
                sec_title = sec.get("title") or ""
                
                # Извлечь code blocks из секции ДО очистки markdown
                sec_text_with_placeholders, code_blocks = _extract_code_blocks(sec_text_raw)
                
                # Очистить markdown-разметку (плейсхолдеры code blocks останутся)
                sec_text_plain = _markdown_to_plain(sec_text_with_placeholders)
                
                # Восстановить code blocks в тексте с префиксом CODE: для лучшей индексации
                for code_block in code_blocks:
                    code_content = code_block['content'].strip()
                    # Добавляем префикс CODE: для лучшей индексации команд
                    restored_code = f"\nCODE:\n{code_content}\n"
                    sec_text_plain = sec_text_plain.replace(code_block['placeholder'], restored_code)
                
                # Разбить секцию на чанки
                sec_chunks = _split_text_into_chunks(sec_text_plain)
                for idx, part in enumerate(sec_chunks, start=1):
                    title = sec_title or ""
                    if len(sec_chunks) > 1:
                        # Нумеруем под-чанки внутри секции, если их несколько
                        title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                    
                    # Определить тип чанка (code или text)
                    chunk_kind = "code" if "CODE:\n" in part else "text"
                    
                    chunks.append(
                        {
                            "content": part,
                            "title": title,
                            "metadata": {
                                "type": "markdown",
                                "chunk_kind": chunk_kind,
                                "section_title": sec_title,
                            },
                        }
                    )

            return chunks if chunks else [
                {"content": content, "title": "", "metadata": {"type": "markdown"}}
            ]
        except Exception as e:
            return [{'content': f"Ошибка загрузки markdown: {str(e)}", 'title': '', 'metadata': {}}]


class PDFLoader(DocumentLoader):
    """Загрузчик для PDF файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить PDF файл"""
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

                    # Чанкуем каждую страницу по 300–800 токенов
                    for idx, part in enumerate(_split_text_into_chunks(text), start=1):
                        title = f"Страница {i + 1}"
                        if len(_split_text_into_chunks(text)) > 1:
                            title = f"{title} (фрагмент {idx})"
                        chunks.append(
                            {
                                "content": part,
                                "title": title,
                                "metadata": {"type": "pdf", "page": i + 1},
                            }
                        )
            
            return chunks if chunks else [
                {"content": "PDF файл пуст", "title": "", "metadata": {"type": "pdf"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека PyPDF2 не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки PDF: {str(e)}", 'title': '', 'metadata': {}}]


class WordLoader(DocumentLoader):
    """Загрузчик для Word файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить Word файл"""
        try:
            from docx import Document
            chunks: List[Dict[str, str]] = []
            
            doc = Document(source)
            current_section = ""
            current_title = ""
            
            for para in doc.paragraphs:
                text = (para.text or "").strip()
                if not text:
                    continue
                
                # Проверка на заголовок
                if para.style and para.style.name.startswith("Heading"):
                    if current_section:
                        # Чанкуем предыдущую секцию
                        for idx, part in enumerate(_split_text_into_chunks(current_section), start=1):
                            title = current_title or ""
                            if len(_split_text_into_chunks(current_section)) > 1:
                                title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                            chunks.append(
                                {
                                    "content": part,
                                    "title": title,
                                    "metadata": {"type": "word"},
                                }
                            )
                    current_title = text
                    current_section = text + "\n"
                else:
                    current_section += text + "\n"
            
            if current_section:
                for idx, part in enumerate(_split_text_into_chunks(current_section), start=1):
                    title = current_title or ""
                    if len(_split_text_into_chunks(current_section)) > 1:
                        title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                    chunks.append(
                        {
                            "content": part,
                            "title": title,
                            "metadata": {"type": "word"},
                        }
                    )
            
            return chunks if chunks else [
                {"content": "Word файл пуст", "title": "", "metadata": {"type": "word"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека python-docx не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки Word: {str(e)}", 'title': '', 'metadata': {}}]


class ExcelLoader(DocumentLoader):
    """Загрузчик для Excel файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить Excel файл"""
        try:
            import pandas as pd
            chunks: List[Dict[str, str]] = []
            
            # Попробовать загрузить все листы
            excel_file = pd.ExcelFile(source)
            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                
                # Конвертировать в текст
                text = f"Лист: {sheet_name}\n\n{df.to_string(index=False)}"

                for idx, part in enumerate(_split_text_into_chunks(text), start=1):
                    title = f"Лист: {sheet_name}"
                    if len(_split_text_into_chunks(text)) > 1:
                        title = f"{title} (фрагмент {idx})"
                    chunks.append(
                        {
                            "content": part,
                            "title": title,
                            "metadata": {"type": "excel", "sheet": sheet_name},
                        }
                    )
            
            return chunks if chunks else [
                {"content": "Excel файл пуст", "title": "", "metadata": {"type": "excel"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека pandas не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки Excel: {str(e)}", 'title': '', 'metadata': {}}]


class WebLoader(DocumentLoader):
    """Загрузчик для веб-страниц"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить веб-страницу с повторными попытками при таймауте"""
        try:
            from bs4 import BeautifulSoup
            import html2text
            import time
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # Retry логика для веб-загрузчика
            max_retries = 3
            base_delay = 2.0
            response = None
            
            for attempt in range(max_retries):
                try:
                    timeout = 10 + (attempt * 5)  # Увеличиваем таймаут с каждой попыткой
                    response = requests.get(source, timeout=timeout, headers=headers)
                    response.raise_for_status()
                    break  # Успешно загружено
                except requests.exceptions.Timeout as e:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Экспоненциальная задержка
                        time.sleep(delay)
                    else:
                        raise
                except Exception as e:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                    else:
                        raise
            
            if response is None:
                return [{'content': f"Не удалось загрузить веб-страницу после {max_retries} попыток", 'title': '', 'metadata': {'type': 'web', 'url': source}}]
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Удалить скрипты и стили
            for script in soup(["script", "style"]):
                script.decompose()
            
            # Конвертировать в markdown-подобный текст
            h = html2text.HTML2Text()
            h.ignore_links = False
            text = h.handle(str(soup))

            # Чанкуем итоговый текст
            chunks: List[Dict[str, str]] = []
            for idx, part in enumerate(_split_text_into_chunks(text), start=1):
                chunks.append(
                    {
                        "content": part,
                        "title": f"Фрагмент {idx}",
                        "metadata": {"type": "web", "url": source},
                    }
                )
            
            return chunks if chunks else [
                {"content": text[:5000], "title": "", "metadata": {"type": "web", "url": source}}
            ]
        except ImportError:
            return [{'content': 'Библиотеки beautifulsoup4 и html2text не установлены', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки веб-страницы: {str(e)}", 'title': '', 'metadata': {}}]


class TextLoader(DocumentLoader):
    """Загрузчик для текстовых файлов"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить текстовый файл"""
        try:
            # Попробовать разные кодировки
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
                # Если не удалось прочитать как текст, попробовать бинарный режим
                with open(source, 'rb') as f:
                    raw_content = f.read()
                    # Попробовать декодировать
                    for encoding in encodings:
                        try:
                            content = raw_content.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
            
            if content is None:
                return [{'content': 'Не удалось прочитать файл. Неподдерживаемая кодировка.', 'title': '', 'metadata': {'type': 'text'}}]
            
            # Разбить на фрагменты, если файл большой (используем общий чанкер)
            if len(content) > 5000:
                chunks: List[Dict[str, str]] = []
                for idx, part in enumerate(_split_text_into_chunks(content), start=1):
                    chunks.append(
                        {
                            "content": part,
                            "title": f"Фрагмент {idx}",
                            "metadata": {"type": "text"},
                        }
                    )
                return chunks if chunks else [
                    {"content": content, "title": "", "metadata": {"type": "text"}}
                ]
            else:
                return [{"content": content, "title": "", "metadata": {"type": "text"}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки текстового файла: {str(e)}", 'title': '', 'metadata': {}}]


class ImageLoader(DocumentLoader):
    """Загрузчик для изображений (для мультимодальной обработки)"""
    
    def load(self, source: str) -> List[Dict[str, str]]:
        """Загрузить изображение (вернет путь к файлу для обработки)"""
        return [{
            'content': source,  # Путь к изображению
            'title': os.path.basename(source),
            'metadata': {'type': 'image', 'path': source}
        }]


class DocumentLoaderManager:
    """Менеджер для загрузчиков документов"""
    
    def __init__(self):
        text_loader = TextLoader()
        self.loaders = {
            'markdown': MarkdownLoader(),
            'md': MarkdownLoader(),
            'pdf': PDFLoader(),
            'docx': WordLoader(),
            'doc': WordLoader(),
            'xlsx': ExcelLoader(),
            'xls': ExcelLoader(),
            'txt': text_loader,
            'text': text_loader,
            'web': WebLoader(),
            'url': WebLoader(),
            'image': ImageLoader(),
            'jpg': ImageLoader(),
            'jpeg': ImageLoader(),
            'png': ImageLoader(),
            'gif': ImageLoader(),
        }
    
    def get_loader(self, file_type: str) -> Optional[DocumentLoader]:
        """Получить загрузчик по типу файла"""
        return self.loaders.get(file_type.lower())
    
    def load_document(self, source: str, file_type: Optional[str] = None) -> List[Dict[str, str]]:
        """Загрузить документ"""
        if file_type is None:
            # Определить тип по расширению или URL
            if source.startswith('http://') or source.startswith('https://'):
                file_type = 'web'
            else:
                ext = Path(source).suffix.lower().lstrip('.')
                file_type = ext if ext else 'text'
        
        loader = self.get_loader(file_type)
        if loader:
            return loader.load(source)
        else:
            # Попытка загрузить как текстовый файл (fallback)
            text_loader = TextLoader()
            return text_loader.load(source)


# Глобальный менеджер загрузчиков
document_loader_manager = DocumentLoaderManager()

