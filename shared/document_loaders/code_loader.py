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
            if not content:
                return [{"content": "", "title": "", "metadata": {"type": "code"}}]

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

            parts = split_code_into_chunks(content, max_chars=max_chars, overlap=overlap)
            symbols = extract_symbols(content)

            chunks: List[Dict[str, str]] = []
            for idx, part in enumerate(parts, start=1):
                chunks.append({
                    "content": part,
                    "title": f"Фрагмент {idx}",
                    "metadata": {
                        "type": "code",
                        "chunk_kind": "code",
                        "code_lang": code_lang,
                        "symbols": symbols,
                    },
                })
            return chunks
        except Exception as e:
            return [{"content": f"Ошибка загрузки кода: {str(e)}", "title": "", "metadata": {}}]
