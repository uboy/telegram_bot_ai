import os
from typing import Optional


def classify_document(content_sample: str, source_path: Optional[str] = None) -> str:
    """
    Heuristic classifier placeholder.
    Returns: text, code, table, markdown, config, log, mixed.
    """
    ext = ""
    if source_path:
        _, ext = os.path.splitext(source_path.lower())

    if ext in {".md", ".markdown"}:
        return "markdown"
    if ext in {".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".cs"}:
        return "code"
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".env"}:
        return "config"
    if ext in {".csv", ".tsv", ".xlsx", ".xls"}:
        return "table"
    if ext in {".log"}:
        return "log"

    sample = content_sample.lower()
    if "```" in sample or "class " in sample or "def " in sample:
        return "code"
    if "\n# " in sample or "\n## " in sample:
        return "markdown"
    if "{" in sample and "}" in sample and ":" in sample:
        return "config"
    if "," in sample and "\n" in sample:
        return "table"

    return "text"
