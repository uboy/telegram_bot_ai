"""
Вспомогательные утилиты
"""
import re
import html
from typing import Optional
from html import escape as html_escape


def detect_language(text: str) -> str:
    """Определить язык текста (простая эвристика)"""
    # Подсчет кириллических символов
    cyrillic_count = len(re.findall(r'[а-яёА-ЯЁ]', text))
    # Подсчет латинских символов
    latin_count = len(re.findall(r'[a-zA-Z]', text))
    
    if cyrillic_count > latin_count:
        return 'ru'
    elif latin_count > 0:
        return 'en'
    else:
        return 'ru'  # По умолчанию русский


def clean_text_for_telegram(text: str) -> str:
    """Очистить текст от markdown символов для безопасной отправки в Telegram
    
    ВНИМАНИЕ: Эта функция удаляет markdown символы и НЕ должна использоваться
    для ответов RAG, которые содержат код. Используйте format_for_telegram_answer()
    для форматирования ответов с кодом.
    """
    # Удалить markdown символы, которые могут вызвать проблемы
    # Заменяем на обычный текст
    # НЕ удаляем backticks - они нужны для кода
    text = text.replace('*', '').replace('_', '').replace('~', '')
    text = text.replace('[', '').replace(']', '').replace('(', '').replace(')', '')
    # Удаляем множественные пробелы
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_wiki_url_for_display(url: str) -> str:
    """Нормализовать URL вики для отображения (конвертировать export URL в читаемый формат)"""
    if not url or not url.startswith(('http://', 'https://')):
        return url
    
    # Если это export URL Gitee вики, конвертируем в нормальный формат
    # Пример: https://gitee.com/.../wikis/pages/export?type=markdown&doc_id=2921510
    # -> https://gitee.com/.../wikis/Sync&Build/Sync%26Build
    if '/wikis/pages/export' in url:
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            # Извлекаем doc_id из query параметров
            if 'doc_id' in query_params:
                doc_id = query_params['doc_id'][0]
                # Строим нормальный URL вики
                # Базовый путь до /wikis
                path_parts = parsed.path.split('/wikis')
                if len(path_parts) >= 2:
                    base_path = path_parts[0] + '/wikis'
                    # Попытаемся найти название страницы из других параметров или использовать doc_id
                    # Для Gitee обычно можно использовать doc_id для построения URL
                    # Но лучше использовать оригинальный URL если он есть в метаданных
                    # Пока просто возвращаем базовый путь вики
                    return f"{parsed.scheme}://{parsed.netloc}{base_path}"
        except Exception:
            pass
    
    # Если это обычный URL вики, возвращаем как есть
    return url


def strip_html_tags(text: str) -> str:
    """
    Безопасно удаляет HTML-теги из текста, оставляя только plain текст.
    Используется для fallback при ошибках parse_mode='HTML' в Telegram.
    """
    if not text:
        return ""
    
    # Сохраняем переносы строк для типичных блочных тегов
    text = re.sub(r'</(p|div|li|h[1-6])\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Удаляем все остальные теги
    text = re.sub(r'<[^>]+>', '', text)

    # Декодируем HTML-сущности корректно
    text = html.unescape(text)

    # Нормализация пробелов БЕЗ уничтожения переносов строк
    # 1) чистим пробелы/табы
    text = re.sub(r'[ \t]+', ' ', text)
    # 2) чистим пробелы вокруг переводов строк
    text = re.sub(r' *\n *', '\n', text)
    # 3) схлопываем слишком много пустых строк
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def format_text_safe(text: str, max_length: int = 4096) -> str:
    """Безопасное форматирование текста для Telegram"""
    # Очистить от markdown
    text = clean_text_for_telegram(text)
    
    # Обрезать если слишком длинный
    if len(text) > max_length:
        text = text[:max_length-50] + "\n\n... (сообщение обрезано)"
    
    return text


def format_commands_in_text(text: str) -> str:
    """Автоматически форматировать команды в тексте как inline код или блоки кода"""
    import re
    
    if not text:
        return ""
    
    # Сначала защищаем уже отформатированные блоки кода
    code_blocks = []
    # Единый паттерн для всех типов fenced blocks
    code_fence_pattern = r'```([a-zA-Z0-9+_-]*)\n?(.*?)```'
    
    def replace_code_block(match):
        idx = len(code_blocks)
        lang_part = match.group(1).strip() if match.group(1) else ''
        content = match.group(2)
        
        # Определяем, является ли lang_part реальным языком или частью inline fenced блока
        if lang_part and not content.startswith('\n') and '\n' not in content:
            # Это inline fenced блок - lang_part на самом деле часть содержимого
            code_blocks.append(('', lang_part + ' ' + content if content else lang_part))
        else:
            # Обычный fenced block
            lang = lang_part if lang_part else ''
            code = content
            code_blocks.append((lang, code))
        
        return f'__CODE_BLOCK_{idx}__'
    
    text = re.sub(code_fence_pattern, replace_code_block, text, flags=re.DOTALL)
    
    # Защищаем inline код
    inline_code_blocks = []
    inline_code_pattern = r'`([^`]+)`'
    
    def replace_inline_code(match):
        idx = len(inline_code_blocks)
        code = match.group(1)
        inline_code_blocks.append(code)
        return f'__INLINE_CODE_{idx}__'
    
    text = re.sub(inline_code_pattern, replace_inline_code, text)
    
    # Команды, которые должны быть обнаружены
    command_prefixes = [
        r'repo\s+', r'git\s+', r'mkdir\s+', r'cd\s+', r'docker\s+', r'kubectl\s+',
        r'python\s+', r'pip\s+', r'make\s+', r'cmake\s+', r'ninja\s+', r'sudo\s+'
    ]
    
    def is_command_line(line: str) -> bool:
        """Проверить, является ли строка командной строкой"""
        line_stripped = line.strip()
        if not line_stripped:
            return False

        if line_stripped.startswith("$ "):
            return True

        # Проверяем начало строки на команды
        for prefix_pattern in command_prefixes:
            if re.match(prefix_pattern, line_stripped, re.IGNORECASE):
                return True

        if line_stripped.startswith("./"):
            return True

        return False
    
    # Разбиваем текст на строки и ищем последовательности командных строк
    lines = text.split('\n')
    result_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Если это командная строка, собираем последовательность команд
        if is_command_line(line):
            command_lines = [line]
            i += 1
            
            # Собираем следующие командные строки подряд
            while i < len(lines) and is_command_line(lines[i]):
                command_lines.append(lines[i])
                i += 1
            
            # Если 2+ командных строки подряд - оборачиваем в блок кода
            if len(command_lines) >= 2:
                commands_text = '\n'.join(command_lines)
                result_lines.append(f'```bash\n{commands_text}\n```')
            else:
                # Одна командная строка
                cmd = command_lines[0].strip()
                # Если короткая (до 60 символов) - inline код, иначе блок
                if len(cmd) <= 60:
                    result_lines.append(f'`{cmd}`')
                else:
                    result_lines.append(f'```bash\n{cmd}\n```')
        else:
            result_lines.append(line)
            i += 1
    
    text = '\n'.join(result_lines)
    
    # Восстанавливаем inline код
    for idx, code in enumerate(inline_code_blocks):
        text = text.replace(f'__INLINE_CODE_{idx}__', f'`{code}`')
    
    # Восстанавливаем блоки кода
    for idx, (lang, code) in enumerate(code_blocks):
        if lang:
            text = text.replace(f'__CODE_BLOCK_{idx}__', f'```{lang}\n{code}```')
        else:
            text = text.replace(f'__CODE_BLOCK_{idx}__', f'```\n{code}```')
    
    return text


def clean_citations(text: str) -> str:
    """Очистить ответ от некорректных тегов citations"""
    import re
    
    if not text:
        return ""
    
    # Сначала обрабатываем блоки кода, чтобы не трогать их содержимое
    code_blocks = []
    # Улучшенный паттерн: ищем ```lang или ```, затем содержимое до закрывающего ```
    # Используем более точный паттерн для правильной обработки
    code_block_pattern = r'```([a-zA-Z0-9+_-]*)\s*\n(.*?)```'
    
    def replace_code_block(match):
        idx = len(code_blocks)
        lang = match.group(1).strip() if match.group(1) else ''
        code = match.group(2)
        # Убираем citations из кода сразу при извлечении
        code_clean = re.sub(r'\[source_id\][^\]]+\]', '', code)
        code_clean = re.sub(r'\[/source_id\]', '', code_clean)
        code_blocks.append((lang, code_clean))
        return f'__CODE_BLOCK_{idx}__'
    
    # Заменяем блоки кода на плейсхолдеры (нежадный поиск)
    text = re.sub(code_block_pattern, replace_code_block, text, flags=re.DOTALL)
    
    # Обрабатываем случаи, когда блок кода без языка и без переноса строки
    # Паттерн: ```text``` (без языка и переноса)
    simple_code_pattern = r'```([^`\n]+?)```'
    def replace_simple_code(match):
        idx = len(code_blocks)
        code = match.group(1)
        code_clean = re.sub(r'\[source_id\][^\]]+\]', '', code)
        code_clean = re.sub(r'\[/source_id\]', '', code_clean)
        code_blocks.append(('', code_clean))
        return f'__CODE_BLOCK_{idx}__'
    
    text = re.sub(simple_code_pattern, replace_simple_code, text)
    
    # ВНИМАНИЕ: агрессивные исправления "экзотики" могут ломать нормальный текст.
    # Поэтому не преобразуем [/source_id]... </source_id> в квадратные скобки.
    # (Достаточно удалить закрывающие теги и мусорные source_id в следующих шагах.)
    
    # Удаляем закрывающие теги [/source_id] - они не нужны
    text = re.sub(r'\[/source_id\]', '', text)
    
    # Удаляем дублирующиеся [source_id] теги подряд
    text = re.sub(r'\[source_id\]([^\]]+)\s*\[source_id\]', r'[source_id]\1', text)
    
    # Удаляем пустые citations [source_id] с пробелами
    text = re.sub(r'\[source_id\]\s+\[source_id\]', '[source_id]', text)
    
    # Исправляем случаи, когда citation находится сразу после закрывающего ``` без пробела
    text = re.sub(r'```\s*\[source_id\]', '```\n\n[source_id]', text)
    
    # Убираем citations, которые находятся в начале строки без текста перед ними
    # (кроме случаев, когда это начало нового пункта списка)
    text = re.sub(r'^\s*\[source_id\]([^\]]+)\]\s*$', '', text, flags=re.MULTILINE)
    
    # Убираем лишние пробелы вокруг citations
    text = re.sub(r'\s+\[source_id\]', ' [source_id]', text)
    text = re.sub(r'\[source_id\]\s+', '[source_id]', text)
    
    # НЕ пытаемся "двигать" citations через regex вокруг fenced-блоков:
    # это часто ломает код и структуру markdown. Мы уже защищаем fenced blocks плейсхолдерами выше.
    
    # Восстанавливаем блоки кода
    for idx, (lang, code) in enumerate(code_blocks):
        # Код уже очищен от citations при извлечении
        # Формируем правильный блок кода
        if lang:
            text = text.replace(f'__CODE_BLOCK_{idx}__', f'```{lang}\n{code}```')
        else:
            # Для блоков без языка проверяем, нужен ли перенос строки
            if code and not code.startswith('\n'):
                text = text.replace(f'__CODE_BLOCK_{idx}__', f'```\n{code}```')
            else:
                text = text.replace(f'__CODE_BLOCK_{idx}__', f'```{code}```')
    
    # Исправляем незакрытые блоки кода (если есть ``` без закрывающего)
    # Просто закрываем в конце, если нужно
    open_count = text.count('```')
    if open_count % 2 != 0:
        # Есть незакрытый блок кода - просто добавляем закрывающий в конце
        text = text + '\n```'
    
    # Финальная очистка: убираем множественные пробелы и переносы строк
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' \n', '\n', text)
    
    return text.strip()


def format_markdown_to_html(text: str) -> str:
    """Конвертировать markdown-подобные конструкции в HTML для Telegram (безопасно)"""
    from html import escape
    import re
    
    if not text:
        return ""
    
    # Сначала обрабатываем блоки кода (чтобы не экранировать их содержимое)
    code_blocks = []
    # Единый паттерн для всех типов fenced blocks: ```lang\n...\n```, ```\n...\n```, ```something```
    code_fence_pattern = r'```([a-zA-Z0-9+_-]*)\n?(.*?)```'
    
    def replace_code_block(match):
        idx = len(code_blocks)
        lang_part = match.group(1).strip() if match.group(1) else ''
        content = match.group(2)
        
        # Определяем, является ли lang_part реальным языком или частью inline fenced блока
        # Если lang_part есть, но content не начинается с \n и не содержит \n, 
        # считаем это inline fenced (например: ```bash echo 1```)
        if lang_part and not content.startswith('\n') and '\n' not in content:
            # Это inline fenced блок - lang_part на самом деле часть содержимого
            code_blocks.append(('', lang_part + ' ' + content if content else lang_part))
        else:
            # Обычный fenced block
            lang = lang_part if lang_part else ''
            code = content
            code_blocks.append((lang, code))
        
        return f'__CODE_BLOCK_{idx}__'
    
    # Заменяем блоки кода на плейсхолдеры
    text = re.sub(code_fence_pattern, replace_code_block, text, flags=re.DOTALL)
    
    # Обрабатываем inline код перед экранированием (чтобы не экранировать содержимое кода)
    inline_code_blocks = []
    inline_code_pattern = r'`([^`]+?)`'
    
    def replace_inline_code(match):
        idx = len(inline_code_blocks)
        inline_code_blocks.append(match.group(1))
        return f'__INLINE_CODE_{idx}__'
    
    text = re.sub(inline_code_pattern, replace_inline_code, text)
    
    # Экранируем HTML символы в остальном тексте
    text = html_escape(text)
    
    # Обрабатываем заголовки (после escape, чтобы не экранировать дважды)
    # Заголовки ### -> <b> (с новой строки)
    text = re.sub(r'###\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Заголовки ## -> <b> (с новой строки)
    text = re.sub(r'##\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Заголовки # -> <b> (с начала строки)
    text = re.sub(r'^#\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    
    # **текст** -> <b>текст</b> (но не внутри тегов)
    # Используем негативный lookahead/lookbehind чтобы не заменять внутри HTML тегов
    text = re.sub(r'(?<!<)\*\*([^*]+?)\*\*(?!>)', r'<b>\1</b>', text)
    
    # *текст* -> <i>текст</i> (но не **текст** и не внутри тегов)
    # Ограничение: курсив только если вокруг есть границы слова/пробелы, чтобы не ломать математику и списки
    text = re.sub(r'(?<!<)(?<!\*)\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)(?!>)', r'<i>\1</i>', text)
    
    # Обрабатываем списки - заменяем на bullet points с правильным форматированием
    # Нумерованные списки (1. 2. 3.) -> • (с начала строки)
    text = re.sub(r'^\d+\.\s+(.+?)(?=\n|$)', r'• \1', text, flags=re.MULTILINE)
    # Маркированные списки (- или *) -> • (с начала строки, но не * для курсива)
    text = re.sub(r'^[-]\s+(.+?)(?=\n|$)', r'• \1', text, flags=re.MULTILINE)
    # Маркированные списки (* в начале строки с пробелом) -> •
    text = re.sub(r'^\*\s+(.+?)(?=\n|$)', r'• \1', text, flags=re.MULTILINE)
    
    # Убираем множественные переносы строк (более 2 подряд -> 2)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Восстанавливаем inline код (содержимое уже экранировано)
    for idx, code in enumerate(inline_code_blocks):
        code_escaped = html_escape(code)
        text = text.replace(f'__INLINE_CODE_{idx}__', f'<code>{code_escaped}</code>')

    # Восстанавливаем блоки кода (содержимое уже экранировано)
    for idx, (lang, code) in enumerate(code_blocks):
        code_escaped = html_escape(code)
        text = text.replace(
            f'__CODE_BLOCK_{idx}__',
            f'<pre><code>{code_escaped}</code></pre>'
        )
    
    return text.strip()


def strip_service_markup(text: str) -> str:
    """
    Удаляет служебные XML-теги и блоки из ответа LLM.
    Защищает code blocks от случайного удаления команд.
    """
    if not text:
        return ""
    
    import re
    
    # Шаг 1: Защищаем code blocks плейсхолдерами (как в clean_citations)
    code_blocks = []
    
    # Блоки кода с языком: ```lang\ncode\n```
    code_block_pattern = r'```([a-zA-Z0-9+_-]*)\s*\n(.*?)```'
    def replace_code_block(match):
        idx = len(code_blocks)
        lang = match.group(1).strip() if match.group(1) else ''
        code = match.group(2)
        code_blocks.append((lang, code))
        return f'__CODE_BLOCK_{idx}__'
    
    text = re.sub(code_block_pattern, replace_code_block, text, flags=re.DOTALL)
    
    # Блоки кода без языка: ```text```
    simple_code_pattern = r'```([^`\n]+?)```'
    def replace_simple_code(match):
        idx = len(code_blocks)
        code = match.group(1)
        code_blocks.append(('', code))
        return f'__CODE_BLOCK_{idx}__'
    
    text = re.sub(simple_code_pattern, replace_simple_code, text)
    
    # Inline код: `code`
    inline_code_blocks = []
    inline_code_pattern = r'`([^`]+?)`'
    def replace_inline_code(match):
        idx = len(inline_code_blocks)
        inline_code_blocks.append(match.group(1))
        return f'__INLINE_CODE_{idx}__'
    
    text = re.sub(inline_code_pattern, replace_inline_code, text)
    
    # Шаг 2: Удаляем служебные блоки и теги
    
    # Удаляем блок <context>...</context> целиком (включая теги)
    text = re.sub(r'<context>.*?</context>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Удаляем служебные XML-теги
    service_tags = [
        r'<source_id>.*?</source_id>',
        r'<doc_title>.*?</doc_title>',
        r'<section_path>.*?</section_path>',
        r'<chunk_kind>.*?</chunk_kind>',
        r'<content>',
        r'</content>',
        r'<user_query>',
        r'</user_query>',
    ]
    
    for pattern in service_tags:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Удаляем типовые "служебные вступления"
    service_intros = [
        r'Based on the context provided[,\s]*',
        r'From source_id[,\s]*',
        r'From source_id\s+"[^"]*"[,\s]*',
    ]
    
    for pattern in service_intros:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Удаляем пустые строки, оставшиеся после удаления тегов
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r'^\s+', '', text, flags=re.MULTILINE)
    
    # Шаг 3: Восстанавливаем code blocks
    for idx, (lang, code) in enumerate(code_blocks):
        if lang:
            text = text.replace(f'__CODE_BLOCK_{idx}__', f'```{lang}\n{code}```')
        else:
            # Всегда восстанавливаем fenced блоки с переносом строки после ```
            text = text.replace(f'__CODE_BLOCK_{idx}__', f'```\n{code}```')
    
    for idx, code in enumerate(inline_code_blocks):
        text = text.replace(f'__INLINE_CODE_{idx}__', f'`{code}`')
    
    return text.strip()


def format_for_telegram_answer(text: str, enable_citations: bool = True) -> str:
    """
    Единый пайплайн форматирования ответа для отправки в Telegram.
    
    Порядок обработки:
    0. strip_service_markup() - удаление служебных тегов и блоков (защищает code blocks)
    1. clean_citations() - очистка citations (не трогает содержимое code blocks)
    2. format_commands_in_text() - автоматическое форматирование командных строк
    3. format_markdown_to_html() - конвертация markdown в HTML
    
    Возвращает HTML-текст, готовый для отправки с parse_mode="HTML".
    
    ВАЖНО: После этого НЕ используйте clean_text_for_telegram() на результате!
    """
    if not text:
        return ""
    
    # Шаг 0: Удаление служебных тегов и блоков (страховка от утечек)
    text = strip_service_markup(text)

    # Нормализуем заголовки разделов для читаемого форматирования
    text = re.sub(r'(?m)^\s*(Main Answer)\s*:\s*', r'## \1\n', text)
    text = re.sub(r'(?m)^\s*(Additionally Found|Additional Information)\s*:\s*', r'## \1\n', text)
    text = re.sub(r'(?m)^\s*(Основной ответ)\s*:\s*', r'## \1\n', text)
    text = re.sub(r'(?m)^\s*(Дополнительно(?: найдено)?)\s*:\s*', r'## \1\n', text)
    text = re.sub(r'\b(Main Answer|Additionally Found|Additional Information)\s*:\s*', r'## \1\n', text)
    text = re.sub(r'\b(Основной ответ|Дополнительно(?: найдено)?)\s*:\s*', r'## \1\n', text)
    
    # Шаг 1: Очистка citations (защищает содержимое code blocks)
    if enable_citations:
        text = clean_citations(text)
    
    # Шаг 2: Автоматическое форматирование командных строк
    text = format_commands_in_text(text)
    
    # Шаг 3: Конвертация markdown в HTML
    html = format_markdown_to_html(text)
    
    return html


def create_prompt_with_language(query: str, context: Optional[str] = None, task: str = "answer", enable_citations: bool = True) -> str:
    """Создать промпт с учетом языка запроса и поддержкой inline citations"""
    language = detect_language(query)
    
    # Проверить настройку citations из конфига
    try:
        from shared.config import RAG_ENABLE_CITATIONS
        enable_citations = RAG_ENABLE_CITATIONS and enable_citations
    except ImportError:
        pass
    
    if language == 'ru':
        if task == "answer":
            if context:
                prompt = f"""### Task:

Ответь на вопрос пользователя, используя предоставленный контекст. Структурируй ответ следующим образом:

1. **Основной ответ** - наиболее релевантная информация с inline citations [source_id] (только если в контексте присутствует строка SOURCE_ID: для соответствующего источника)
2. **Дополнительная информация** (если есть) - кратко упомяни другие найденные релевантные темы со ссылками на источники

### Guidelines:

- **Если вопрос неясен или требует уточнения**, четко скажи об этом и попроси пользователя уточнить запрос.
- **Если информации слишком много и нужно уточнить**, предложи пользователю конкретизировать вопрос.
- Если ты не знаешь ответа, четко скажи об этом.
- Отвечай на том же языке, что и запрос пользователя.
- Если контекст нечитаемый или низкого качества, сообщи пользователю об этом.
- **КРИТИЧЕСКИ ВАЖНО: Используй ТОЛЬКО информацию, которая явно присутствует в предоставленном контексте.**
- **НИКОГДА не выводи текст из блока <context>...</context> в ответ.**
- **НИКОГДА не выводи любые XML/служебные теги/поля, включая <context>, <user_query>, <source_id>, <doc_title>, <section_path>, <chunk_kind>, <content> и любые похожие.**
- **Если в ответе нужно ссылаться на источник — используй только inline citation [source_id] (если он есть), но не печатай <source_id>...</source_id>.**
- **НЕ придумывай команды, URL, пути к файлам или другую информацию, которой нет в контексте.**
- **Если в контексте нет конкретной команды или URL, НЕ выдумывай их - скажи, что этой информации нет в базе знаний.**
- **Если ответа нет в предоставленном контексте, четко скажи пользователю, что в базе знаний нет информации по этому вопросу.**
- **Включай inline citations используя [source_id] только если в контексте присутствует строка SOURCE_ID: для соответствующего источника.**
- **НЕ используй закрывающие теги [/source_id] - используй только [source_id]**
- Не используй citations, если строка SOURCE_ID: отсутствует в контексте.
- Не используй XML теги в ответе.
- Убедись, что citations кратки и напрямую связаны с предоставленной информацией.
- **НЕ размещай citations внутри блоков кода - размещай их после закрывающего ```**
- **При цитировании команд или URL используй ТОЛЬКО те, которые точно указаны в контексте.**
- **Форматируй все команды в блоки кода: ```bash\nкоманда\n``` или как inline код: `команда`**
- **Если в контексте указана команда, обязательно форматируй её как код.**
- **Если информация неполная или отсутствует, НЕ выдумывай недостающие части - скажи, что информации нет.**
- **Приоритет: сначала дай наиболее релевантный и полный ответ, затем кратко упомяни другие найденные темы.**

### Формат ответа:

**Основной ответ:**
[Детальный ответ на вопрос с citations [source_id], если они есть в контексте]

**Дополнительно найдено:** (только если есть дополнительная релевантная информация)
- [Краткое упоминание других релевантных тем с citations, если они есть в контексте]

### Важные правила форматирования:

- **Все команды должны быть в блоках кода**: ```bash\nкоманда\n``` или как inline код: `команда`
- **Если команда длинная или многострочная, используй блок кода с языком (bash, sh, и т.д.)**
- **Если команда короткая (одна строка), используй inline код: `команда`**
- **НЕ повторяй одну и ту же информацию дважды в одном пункте**
- **Если информация неполная (например, есть только для Windows, но нет для Mac), НЕ выдумывай недостающие части - явно укажи, что информации нет**
- **НЕ создавай пункты типа "Run X for Y: X" - это бессмысленное повторение. Если информации нет, просто скажи об этом.**
- **Если в контексте указано только название команды без деталей, НЕ выдумывай детали - просто упомяни название команды.**
- **Не копируй шаблон ответа и не вставляй примерные источники/ссылки.**

### Output:

Дай структурированный ответ: сначала основной ответ с citations (если есть в контексте), затем кратко дополнительная информация (если есть).

<context>

{context}

</context>

<user_query>

{query}

</user_query>"""
            else:
                prompt = f"""Ты помощник. Ответь на вопрос пользователя на русском языке.

Вопрос: {query}

Ответь подробно и точно на русском языке:"""
        elif task == "search_summary":
            prompt = f"""Проанализируй результаты поиска в интернете и дай структурированный, читаемый ответ на русском языке на вопрос: {query}

### Требования к ответу:
- Используй заголовки, списки, отступы для лучшей читаемости
- Структурируй информацию логически
- Выделяй важные моменты жирным текстом (**текст**)
- Используй списки для перечислений
- Делай ответ читаемым и структурированным

Результаты поиска:
{context}

Дай структурированный, читаемый ответ на русском языке с использованием форматирования:"""
        else:
            prompt = query
    else:
        if task == "answer":
            if context:
                citations_instruction = ""
                if enable_citations:
                    citations_instruction = """
- Use inline citations in the format [source_id] **only if the context contains a SOURCE_ID: line for the corresponding source**.
- **DO NOT use closing tags [/source_id] - use only [source_id] at the start of citation**
- Do not cite if the SOURCE_ID: line is not present in the context.
- Ensure citations are concise and directly related to the information provided.
- **DO NOT place citations inside code blocks (```...```) - place them after the code block"""
                
                prompt = f"""### Task:

Respond to the user query using the provided context. Structure your response as follows:

1. **Main Answer** - most relevant information with inline citations [source_id] (only if the context contains a SOURCE_ID: line for the corresponding source)
2. **Additional Information** (if available) - briefly mention other relevant topics found with source references

### Guidelines:

- **If the question is unclear or requires clarification**, clearly state this and ask the user to clarify the query.
- **If there is too much information and clarification is needed**, suggest the user to specify the question.
- If you don't know the answer, clearly state that.
- Respond in the same language as the user's query.
- If the context is unreadable or of poor quality, inform the user about this.
- **CRITICALLY IMPORTANT: Use ONLY information that is explicitly present in the provided context.**
- **Never output any text from <context>...</context>.**
- **Never output XML/service tags, including <context>, <user_query>, <source_id>, <doc_title>, <section_path>, <chunk_kind>, <content>, etc.**
- **Use citations only as [source_id], never the raw tags.**
- **DO NOT make up commands, URLs, file paths, or any other information that is not in the context.**
- **If a specific command or URL is not in the context, DO NOT invent it - tell the user that this information is not in the knowledge base.**
- **If the answer isn't present in the provided context, clearly tell the user that there is no information in the knowledge base about this question.**
- **Only include inline citations using [source_id] if the context contains a SOURCE_ID: line for the corresponding source.**
- **DO NOT use closing tags [/source_id] - use only [source_id]**
- Do not cite if the SOURCE_ID: line is not present in the context.
- Do not use XML tags in your response.
- Ensure citations are concise and directly related to the information provided.
- **DO NOT place citations inside code blocks - place them after the closing ```**
- **When quoting commands or URLs, use ONLY those that are exactly specified in the context.**
- **Format all commands as code blocks: ```bash\ncommand\n``` or as inline code: `command`**
- **If a command is mentioned in the context, always format it as code.**
- **If information is incomplete or missing, DO NOT make up missing parts - say that the information is not available.**
- **DO NOT repeat the same information twice in one point (e.g., "Run X for Y: X" is meaningless).**
- **If the context only mentions a command name without details, DO NOT make up details - just mention the command name.**
- **Priority: first give the most relevant and complete answer, then briefly mention other topics found.**

### Response Format:

**Main Answer:**
[Detailed answer to the question with citations [source_id] where necessary]

**Additionally Found:** (only if there is additional relevant information)
- [Brief mention of other relevant topics with citations if present in context]

### Important formatting rules:

- **All commands must be in code blocks**: ```bash\ncommand\n``` or as inline code: `command`
- **If a command is long or multi-line, use a code block with language (bash, sh, etc.)**
- **If a command is short (single line), use inline code: `command`**
- **DO NOT repeat the same information twice in one point**
- **If information is incomplete (e.g., only for Windows but not for Mac), DO NOT make up missing information - explicitly state that the information is not available**
- **Do not copy the response template or insert example sources/links.**

### Output:

Provide a structured response: first the main answer with citations (if present in context), then briefly additional information (if any).

<context>

{context}

</context>

<user_query>

{query}

</user_query>"""
            else:
                prompt = f"""You are a helpful assistant. Answer the user's question in English.

Question: {query}

Answer in detail and accurately in English:"""
        elif task == "search_summary":
            prompt = f"""Analyze the web search results and provide a structured, readable answer in English to the question: {query}

### Response Requirements:
- Use headers, lists, indentation for better readability
- Structure information logically
- Highlight important points with bold text (**text**)
- Use lists for enumerations
- Make the answer readable and structured

Search results:
{context}

Provide a structured, readable answer in English with formatting:"""
        else:
            prompt = query
    
    return prompt

