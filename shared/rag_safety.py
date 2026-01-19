"""
Safety helpers for RAG responses.
"""
import re


def strip_unknown_citations(answer: str, context: str) -> str:
    if not answer:
        return answer

    def norm_id(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip())

    allowed_ids = set()
    for line in (context or "").splitlines():
        if line.startswith("SOURCE_ID:"):
            allowed_ids.add(norm_id(line.split(":", 1)[1]))

    if not allowed_ids:
        return re.sub(r"\[source_id[^\]]*\]", "", answer)

    def replace(match: re.Match) -> str:
        raw_id = norm_id(match.group(1))
        return match.group(0) if raw_id in allowed_ids else ""

    answer = re.sub(r"\[source_id\]([^\]]+)\]", replace, answer)
    answer = re.sub(r"\[source_id:\s*([^\]]+)\]", replace, answer)
    return answer


def strip_untrusted_urls(answer: str, context: str) -> str:
    if not answer or not context:
        return answer

    context_norm = re.sub(r"\s+", " ", context).lower()

    def url_in_context(url: str) -> bool:
        return url.lower() in context_norm

    def replace_markdown_link(match: re.Match) -> str:
        text = match.group(1) or ""
        url = match.group(2) or ""
        return match.group(0) if url_in_context(url) else text

    answer = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_markdown_link, answer)

    def replace_bare_url(match: re.Match) -> str:
        url = match.group(1)
        return url if url_in_context(url) else ""

    answer = re.sub(r"(https?://\S+)", replace_bare_url, answer)
    answer = re.sub(r"\s{2,}", " ", answer).strip()
    return answer


def sanitize_commands_in_answer(answer: str, context: str) -> str:
    if not answer or not context:
        return answer

    context_norm = re.sub(r"\s+", " ", context).lower()
    command_prefixes = (
        "git ",
        "repo ",
        "./",
        "bash ",
        "python ",
        "pip ",
        "cmake ",
        "make ",
        "ninja ",
        "docker ",
        "kubectl ",
        "sudo ",
        "apt ",
        "yum ",
        "npm ",
        "yarn ",
    )

    def is_command_line(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if s.startswith("$ "):
            s = s[2:].lstrip()
        if s.startswith(command_prefixes):
            return True
        if " && " in s or s.startswith("cd "):
            return True
        return False

    def line_in_context(line: str) -> bool:
        s = re.sub(r"\s+", " ", line.strip()).lower()
        return bool(s) and s in context_norm

    def contains_wiki_url(line: str) -> bool:
        return "/wikis/" in line or "#sync" in line or "#build" in line

    code_pattern = r"```([a-zA-Z0-9+_-]*)\n(.*?)```"
    removed_any = False

    def replace_code(match: re.Match) -> str:
        nonlocal removed_any
        lang = match.group(1) or ""
        body = match.group(2) or ""
        lines = body.splitlines()
        kept = []
        for ln in lines:
            if is_command_line(ln):
                if contains_wiki_url(ln) or not line_in_context(ln):
                    removed_any = True
                    continue
            kept.append(ln)
        if not kept:
            return "Команда отсутствует в базе знаний."
        return f"```{lang}\n" + "\n".join(kept) + "\n```"

    answer = re.sub(code_pattern, replace_code, answer, flags=re.DOTALL)

    def replace_inline(match: re.Match) -> str:
        nonlocal removed_any
        code = match.group(1) or ""
        if is_command_line(code):
            if contains_wiki_url(code) or not line_in_context(code):
                removed_any = True
                return "команда отсутствует в базе знаний"
        return match.group(0)

    answer = re.sub(r"`([^`]+)`", replace_inline, answer)
    if removed_any and len(answer.strip()) < 80:
        return (
            "В найденных источниках нет точных команд для сборки/запуска по вашему запросу. "
            "Уточните компонент или платформу (например, C-API, XTS, e2e)."
        )
    return answer
