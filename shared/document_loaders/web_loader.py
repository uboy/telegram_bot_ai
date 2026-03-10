"""
Загрузчик для веб-страниц
"""
import os
import re
import requests
import time
from typing import List, Dict
from urllib.parse import urlparse
from .base import DocumentLoader
from .chunking import split_text_structurally_with_metadata, split_text_into_chunks


def _fallback_web_doc_title(source: str, page_title: str) -> str:
    if page_title:
        return page_title.strip()
    parsed = urlparse(source or "")
    path = (parsed.path or "").rstrip("/")
    if path:
        return os.path.basename(path) or path.split("/")[-1]
    return parsed.netloc or source or "web"


def _build_overlap_chunk_records(parts: List[str], base_text: str, overlap: int | None) -> List[Dict[str, int | str]]:
    try:
        effective_overlap = int(overlap) if overlap is not None else 0
    except (ValueError, TypeError):
        effective_overlap = 0
    if effective_overlap < 0:
        effective_overlap = 0

    records: List[Dict[str, int | str]] = []
    search_start = 0
    for idx, part in enumerate(parts):
        if not part:
            continue
        char_start = base_text.find(part, search_start)
        if char_start < 0:
            char_start = search_start
        char_end = char_start + len(part)
        content = part
        if effective_overlap > 0 and idx > 0:
            prev = parts[idx - 1]
            overlap_text = prev[-effective_overlap:] if len(prev) > effective_overlap else prev
            if overlap_text:
                content = overlap_text + "\n\n" + part
        records.append({
            "content": content,
            "char_start": char_start,
            "char_end": char_end,
            "chunk_kind": "text",
        })
        search_start = char_end
    return records


class WebLoader(DocumentLoader):
    """Загрузчик для веб-страниц"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить веб-страницу с повторными попытками при таймауте"""
        try:
            from bs4 import BeautifulSoup
            import html2text
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            max_retries = 3
            base_delay = 2.0
            response = None
            
            for attempt in range(max_retries):
                try:
                    timeout = 10 + (attempt * 5)
                    response = requests.get(source, timeout=timeout, headers=headers)
                    response.raise_for_status()
                    break
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                    else:
                        raise
                except Exception:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                    else:
                        raise
            
            if response is None:
                return [{'content': f"Не удалось загрузить веб-страницу после {max_retries} попыток", 'title': '', 'metadata': {'type': 'web', 'url': source}}]
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            for element in soup(["script", "style", "nav", "header", "footer", "aside"]):
                element.decompose()
            
            page_title = ""
            if soup.title:
                page_title = soup.title.string or ""
            elif soup.find('h1'):
                page_title = soup.find('h1').get_text().strip()
            
            main_content = soup.find('main') or soup.find('article') or soup.find('body') or soup
            
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            h.body_width = 0
            text = h.handle(str(main_content))
            
            if page_title:
                text = f"{page_title}\n\n{text}"
            doc_title = _fallback_web_doc_title(source, page_title)

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

            if chunking_mode == "full":
                content = text
                if max_chars and max_chars > 0 and len(content) > max_chars:
                    content = content[:max_chars]
                return [{
                    "content": content,
                    "title": doc_title or source,
                    "metadata": {
                        "type": "web",
                        "url": source,
                        "doc_title": doc_title,
                        "page_title": page_title,
                        "section_title": doc_title,
                        "section_path": doc_title or "ROOT",
                        "chunk_kind": "full_page",
                        "chunk_no": 1,
                        "parser_profile": "loader:web:v1",
                        "truncated": bool(max_chars and len(text) > max_chars),
                    },
                }]

            sections: List[Dict[str, str]] = []
            current_section_lines: List[str] = []
            header_stack: List[tuple[int, str]] = []
            
            lines = text.split('\n')
            for line in lines:
                header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if header_match:
                    if current_section_lines:
                        section_content = "\n".join(current_section_lines).strip()
                        if section_content:
                            prev_title = header_stack[-1][1] if header_stack else doc_title
                            prev_path = " > ".join([doc_title] + [item[1] for item in header_stack if item[1]]) if header_stack else (doc_title or "ROOT")
                            sections.append({
                                "content": section_content,
                                "section_title": prev_title or doc_title,
                                "section_path": prev_path,
                            })
                    level = len(header_match.group(1))
                    header_text = header_match.group(2).strip()
                    while header_stack and header_stack[-1][0] >= level:
                        header_stack.pop()
                    header_stack.append((level, header_text))
                    current_section_lines = [line]
                else:
                    current_section_lines.append(line)
            
            if current_section_lines:
                section_content = "\n".join(current_section_lines).strip()
                if section_content:
                    section_title = header_stack[-1][1] if header_stack else doc_title
                    section_path = " > ".join([doc_title] + [item[1] for item in header_stack if item[1]]) if header_stack else (doc_title or "ROOT")
                    sections.append({
                        "content": section_content,
                        "section_title": section_title or doc_title,
                        "section_path": section_path,
                    })
            
            if not sections:
                sections = [{"content": text, "section_title": doc_title, "section_path": doc_title or "ROOT"}]

            chunks: List[Dict[str, str]] = []
            global_chunk_no = 1
            document_char_offset = 0
            for sec in sections:
                sec_text = sec.get("content") or ""
                sec_title = sec.get("section_title") or doc_title or ""
                sec_path = sec.get("section_path") or doc_title or "ROOT"

                if chunking_mode == "fixed":
                    sec_base_chunks = split_text_into_chunks(sec_text, max_chars=max_chars, overlap=0)
                    sec_records = _build_overlap_chunk_records(sec_base_chunks, sec_text, overlap)
                else:
                    sec_records = split_text_structurally_with_metadata(sec_text, max_chars=max_chars, overlap=overlap)

                for section_chunk_no, record in enumerate(sec_records, start=1):
                    part = str(record["content"])
                    title = sec_title or doc_title or f"Фрагмент {section_chunk_no}"
                    if len(sec_records) > 1 and title:
                        title = f"{title} (фрагмент {section_chunk_no})"
                    chunks.append({
                        "content": part,
                        "title": title,
                        "metadata": {
                            "type": "web",
                            "url": source,
                            "doc_title": doc_title,
                            "page_title": page_title,
                            "section_title": sec_title,
                            "section_path": sec_path,
                            "chunk_kind": record.get("chunk_kind") or "text",
                            "chunk_no": global_chunk_no,
                            "char_start": document_char_offset + int(record.get("char_start") or 0),
                            "char_end": document_char_offset + int(record.get("char_end") or 0),
                            "parser_profile": "loader:web:v1",
                        },
                    })
                    global_chunk_no += 1
                document_char_offset += len(sec_text) + 2
            
            return chunks if chunks else [
                {"content": text[:5000], "title": "", "metadata": {"type": "web", "url": source, "doc_title": doc_title, "parser_profile": "loader:web:v1"}}
            ]
        except ImportError:
            return [{'content': 'Библиотеки beautifulsoup4 и html2text не установлены', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки веб-страницы: {str(e)}", 'title': '', 'metadata': {}}]

