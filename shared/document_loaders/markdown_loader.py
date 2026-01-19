"""
Загрузчик для Markdown файлов
"""
import re
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_markdown_section_into_chunks, split_text_into_chunks


class MarkdownLoader(DocumentLoader):
    """Загрузчик для Markdown файлов"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить markdown файл"""
        import os
        try:
            with open(source, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Извлечь имя файла для doc_title
            doc_title = os.path.basename(source)
            if doc_title.endswith('.md'):
                doc_title = doc_title[:-3]

            # Очистить markdown-разметку до более "плоского" текста,
            # но СОХРАНИТЬ code blocks для индексации команд
            def _extract_code_blocks(text: str) -> tuple[str, list[dict]]:
                """Извлечь code blocks из текста и заменить их на плейсхолдеры"""
                code_blocks = []
                code_counter = 0
                
                def replace_code_block(match):
                    nonlocal code_counter
                    language = (match.group(2) or "").strip()  # Разрешаем язык с пробелами, убираем пробелы
                    code_content = match.group(3)
                    code_counter += 1
                    placeholder = f"__CODE_BLOCK_{code_counter}__"
                    code_blocks.append({
                        'content': code_content,
                        'language': language,
                        'placeholder': placeholder
                    })
                    return placeholder
                
                # Улучшенный pattern: поддерживает отступы у fenced code blocks (например, внутри списков)
                pattern = r"(?m)^(?P<indent>[ \t]*)```([^\n\r`]*)\r?\n(.*?)\r?\n(?P=indent)```"
                text_with_placeholders = re.sub(pattern, replace_code_block, text, flags=re.DOTALL | re.MULTILINE)
                return text_with_placeholders, code_blocks
            
            def _markdown_to_plain(text: str) -> str:
                """Очистить markdown-разметку"""
                text = re.sub(r"`([^`]+)`", r"\1", text)
                text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", text)
                text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
                # Очищаем только звездочки, чтобы не ломать идентификаторы с underscore.
                text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                text = re.sub(r"(?<!\w)\*(\S[^*]*?)\*(?!\w)", r"\1", text)
                text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
                # Удаляем только blockquote (>), сохраняем маркеры списков (-, *, +) для корректного определения chunk_kind
                text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
                text = re.sub(r"\|", " ", text)
                text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
                text = re.sub(r"\s+\n", "\n", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()

            chunking_mode = (options or {}).get("chunking_mode") or "section"
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

            sections: List[Dict[str, str]] = []
            if chunking_mode in ("full", "fixed"):
                sections = [{
                    "content": content,
                    "section_title": doc_title,
                    "section_path": doc_title or "ROOT",
                }]
            else:
                # Разбиение по заголовкам с построением section_path (стек заголовков)
                current_section_lines = []
                header_stack = []  # Стек заголовков: (level: int, header_text: str) для построения section_path

                for line in content.split("\n"):
                    header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
                    if header_match:
                        header_level = len(header_match.group(1))
                        header_text = header_match.group(2).strip()

                        # Сохранить предыдущую секцию ПЕРЕД обновлением стека (исправление бага)
                        if current_section_lines:
                            section_content = "\n".join(current_section_lines).strip()
                            if section_content:
                                # Использовать текущий header_stack (до обновления) для правильного title/path
                                prev_section_title = header_stack[-1][1] if header_stack else ""
                                prev_section_path = " > ".join([h[1] for h in header_stack]) if header_stack else ""
                                # Для секций без заголовков (до первого заголовка) подставляем doc_title
                                if not prev_section_path:
                                    prev_section_path = doc_title if doc_title else "ROOT"
                                sections.append({
                                    "content": section_content,
                                    "section_title": prev_section_title,  # Заголовок секции
                                    "section_path": prev_section_path,
                                })

                        # Обновить стек заголовков: убрать заголовки того же или более глубокого уровня
                        # Используем level:int вместо len(header_stack[-1][0]) для ясности
                        while header_stack and header_stack[-1][0] >= header_level:
                            header_stack.pop()
                        header_stack.append((header_level, header_text))

                        # Начать новую секцию (не включаем заголовок в content)
                        current_section_lines = []
                    else:
                        current_section_lines.append(line)

                # Сохранить последнюю секцию
                if current_section_lines:
                    section_content = "\n".join(current_section_lines).strip()
                    if section_content:
                        section_path = " > ".join([h[1] for h in header_stack]) if header_stack else ""
                        # Для секций без заголовков (до первого заголовка) подставляем doc_title
                        if not section_path:
                            section_path = doc_title if doc_title else "ROOT"
                        sections.append({
                            "content": section_content,
                            "section_title": header_stack[-1][1] if header_stack else "",
                            "section_path": section_path,
                        })

                if not sections:
                    sections = [{"content": content, "title": "", "section_path": ""}]

            # Чанкинг внутри каждой секции
            chunks: List[Dict[str, str]] = []
            for sec in sections:
                sec_text_raw = sec.get("content") or ""
                sec_title = sec.get("section_title") or ""  # Заголовок секции из metadata
                sec_path = sec.get("section_path") or ""
                
                sec_text_with_placeholders, code_blocks = _extract_code_blocks(sec_text_raw)
                sec_text_plain = _markdown_to_plain(sec_text_with_placeholders)
                
                # Создать словарь для сопоставления code_block с чанками
                # Ключ: placeholder, значение: code_block с language
                code_block_map = {cb['placeholder']: cb for cb in code_blocks}
                
                for code_block in code_blocks:
                    code_content = code_block['content'].strip()
                    # Используем парные маркеры для надежного определения границ кода
                    restored_code = f"\n__CODE_BLOCK_START__\n{code_content}\n__CODE_BLOCK_END__\n"
                    sec_text_plain = sec_text_plain.replace(code_block['placeholder'], restored_code)
                
                if chunking_mode == "full":
                    sec_chunks = [sec_text_plain]
                elif chunking_mode == "fixed":
                    sec_chunks = split_text_into_chunks(sec_text_plain, max_chars=max_chars, overlap=overlap)
                else:
                    sec_chunks = split_markdown_section_into_chunks(sec_text_plain, max_chars=max_chars, overlap=overlap)

                def _restore_code_fences(chunk_text: str) -> str:
                    if "__CODE_BLOCK_START__" not in chunk_text:
                        return chunk_text
                    text_out = chunk_text
                    text_out = text_out.replace("__CODE_BLOCK_START__\n", "```\n")
                    text_out = text_out.replace("\n__CODE_BLOCK_END__", "\n```")
                    return text_out

                for idx, part in enumerate(sec_chunks, start=1):
                    # title = doc_title (для поиска по документу, а не по секции)
                    # section_title = заголовок секции (для навигации)
                    title = doc_title
                    section_title = sec_title or ""
                    
                    # Определить chunk_kind по маркерам и найти language для code чанков
                    chunk_kind = "text"
                    code_lang = None
                    if chunking_mode == "full":
                        chunk_kind = "full_page"
                    elif "__CODE_BLOCK_START__" in part:
                        chunk_kind = "code"
                        # Найти соответствующий code_block: извлечь код из чанка и найти в code_blocks
                        code_in_chunk = part.split("__CODE_BLOCK_START__")[1].split("__CODE_BLOCK_END__")[0].strip() if "__CODE_BLOCK_START__" in part else ""
                        for cb in code_blocks:
                            if cb['content'].strip() == code_in_chunk:
                                code_lang = cb.get('language') or None
                                break
                    # Проверяем наличие списка внутри чанка (не только в начале), чтобы не пропустить списки с описанием
                    # Включаем нумерованные списки, bullet списки и "Step 1:" / "Steps:" паттерны
                    elif (re.search(r'(?m)^\s*\d+\.\s+', part) or 
                          re.search(r'(?m)^\s*[-*•]\s+', part) or
                          re.search(r'(?i)(?:^|\n)\s*(?:step\s+\d+|steps?)\s*:', part)):
                        chunk_kind = "list"
                    
                    metadata = {
                        "type": "markdown",
                        "chunk_kind": chunk_kind,
                        "section_title": section_title,  # Заголовок секции
                        "section_path": sec_path,  # Полный путь через заголовки
                        "chunk_no": idx,
                        "doc_title": doc_title,  # Дублируем для совместимости
                    }
                    # Добавить code_lang для code чанков
                    if code_lang:
                        metadata["code_lang"] = code_lang
                    
                    chunks.append({
                        "content": _restore_code_fences(part),
                        "title": title,  # Название документа для поиска
                        "metadata": metadata,
                    })

            return chunks if chunks else [
                {"content": content, "title": "", "metadata": {"type": "markdown"}}
            ]
        except Exception as e:
            return [{'content': f"Ошибка загрузки markdown: {str(e)}", 'title': '', 'metadata': {}}]

