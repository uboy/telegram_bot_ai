"""
Модуль для разбиения текста на чанки с учетом структуры документа
"""
import re
import logging
from typing import List

logger = logging.getLogger(__name__)


def split_text_into_chunks(
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


def split_markdown_section_into_chunks(
    text: str,
    max_chars: int = None,
    overlap: int = None,
) -> List[str]:
    """
    Структурный разбиенийщик секции Markdown на чанки с учетом структуры документа.
    
    Правила:
    1. Блоки кода (CODE:\n...\n) сохраняются как единое целое
    2. Нумерованные списки (1., 2., ...) сохраняются целиком, если возможно
    3. Абзацы не разрываются посередине
    4. Если секция небольшая, она остается одним чанком
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
    
    # Если текст помещается в один чанк, возвращаем его целиком
    if len(text) <= max_chars:
        return [text]
    
    chunks: List[str] = []
    current_chunk = ""
    lines = text.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i]
        line_len = len(line)
        current_len = len(current_chunk)
        
        # Проверка: является ли строка началом блока кода?
        is_code_start = "__CODE_BLOCK_START__" in line
        
        # Проверка: является ли строка началом нумерованного списка?
        is_numbered_list_item = bool(re.match(r'^\s*\d+\.\s+', line))
        
        # Проверка: является ли строка элементом маркированного списка?
        is_bullet_list_item = bool(re.match(r'^\s*[-*•]\s+', line))
        
        # Если это блок кода, собираем весь блок между маркерами
        if is_code_start:
            code_block = line + '\n'
            i += 1
            code_end_found = False
            while i < len(lines):
                next_line = lines[i]
                code_block += next_line + '\n'
                if "__CODE_BLOCK_END__" in next_line:
                    code_end_found = True
                    i += 1
                    break
                i += 1
            
            # Если маркер конца не найден, все равно сохраняем как код
            if not code_end_found:
                logger.warning("Code block end marker not found, treating until end as code")
            
            code_block_len = len(code_block)
            if current_chunk and current_len + code_block_len > max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = code_block
            elif code_block_len > max_chars:
                if code_block_len <= max_chars * 1.5:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    chunks.append(code_block.strip())
                    current_chunk = ""
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    # Для очень больших блоков кода разбиваем по строкам
                    code_lines = code_block.split('\n')
                    temp_code = ""
                    for code_line in code_lines:
                        if code_line.strip():
                            if temp_code and len(temp_code) + len(code_line) + 1 > max_chars:
                                chunks.append(temp_code.strip())
                                temp_code = code_line + '\n'
                            else:
                                temp_code += code_line + '\n'
                    current_chunk = temp_code
            else:
                current_chunk += code_block
        
        # Если это элемент списка (нумерованный или маркированный), собираем весь список целиком
        elif is_numbered_list_item or is_bullet_list_item:
            list_block = line + '\n'
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip():
                    list_block += '\n'
                    i += 1
                    break
                if re.match(r'^\s*\d+\.\s+', next_line) or re.match(r'^\s{2,}', next_line):
                    list_block += next_line + '\n'
                    i += 1
                else:
                    break
            
            list_block_len = len(list_block)
            if current_chunk and current_len + list_block_len > max_chars:
                chunks.append(current_chunk.strip())
                if list_block_len <= max_chars * 1.2:
                    current_chunk = list_block
                else:
                    # Определить тип списка по первой строке
                    first_line = list_block.split('\n')[0] if list_block else ""
                    is_numbered = bool(re.match(r'^\s*\d+\.\s+', first_line))
                    
                    # Использовать правильный паттерн в зависимости от типа списка
                    if is_numbered:
                        list_items = re.split(r'(\n\s*\d+\.\s+)', list_block)
                    else:
                        list_items = re.split(r'(\n\s*[-*•]\s+)', list_block)
                    
                    temp_list = ""
                    for j in range(0, len(list_items), 2):
                        item = (list_items[j] if j < len(list_items) else "") + \
                               (list_items[j+1] if j+1 < len(list_items) else "")
                        if temp_list and len(temp_list) + len(item) > max_chars:
                            chunks.append(temp_list.strip())
                            temp_list = item
                        else:
                            temp_list += item
                    current_chunk = temp_list
            elif list_block_len > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                if list_block_len <= max_chars * 1.2:
                    current_chunk = list_block
                else:
                    # Определить тип списка по первой строке
                    first_line = list_block.split('\n')[0] if list_block else ""
                    is_numbered = bool(re.match(r'^\s*\d+\.\s+', first_line))
                    
                    # Использовать правильный паттерн в зависимости от типа списка
                    if is_numbered:
                        list_items = re.split(r'(\n\s*\d+\.\s+)', list_block)
                    else:
                        list_items = re.split(r'(\n\s*[-*•]\s+)', list_block)
                    
                    temp_list = ""
                    for j in range(0, len(list_items), 2):
                        item = (list_items[j] if j < len(list_items) else "") + \
                               (list_items[j+1] if j+1 < len(list_items) else "")
                        if temp_list and len(temp_list) + len(item) > max_chars:
                            chunks.append(temp_list.strip())
                            temp_list = item
                        else:
                            temp_list += item
                    current_chunk = temp_list
            else:
                current_chunk += list_block
        
        # Обычная строка
        else:
            if not line.strip():
                if current_chunk:
                    current_chunk += '\n'
                i += 1
                continue
            
            if current_len + line_len + 1 <= max_chars:
                current_chunk += line + '\n'
                i += 1
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                if line_len > max_chars:
                    sentences = re.split(r'([.!?]\s+)', line)
                    temp_line = ""
                    for j in range(0, len(sentences), 2):
                        sentence = sentences[j] + (sentences[j+1] if j+1 < len(sentences) else "")
                        if temp_line and len(temp_line) + len(sentence) + 1 > max_chars:
                            chunks.append(temp_line.strip())
                            temp_line = sentence
                        else:
                            temp_line += sentence
                    current_chunk = temp_line + '\n' if temp_line else ""
                else:
                    current_chunk = line + '\n'
                i += 1
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Применить overlap между соседними чанками
    if overlap > 0 and len(chunks) > 1:
        overlapped_chunks = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                prev_chunk = chunks[i-1]
                overlap_text = prev_chunk[-overlap:] if len(prev_chunk) > overlap else prev_chunk
                chunk = overlap_text + "\n\n" + chunk
            overlapped_chunks.append(chunk)
        return overlapped_chunks
    
    return [c for c in chunks if c]


def split_text_structurally(
    text: str,
    max_chars: int = None,
    overlap: int = None,
) -> List[str]:
    """
    Универсальный структурный разбиенийщик текста на чанки.
    
    Учитывает структуру:
    - Абзацы (разделены двойными переносами строк)
    - Нумерованные списки (1., 2., ...)
    - Маркированные списки (-, *, •)
    - Блоки кода (если есть префикс CODE:)
    - Предложения (для разбиения больших абзацев)
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
    
    # Если текст помещается в один чанк, возвращаем его целиком
    if len(text) <= max_chars:
        return [text]
    
    chunks: List[str] = []
    current_chunk = ""
    lines = text.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i]
        line_len = len(line)
        current_len = len(current_chunk)
        
        # Проверка: является ли строка началом блока кода?
        is_code_start = "__CODE_BLOCK_START__" in line
        
        # Проверка: является ли строка элементом списка?
        is_numbered_list_item = bool(re.match(r'^\s*\d+\.\s+', line))
        is_bullet_list_item = bool(re.match(r'^\s*[-*•]\s+', line))
        is_list_item = is_numbered_list_item or is_bullet_list_item
        is_list_continuation = bool(re.match(r'^\s{2,}', line)) and line.strip() and not is_list_item
        
        # Если это блок кода, собираем весь блок между маркерами
        if is_code_start:
            code_block = line + '\n'
            i += 1
            code_end_found = False
            while i < len(lines):
                next_line = lines[i]
                code_block += next_line + '\n'
                if "__CODE_BLOCK_END__" in next_line:
                    code_end_found = True
                    i += 1
                    break
                i += 1
            
            # Если маркер конца не найден, все равно сохраняем как код
            if not code_end_found:
                logger.warning("Code block end marker not found, treating until end as code")
            
            code_block_len = len(code_block)
            if current_chunk and current_len + code_block_len > max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = code_block if code_block_len <= max_chars * 1.5 else code_block[:max_chars]
            elif code_block_len > max_chars * 1.5:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                chunks.append(code_block.strip())
                current_chunk = ""
            else:
                current_chunk += code_block
        
        # Если это элемент списка, собираем весь список целиком
        elif is_list_item:
            list_block = line + '\n'
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip():
                    list_block += '\n'
                    i += 1
                    break
                if re.match(r'^\s*\d+\.\s+', next_line) or re.match(r'^\s*[-*•]\s+', next_line) or re.match(r'^\s{2,}', next_line):
                    list_block += next_line + '\n'
                    i += 1
                else:
                    break
            
            list_block_len = len(list_block)
            if current_chunk and current_len + list_block_len > max_chars:
                chunks.append(current_chunk.strip())
                if list_block_len <= max_chars * 1.2:
                    current_chunk = list_block
                else:
                    # Определить тип списка по первой строке
                    first_line = list_block.split('\n')[0] if list_block else ""
                    is_numbered = bool(re.match(r'^\s*\d+\.\s+', first_line))
                    
                    # Использовать правильный паттерн в зависимости от типа списка
                    if is_numbered:
                        list_items = re.split(r'(\n\s*\d+\.\s+)', list_block)
                    else:
                        list_items = re.split(r'(\n\s*[-*•]\s+)', list_block)
                    
                    temp_list = ""
                    for j in range(0, len(list_items), 2):
                        item = (list_items[j] if j < len(list_items) else "") + \
                               (list_items[j+1] if j+1 < len(list_items) else "")
                        if temp_list and len(temp_list) + len(item) > max_chars:
                            chunks.append(temp_list.strip())
                            temp_list = item
                        else:
                            temp_list += item
                    current_chunk = temp_list
            elif list_block_len > max_chars * 1.2:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                if list_block_len <= max_chars * 1.2:
                    current_chunk = list_block
                else:
                    # Определить тип списка по первой строке
                    first_line = list_block.split('\n')[0] if list_block else ""
                    is_numbered = bool(re.match(r'^\s*\d+\.\s+', first_line))
                    
                    # Использовать правильный паттерн в зависимости от типа списка
                    if is_numbered:
                        list_items = re.split(r'(\n\s*\d+\.\s+)', list_block)
                    else:
                        list_items = re.split(r'(\n\s*[-*•]\s+)', list_block)
                    
                    temp_list = ""
                    for j in range(0, len(list_items), 2):
                        item = (list_items[j] if j < len(list_items) else "") + \
                               (list_items[j+1] if j+1 < len(list_items) else "")
                        if temp_list and len(temp_list) + len(item) > max_chars:
                            chunks.append(temp_list.strip())
                            temp_list = item
                        else:
                            temp_list += item
                    current_chunk = temp_list
            else:
                current_chunk += list_block
        
        # Обычная строка
        else:
            if not line.strip():
                if current_chunk:
                    current_chunk += '\n'
                i += 1
                continue
            
            if current_len + line_len + 1 <= max_chars:
                current_chunk += line + '\n'
                i += 1
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                if line_len > max_chars:
                    sentences = re.split(r'([.!?]\s+)', line)
                    temp_line = ""
                    for j in range(0, len(sentences), 2):
                        sentence = sentences[j] + (sentences[j+1] if j+1 < len(sentences) else "")
                        if temp_line and len(temp_line) + len(sentence) + 1 > max_chars:
                            chunks.append(temp_line.strip())
                            temp_line = sentence
                        else:
                            temp_line += sentence
                    current_chunk = temp_line + '\n' if temp_line else ""
                else:
                    current_chunk = line + '\n'
                i += 1
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Применить overlap
    if overlap > 0 and len(chunks) > 1:
        overlapped_chunks = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                prev_chunk = chunks[i-1]
                overlap_text = prev_chunk[-overlap:] if len(prev_chunk) > overlap else prev_chunk
                chunk = overlap_text + "\n\n" + chunk
            overlapped_chunks.append(chunk)
        return overlapped_chunks
    
    return [c for c in chunks if c]

