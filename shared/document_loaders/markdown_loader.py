"""
Загрузчик для Markdown файлов
"""
import re
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_markdown_section_into_chunks


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
                    language = match.group(1) or ""
                    code_content = match.group(2)
                    code_counter += 1
                    placeholder = f"__CODE_BLOCK_{code_counter}__"
                    code_blocks.append({
                        'content': code_content,
                        'language': language,
                        'placeholder': placeholder
                    })
                    return placeholder
                
                pattern = r"```(\w+)?\n(.*?)```"
                text_with_placeholders = re.sub(pattern, replace_code_block, text, flags=re.DOTALL)
                return text_with_placeholders, code_blocks
            
            def _markdown_to_plain(text: str) -> str:
                """Очистить markdown-разметку"""
                text = re.sub(r"`([^`]+)`", r"\1", text)
                text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", text)
                text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
                text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
                text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
                text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
                text = re.sub(r"^[>\-\*+]\s+", "", text, flags=re.MULTILINE)
                text = re.sub(r"\|", " ", text)
                text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
                text = re.sub(r"\s+\n", "\n", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()

            # Разбиение по заголовкам в оригинальном markdown (до очистки)
            sections: List[Dict[str, str]] = []
            current_section_lines = []
            current_title = ""
            
            for line in content.split("\n"):
                header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
                if header_match:
                    if current_section_lines:
                        section_content = "\n".join(current_section_lines).strip()
                        if section_content:
                            sections.append({
                                "content": section_content,
                                "title": current_title,
                            })
                    current_title = header_match.group(2).strip()
                    current_section_lines = [line]
                else:
                    current_section_lines.append(line)
            
            if current_section_lines:
                section_content = "\n".join(current_section_lines).strip()
                if section_content:
                    sections.append({
                        "content": section_content,
                        "title": current_title,
                    })
            
            if not sections:
                sections = [{"content": content, "title": ""}]

            # Чанкинг внутри каждой секции
            chunks: List[Dict[str, str]] = []
            for sec in sections:
                sec_text_raw = sec.get("content") or ""
                sec_title = sec.get("title") or ""
                
                sec_text_with_placeholders, code_blocks = _extract_code_blocks(sec_text_raw)
                sec_text_plain = _markdown_to_plain(sec_text_with_placeholders)
                
                for code_block in code_blocks:
                    code_content = code_block['content'].strip()
                    restored_code = f"\nCODE:\n{code_content}\n"
                    sec_text_plain = sec_text_plain.replace(code_block['placeholder'], restored_code)
                
                sec_chunks = split_markdown_section_into_chunks(sec_text_plain)
                for idx, part in enumerate(sec_chunks, start=1):
                    title = sec_title or ""
                    if len(sec_chunks) > 1:
                        title = f"{title} (фрагмент {idx})" if title else f"Фрагмент {idx}"
                    
                    chunk_kind = "code" if "CODE:\n" in part else "text"
                    
                    chunks.append({
                        "content": part,
                        "title": title,
                        "metadata": {
                            "type": "markdown",
                            "chunk_kind": chunk_kind,
                            "section_title": sec_title,
                        },
                    })

            return chunks if chunks else [
                {"content": content, "title": "", "metadata": {"type": "markdown"}}
            ]
        except Exception as e:
            return [{'content': f"Ошибка загрузки markdown: {str(e)}", 'title': '', 'metadata': {}}]

