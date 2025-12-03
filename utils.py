"""
Вспомогательные утилиты
"""
import re
from typing import Optional


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
    """Очистить текст от markdown символов для безопасной отправки в Telegram"""
    # Удалить markdown символы, которые могут вызвать проблемы
    # Заменяем на обычный текст
    text = text.replace('*', '').replace('_', '').replace('`', '').replace('~', '')
    text = text.replace('[', '').replace(']', '').replace('(', '').replace(')', '')
    # Удаляем множественные пробелы
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def format_text_safe(text: str, max_length: int = 4096) -> str:
    """Безопасное форматирование текста для Telegram"""
    # Очистить от markdown
    text = clean_text_for_telegram(text)
    
    # Обрезать если слишком длинный
    if len(text) > max_length:
        text = text[:max_length-50] + "\n\n... (сообщение обрезано)"
    
    return text


def format_markdown_to_html(text: str) -> str:
    """Конвертировать markdown-подобные конструкции в HTML для Telegram"""
    from html import escape
    import re
    
    # Сначала обрабатываем блоки кода (чтобы не экранировать их содержимое)
    code_blocks = []
    code_block_pattern = r'```([^`]+?)```'
    
    def replace_code_block(match):
        idx = len(code_blocks)
        code_blocks.append(match.group(1))
        return f'__CODE_BLOCK_{idx}__'
    
    # Заменяем блоки кода на плейсхолдеры
    text = re.sub(code_block_pattern, replace_code_block, text, flags=re.DOTALL)
    
    # Экранируем HTML символы в остальном тексте
    text = escape(text)
    
    # Восстанавливаем блоки кода (они уже экранированы через escape)
    for idx, code in enumerate(code_blocks):
        # Экранируем код отдельно
        code_escaped = escape(code)
        text = text.replace(f'__CODE_BLOCK_{idx}__', f'<pre>{code_escaped}</pre>')
    
    # Обрабатываем inline код (после escape)
    text = re.sub(r'`([^`]+?)`', lambda m: f'<code>{m.group(1)}</code>', text)
    
    # Заголовки ### -> <b>
    text = re.sub(r'###\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Заголовки ## -> <b>
    text = re.sub(r'##\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Заголовки # -> <b>
    text = re.sub(r'^#\s+(.+?)(?=\n|$)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # **текст** -> <b>текст</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # *текст* -> <i>текст</i> (но не **текст**)
    text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', text)
    
    # Обрабатываем списки - заменяем на bullet points с правильным форматированием
    # Нумерованные списки (1. 2. 3.) -> • 
    text = re.sub(r'^\d+\.\s+(.+?)(?=\n|$)', r'• \1', text, flags=re.MULTILINE)
    # Маркированные списки (- или *) -> •
    text = re.sub(r'^[-*]\s+(.+?)(?=\n|$)', r'• \1', text, flags=re.MULTILINE)
    
    return text


def create_prompt_with_language(query: str, context: Optional[str] = None, task: str = "answer", enable_citations: bool = True) -> str:
    """Создать промпт с учетом языка запроса и поддержкой inline citations"""
    language = detect_language(query)
    
    # Проверить настройку citations из конфига
    try:
        from config import RAG_ENABLE_CITATIONS
        enable_citations = RAG_ENABLE_CITATIONS and enable_citations
    except ImportError:
        pass
    
    if language == 'ru':
        if task == "answer":
            if context:
                citations_instruction = ""
                if enable_citations:
                    citations_instruction = """
- Используй inline citations в формате [source_id] ТОЛЬКО когда в контексте явно указан тег <source_id>
- Не используй citations, если тег <source_id> отсутствует в контексте
- Citations должны быть краткими и напрямую связаны с предоставленной информацией"""
                
                prompt = f"""### Task:

Ответь на вопрос пользователя, используя предоставленный контекст. Структурируй ответ следующим образом:

1. **Основной ответ** - наиболее релевантная информация с inline citations [source_id] (только когда тег <source_id> явно указан в контексте)
2. **Дополнительная информация** (если есть) - кратко упомяни другие найденные релевантные темы со ссылками на источники

### Guidelines:

- **Если вопрос неясен или требует уточнения**, четко скажи об этом и попроси пользователя уточнить запрос.
- **Если информации слишком много и нужно уточнить**, предложи пользователю конкретизировать вопрос.
- Если ты не знаешь ответа, четко скажи об этом.
- Отвечай на том же языке, что и запрос пользователя.
- Если контекст нечитаемый или низкого качества, сообщи пользователю об этом.
- **ВАЖНО: Если ответа нет в предоставленном контексте, четко скажи пользователю, что в базе знаний нет информации по этому вопросу. НЕ придумывай ответы, которых нет в контексте.**
- **Включай inline citations используя [source_id] только когда тег <source_id> явно указан в контексте.**
- Не используй citations, если тег <source_id> отсутствует в контексте.
- Не используй XML теги в ответе.
- Убедись, что citations кратки и напрямую связаны с предоставленной информацией.
- **Приоритет: сначала дай наиболее релевантный и полный ответ, затем кратко упомяни другие найденные темы.**

### Format ответа:

**Основной ответ:**
[Детальный ответ на вопрос с citations [source_id] где необходимо]

**Дополнительно найдено:**
• [Краткое упоминание других релевантных тем] [source_id]

### Example:

**Основной ответ:**
Для синхронизации и сборки master версии O HoS используйте следующие команды:
1. Клонирование репозитория: `git clone https://...` [sync_build_guide]
2. Сборка: `cmake .. && make` [sync_build_guide]

**Дополнительно найдено:**
• Информация о настройке окружения [environment_setup]
• Документация по API [api_docs]

Если <source_id> отсутствует, ответ должен пропустить citation.

### Output:

Дай структурированный ответ: сначала основной ответ с citations, затем кратко дополнительная информация.

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
- Use inline citations in the format [source_id] **only when the <source_id> tag is explicitly provided** in the context.
- Do not cite if the <source_id> tag is not provided in the context.
- Ensure citations are concise and directly related to the information provided."""
                
                prompt = f"""### Task:

Respond to the user query using the provided context. Structure your response as follows:

1. **Main Answer** - most relevant information with inline citations [source_id] (only when the <source_id> tag is explicitly provided in the context)
2. **Additional Information** (if available) - briefly mention other relevant topics found with source references

### Guidelines:

- **If the question is unclear or requires clarification**, clearly state this and ask the user to clarify the query.
- **If there is too much information and clarification is needed**, suggest the user to specify the question.
- If you don't know the answer, clearly state that.
- Respond in the same language as the user's query.
- If the context is unreadable or of poor quality, inform the user about this.
- **IMPORTANT: If the answer isn't present in the provided context, clearly tell the user that there is no information in the knowledge base about this question. DO NOT make up answers that are not in the context.**
- **Only include inline citations using [source_id] when a <source_id> tag is explicitly provided in the context.**
- Do not cite if the <source_id> tag is not provided in the context.
- Do not use XML tags in your response.
- Ensure citations are concise and directly related to the information provided.
- **Priority: first give the most relevant and complete answer, then briefly mention other topics found.**

### Response Format:

**Main Answer:**
[Detailed answer to the question with citations [source_id] where necessary]

**Additionally Found:**
• [Brief mention of other relevant topics] [source_id]

### Example:

**Main Answer:**
To sync and build the master version of O HoS, use the following commands:
1. Clone repository: `git clone https://...` [sync_build_guide]
2. Build: `cmake .. && make` [sync_build_guide]

**Additionally Found:**
• Environment setup information [environment_setup]
• API documentation [api_docs]

If no <source_id> is present, the response should omit the citation.

### Output:

Provide a structured response: first the main answer with citations, then briefly additional information.

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

