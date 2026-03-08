"""
Safety helpers for RAG responses.
"""
import re
import shlex
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit


COMMAND_PREFIXES = (
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
SHELL_CONNECTORS = {"&&", "||", "|", ";"}
SIGNATURE_WRAPPERS = {"sudo"}
SCRIPT_SUFFIXES = (".sh", ".py", ".ps1", ".bat", ".cmd", ".exe")


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


def _clean_url_candidate(url: str) -> str:
    candidate = (url or "").strip().strip("`").strip("<>").strip("'\"")
    return candidate.rstrip(".,;")


def _url_variants(url: str) -> Set[str]:
    candidate = _clean_url_candidate(url)
    if not candidate.startswith(("http://", "https://")):
        return set()

    variants = {candidate, candidate.lower()}
    try:
        parsed = urlsplit(candidate)
        canonical = urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "",
                parsed.query or "",
                parsed.fragment or "",
            )
        )
        variants.add(canonical)
        variants.add(canonical.lower())
        decoded = unquote(canonical)
        variants.add(decoded)
        variants.add(decoded.lower())
        if canonical.endswith("/") and parsed.path not in ("", "/"):
            variants.add(canonical.rstrip("/"))
            variants.add(canonical.rstrip("/").lower())
        if decoded.endswith("/") and parsed.path not in ("", "/"):
            variants.add(decoded.rstrip("/"))
            variants.add(decoded.rstrip("/").lower())
    except Exception:
        pass
    return {variant for variant in variants if variant}


def _build_allowed_url_set(context: str, allowed_urls: Optional[Iterable[str]] = None) -> Set[str]:
    allowed: Set[str] = set()
    for match in re.findall(r"(https?://\S+)", context or ""):
        allowed.update(_url_variants(match))
    for url in allowed_urls or []:
        if isinstance(url, str):
            allowed.update(_url_variants(url))
    return allowed


def strip_untrusted_urls(answer: str, context: str, allowed_urls: Optional[Iterable[str]] = None) -> str:
    if not answer or (not context and not allowed_urls):
        return answer

    allowed_url_set = _build_allowed_url_set(context, allowed_urls)

    def url_in_context(url: str) -> bool:
        return any(variant in allowed_url_set for variant in _url_variants(url))

    preserved_links: List[str] = []

    def replace_markdown_link(match: re.Match) -> str:
        text = match.group(1) or ""
        url = match.group(2) or ""
        if url_in_context(url):
            placeholder = f"__URL_LINK_{len(preserved_links)}__"
            preserved_links.append(match.group(0))
            return placeholder
        return text

    answer = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_markdown_link, answer)

    def replace_bare_url(match: re.Match) -> str:
        url = match.group(1)
        return url if url_in_context(url) else ""

    answer = re.sub(r"(https?://\S+)", replace_bare_url, answer)
    for index, link in enumerate(preserved_links):
        answer = answer.replace(f"__URL_LINK_{index}__", link)
    answer = re.sub(r"\s{2,}", " ", answer).strip()
    return answer


def _normalize_command_line(line: str) -> str:
    s = (line or "").strip().strip("`")
    s = re.sub(r"^\s*(?:[-*]\s+|\d+\.\s+)", "", s)
    if s.startswith("$ "):
        s = s[2:].lstrip()
    return s.strip()


def _is_command_line(line: str) -> bool:
    s = _normalize_command_line(line)
    if not s:
        return False
    if s.startswith(COMMAND_PREFIXES):
        return True
    if any(connector in s for connector in (" && ", " || ", " | ", ";")) or s.startswith("cd "):
        return True
    tokens = _split_command_tokens(line)
    if not tokens:
        return False
    command_token = ""
    command_index = -1
    for index, token in enumerate(tokens):
        if token in SIGNATURE_WRAPPERS or token.startswith("-"):
            continue
        command_token = token
        command_index = index
        break
    if not command_token:
        return False
    remainder = tokens[command_index + 1 :]
    return _looks_like_generic_shell_command(command_token, remainder)


def _tokenize_shell_line(line: str, keep_connectors: bool = False) -> List[str]:
    normalized = _normalize_command_line(line)
    if not normalized:
        return []
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars="|&;")
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw_tokens = list(lexer)
    except Exception:
        raw_tokens = normalized.split()

    tokens: List[str] = []
    for token in raw_tokens:
        token = token.strip().strip("`").strip("'\"").rstrip(",.;")
        if not token:
            continue
        if token in SHELL_CONNECTORS:
            if keep_connectors:
                tokens.append(token)
            continue
        if token.startswith("$"):
            token = token.lstrip("$")
        if not token:
            continue
        tokens.append(token)
    return tokens


def _split_command_tokens(line: str) -> List[str]:
    tokens: List[str] = []
    for token in _tokenize_shell_line(line):
        long_opt = re.fullmatch(r"(--[A-Za-z0-9][A-Za-z0-9_-]*?)=(.+)", token)
        if long_opt:
            tokens.append(long_opt.group(1).lower())
            value = long_opt.group(2).strip("'\"").lower()
            if value:
                tokens.append(value)
            continue

        short_opt_value = re.fullmatch(r"(-[A-Za-z])(\d+)", token)
        if short_opt_value:
            tokens.append(short_opt_value.group(1).lower())
            tokens.append(short_opt_value.group(2).lower())
            continue

        short_opt_eq = re.fullmatch(r"(-[A-Za-z])=(.+)", token)
        if short_opt_eq:
            tokens.append(short_opt_eq.group(1).lower())
            value = short_opt_eq.group(2).strip("'\"").lower()
            if value:
                tokens.append(value)
            continue

        tokens.append(token.lower())
    return tokens


def _looks_like_path_or_artifact(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    return any(marker in token for marker in ("/", "\\", ".", ":", "="))


def _looks_like_executable(token: str) -> bool:
    if not token or token in SIGNATURE_WRAPPERS or token.startswith("-"):
        return False
    return bool(
        token.startswith(("./", ".\\", "/", "~"))
        or "/" in token
        or "\\" in token
        or token.endswith(SCRIPT_SUFFIXES)
        or re.fullmatch(r"[a-z0-9][a-z0-9_.:-]*", token)
    )


def _looks_like_generic_shell_command(command: str, remainder: List[str]) -> bool:
    if not _looks_like_executable(command):
        return False
    if any(token.startswith("-") for token in remainder):
        return True
    if any(_looks_like_path_or_artifact(token) for token in remainder):
        return True
    return False


def _command_signature(tokens: List[str]) -> Tuple[str, ...]:
    signature: List[str] = []
    for token in tokens:
        if token in SIGNATURE_WRAPPERS and not signature:
            continue
        if token.startswith("-"):
            if signature:
                break
            continue
        signature.append(token)
        if len(signature) == 2:
            break
    return tuple(signature)


def _split_command_segments(line: str) -> List[List[str]]:
    raw_tokens = _tokenize_shell_line(line, keep_connectors=True)
    if not raw_tokens:
        return []

    segments: List[List[str]] = []
    current: List[str] = []
    for token in raw_tokens:
        if token in SHELL_CONNECTORS:
            if current:
                segments.append(_split_command_tokens(" ".join(current)))
                current = []
            continue
        current.append(token)
    if current:
        segments.append(_split_command_tokens(" ".join(current)))
    return [segment for segment in segments if segment]


def _parse_command_shape(tokens: List[str]) -> Dict[str, object]:
    signature = _command_signature(tokens)
    options: List[Tuple[str, Optional[str]]] = []
    positionals: List[str] = []

    index = len(signature)
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            value: Optional[str] = None
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                value = tokens[index + 1]
                index += 1
            options.append((token, value))
        else:
            positionals.append(token)
        index += 1

    return {
        "tokens": tuple(tokens),
        "signature": signature,
        "options": tuple(options),
        "positionals": tuple(positionals),
    }


def _is_subsequence(needle: Tuple[object, ...], haystack: Tuple[object, ...]) -> bool:
    if not needle:
        return True
    offset = 0
    for item in haystack:
        if item == needle[offset]:
            offset += 1
            if offset == len(needle):
                return True
    return False


def _extract_context_command_catalog(context: str) -> List[Dict[str, object]]:
    candidates: List[str] = []
    for match in re.finditer(r"```[a-zA-Z0-9+_-]*\n(.*?)```", context or "", flags=re.DOTALL):
        body = match.group(1) or ""
        candidates.extend(body.splitlines())
    candidates.extend(re.findall(r"`([^`]+)`", context or ""))
    candidates.extend((context or "").splitlines())

    catalog: List[Dict[str, object]] = []
    seen: Set[Tuple[str, Tuple[str, ...]]] = set()
    for candidate in candidates:
        if not _is_command_line(candidate):
            continue
        raw_normalized = _normalize_command_line(candidate).lower()
        segments = _split_command_segments(candidate) or [_split_command_tokens(candidate)]
        for tokens in segments:
            shape = _parse_command_shape(tokens)
            signature = shape["signature"]
            if not raw_normalized or not tokens or not signature:
                continue
            key = (" ".join(tokens), tuple(tokens))
            if key in seen:
                continue
            seen.add(key)
            catalog.append(
                {
                    "normalized": raw_normalized,
                    "segment_normalized": " ".join(tokens),
                    **shape,
                }
            )
    return catalog


def _is_grounded_command(line: str, context_catalog: List[Dict[str, object]]) -> bool:
    raw_normalized = _normalize_command_line(line).lower()
    answer_tokens = _split_command_tokens(line)
    if not raw_normalized or not answer_tokens:
        return False
    answer_shape = _parse_command_shape(answer_tokens)
    answer_signature = answer_shape["signature"]
    if not answer_tokens or not answer_signature:
        return False

    for entry in context_catalog:
        if raw_normalized == entry["normalized"] or tuple(answer_tokens) == entry["tokens"]:
            return True
        if answer_signature != entry["signature"]:
            continue
        if not _is_subsequence(answer_shape["options"], entry["options"]):
            continue
        if not _is_subsequence(answer_shape["positionals"], entry["positionals"]):
            continue
        return True
    return False


def sanitize_commands_in_answer(answer: str, context: str) -> str:
    if not answer or not context:
        return answer

    def contains_wiki_url(line: str) -> bool:
        return "/wikis/" in line or "#sync" in line or "#build" in line

    code_pattern = r"```([a-zA-Z0-9+_-]*)\n(.*?)```"
    removed_any = False
    context_catalog = _extract_context_command_catalog(context)

    def replace_code(match: re.Match) -> str:
        nonlocal removed_any
        lang = match.group(1) or ""
        body = match.group(2) or ""
        lines = body.splitlines()
        kept = []
        for ln in lines:
            if contains_wiki_url(ln):
                removed_any = True
                continue
            if _is_command_line(ln):
                if not _is_grounded_command(ln, context_catalog):
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
        if _is_command_line(code):
            if contains_wiki_url(code) or not _is_grounded_command(code, context_catalog):
                removed_any = True
                return "команда отсутствует в базе знаний"
        return match.group(0)

    answer = re.sub(r"(?<!`)`([^`]+)`(?!`)", replace_inline, answer)
    if removed_any and len(answer.strip()) < 80:
        if "```" in answer or re.search(r"\b(?:git|repo|\./|bash|python|pip|cmake|make|ninja|docker|kubectl|sudo|apt|yum|npm|yarn)\b", answer):
            return answer
        return (
            "В найденных источниках нет точных команд для сборки/запуска по вашему запросу. "
            "Уточните компонент или платформу (например, C-API, XTS, e2e)."
        )
    return answer
