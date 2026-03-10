"""
Загрузчик для исходного кода с извлечением символов.
"""
import os
import re
from typing import List, Dict

from .base import DocumentLoader
from .chunking import split_code_into_chunks


_CODE_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".hpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
}


def _read_text_file(path: str) -> str:
    encodings = ["utf-8", "utf-8-sig", "cp1251", "latin-1", "windows-1251"]
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def extract_symbols(text: str) -> List[str]:
    symbols = set()
    patterns = [
        r"(?m)^\s*def\s+([A-Za-z_]\w*)",
        r"(?m)^\s*class\s+([A-Za-z_]\w*)",
        r"(?m)^\s*function\s+([A-Za-z_]\w*)",
        r"(?m)^\s*async\s+function\s+([A-Za-z_]\w*)",
        r"(?m)^\s*func\s+([A-Za-z_]\w*)",
        r"(?m)^\s*(struct|enum|trait)\s+([A-Za-z_]\w*)",
        r"(?m)^\s*interface\s+([A-Za-z_]\w*)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if isinstance(match, tuple):
                symbols.add(match[-1])
            else:
                symbols.add(match)
    return sorted(symbols)


class CodeLoader(DocumentLoader):
    """Загрузчик для исходного кода"""

    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        try:
            content = _read_text_file(source)
            doc_title = os.path.basename(source) or ""
            if "." in doc_title:
                doc_title = os.path.splitext(doc_title)[0]
            section_path = doc_title or "ROOT"
            file_name = os.path.basename(source)
            if not content:
                return [{
                    "content": "",
                    "title": doc_title,
                    "metadata": {
                        "type": "code",
                        "chunk_kind": "code",
                        "parser_profile": "loader:code:v1",
                        "file_path": file_name,
                        "doc_title": doc_title,
                        "section_title": doc_title,
                        "section_path": section_path,
                    },
                }]

            ext = os.path.splitext(source)[1].lower()
            code_lang = _CODE_LANG_BY_EXT.get(ext, "")

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

            base_parts = split_code_into_chunks(content, max_chars=max_chars, overlap=0)
            symbols = extract_symbols(content)

            try:
                effective_overlap = int(overlap) if overlap is not None else 0
            except (ValueError, TypeError):
                effective_overlap = 0
            if effective_overlap < 0:
                effective_overlap = 0

            chunks: List[Dict[str, str]] = []
            search_start = 0
            for idx, base_part in enumerate(base_parts, start=1):
                char_start = content.find(base_part, search_start)
                if char_start < 0:
                    char_start = search_start
                char_end = char_start + len(base_part)
                part = base_part
                if effective_overlap > 0 and idx > 1:
                    prev = base_parts[idx - 2]
                    overlap_text = prev[-effective_overlap:] if len(prev) > effective_overlap else prev
                    if overlap_text:
                        part = overlap_text + "\n\n" + base_part

                chunk_symbols = extract_symbols(base_part)
                primary_symbol = doc_title
                if len(base_parts) > 1 and chunk_symbols:
                    primary_symbol = chunk_symbols[0]
                title = primary_symbol if primary_symbol != doc_title else f"{doc_title} (фрагмент {idx})"
                chunk_section_path = doc_title or "ROOT"
                if primary_symbol and primary_symbol != doc_title:
                    chunk_section_path = f"{doc_title} > {primary_symbol}"
                chunks.append({
                    "content": part,
                    "title": title,
                    "metadata": {
                        "type": "code",
                        "chunk_kind": "code",
                        "code_lang": code_lang,
                        "symbols": symbols,
                        "chunk_symbols": chunk_symbols,
                        "primary_symbol": primary_symbol,
                        "chunk_no": idx,
                        "char_start": char_start,
                        "char_end": char_end,
                        "parser_profile": "loader:code:v1",
                        "file_path": file_name,
                        "doc_title": doc_title,
                        "section_title": primary_symbol if len(base_parts) > 1 else doc_title,
                        "section_path": chunk_section_path if len(base_parts) > 1 else section_path,
                    },
                })
                search_start = char_end
            return chunks
        except Exception as e:
            return [{"content": f"Ошибка загрузки кода: {str(e)}", "title": "", "metadata": {}}]
