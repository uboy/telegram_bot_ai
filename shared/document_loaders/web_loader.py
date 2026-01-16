"""
Загрузчик для веб-страниц
"""
import re
import requests
import time
from typing import List, Dict
from .base import DocumentLoader
from .chunking import split_text_structurally, split_text_into_chunks


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
                    "title": page_title or source,
                    "metadata": {
                        "type": "web",
                        "url": source,
                        "page_title": page_title,
                        "section_title": page_title,
                        "chunk_kind": "full_page",
                        "truncated": bool(max_chars and len(text) > max_chars),
                    },
                }]

            sections: List[Dict[str, str]] = []
            current_section = ""
            current_title = page_title or ""
            
            lines = text.split('\n')
            for line in lines:
                header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if header_match:
                    if current_section:
                        sections.append({"content": current_section.strip(), "title": current_title})
                    current_title = header_match.group(2).strip()
                    current_section = line + "\n"
                else:
                    current_section += line + "\n"
            
            if current_section:
                sections.append({"content": current_section.strip(), "title": current_title})
            
            if not sections:
                sections = [{"content": text, "title": page_title or ""}]

            chunks: List[Dict[str, str]] = []
            for sec in sections:
                sec_text = sec.get("content") or ""
                sec_title = sec.get("title") or ""

                if chunking_mode == "fixed":
                    sec_chunks = split_text_into_chunks(sec_text, max_chars=max_chars, overlap=overlap)
                else:
                    sec_chunks = split_text_structurally(sec_text, max_chars=max_chars, overlap=overlap)

                for idx, part in enumerate(sec_chunks, start=1):
                    title = sec_title or f"Фрагмент {idx}"
                    if len(sec_chunks) > 1:
                        title = f"{title} (фрагмент {idx})" if sec_title else f"Фрагмент {idx}"
                    chunks.append({
                        "content": part,
                        "title": title,
                        "metadata": {
                            "type": "web",
                            "url": source,
                            "page_title": page_title,
                            "section_title": sec_title,
                            "chunk_kind": "text",
                        },
                    })
            
            return chunks if chunks else [
                {"content": text[:5000], "title": "", "metadata": {"type": "web", "url": source}}
            ]
        except ImportError:
            return [{'content': 'Библиотеки beautifulsoup4 и html2text не установлены', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки веб-страницы: {str(e)}", 'title': '', 'metadata': {}}]

