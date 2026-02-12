"""Parsers for Telegram chat history export files."""
import csv
import hashlib
import io
import json
import logging
import re
from typing import List, Dict

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_telegram_json(content: str, chat_id: str = None) -> List[Dict]:
    """Parse Telegram JSON export format (result.json).

    Expects the standard Telegram Desktop JSON export structure with
    a top-level ``messages`` array.  Service messages (pins, joins, etc.)
    are skipped.  Rich-text ``text`` fields stored as a list of entity
    dicts are flattened into plain strings.

    Args:
        content: Raw JSON string from the export file.
        chat_id: Optional override for the chat identifier.  When *None*,
            the ``id`` field from the JSON root is used.

    Returns:
        List of normalised message dicts.

    Raises:
        ValueError: If *content* is not valid JSON.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    messages_raw = data.get('messages', [])
    source_chat_id = chat_id or str(data.get('id', ''))
    results: List[Dict] = []

    for msg in messages_raw:
        # Skip service messages (pin_message, join, leave, etc.)
        if msg.get('type') != 'message':
            continue

        # Extract text -- can be a plain string or a list of entity objects
        text = msg.get('text', '')
        if isinstance(text, list):
            parts: List[str] = []
            for part in text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(part.get('text', ''))
            text = ''.join(parts)

        if not text or not text.strip():
            continue

        # Normalise from_id (Telegram stores "user123456789")
        from_id = str(msg.get('from_id', ''))
        if from_id.startswith('user'):
            from_id = from_id[4:]

        results.append({
            'chat_id': source_chat_id,
            'message_id': msg.get('id', 0),
            'author_telegram_id': from_id,
            'author_username': None,
            'author_display_name': msg.get('from', ''),
            'text': text.strip(),
            'timestamp': msg.get('date', ''),
            'thread_id': msg.get('reply_to_message_id'),
            'is_system_message': False,
            'is_bot_message': False,
        })

    return results


def parse_telegram_html(content: str, chat_id: str = None) -> List[Dict]:
    """Parse Telegram HTML export format.

    Telegram Desktop can export chat history as a set of HTML files.
    Messages live inside ``div.message`` elements with child elements
    ``.from_name``, ``.date``, and ``.text``.

    Because the HTML export does not include numeric message IDs, a
    synthetic ID is derived by hashing the author, timestamp, and the
    first 50 characters of the message text.

    Args:
        content: Raw HTML string.
        chat_id: Optional chat identifier to attach to every message.

    Returns:
        List of normalised message dicts.
    """
    soup = BeautifulSoup(content, 'html.parser')
    results: List[Dict] = []

    for msg_div in soup.select('div.message'):
        # Author
        from_el = msg_div.select_one('.from_name')
        author = from_el.get_text(strip=True) if from_el else ''

        # Timestamp -- stored in the ``title`` attribute or as inner text
        date_el = msg_div.select_one('.date')
        timestamp = ''
        if date_el:
            timestamp = date_el.get('title', '') or date_el.get_text(strip=True)

        # Message body
        text_el = msg_div.select_one('.text')
        text = text_el.get_text(strip=True) if text_el else ''

        if not text:
            continue

        # Synthetic deterministic message ID from content hash
        hash_input = f"{author}:{timestamp}:{text[:50]}"
        synthetic_id = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)

        results.append({
            'chat_id': chat_id or '',
            'message_id': synthetic_id,
            'author_telegram_id': None,
            'author_username': None,
            'author_display_name': author,
            'text': text,
            'timestamp': timestamp,
            'thread_id': None,
            'is_system_message': False,
            'is_bot_message': False,
        })

    return results


def parse_csv(content: str, chat_id: str = None) -> List[Dict]:
    """Parse CSV chat history with flexible column detection.

    Recognises several common column-name variants for the key fields:

    * **text**: ``text``, ``message``, ``content``, ``body``
    * **author**: ``author``, ``from``, ``user``, ``sender``, ``username``
    * **timestamp**: ``timestamp``, ``date``, ``time``, ``datetime``
    * **message id**: ``message_id``, ``id``

    Rows whose text field is empty are silently skipped.

    Args:
        content: Raw CSV string (with header row).
        chat_id: Optional chat identifier override.

    Returns:
        List of normalised message dicts.
    """
    reader = csv.DictReader(io.StringIO(content))
    results: List[Dict] = []
    counter = 0

    for row in reader:
        counter += 1

        # Flexible column detection
        text = (row.get('text') or row.get('message') or
                row.get('content') or row.get('body') or '')
        author = (row.get('author') or row.get('from') or
                  row.get('user') or row.get('sender') or
                  row.get('username') or '')
        timestamp = (row.get('timestamp') or row.get('date') or
                     row.get('time') or row.get('datetime') or '')
        msg_id = row.get('message_id') or row.get('id') or counter

        if not text.strip():
            continue

        results.append({
            'chat_id': chat_id or '',
            'message_id': int(msg_id) if str(msg_id).isdigit() else counter,
            'author_telegram_id': None,
            'author_username': None,
            'author_display_name': author.strip(),
            'text': text.strip(),
            'timestamp': timestamp.strip(),
            'thread_id': None,
            'is_system_message': False,
            'is_bot_message': False,
        })

    return results


def parse_txt(content: str, chat_id: str = None) -> List[Dict]:
    """Parse plain-text chat logs.

    Supports several common timestamp/author line formats:

    * ``[2024-01-15 10:30] John: Hello``
    * ``2024-01-15 10:30:00 - John: Hello``
    * ``15.01.2024 10:30 - John: Hello``

    Lines that do not match any known pattern are appended to the
    preceding message as continuation text (multi-line messages).

    Args:
        content: Raw text string.
        chat_id: Optional chat identifier override.

    Returns:
        List of normalised message dicts.
    """
    results: List[Dict] = []
    counter = 0

    patterns = [
        # [2024-01-15 10:30] John: text
        re.compile(r'^\[(.+?)\]\s+(.+?):\s+(.+)$'),
        # 2024-01-15 10:30:00 - John: text  (ISO-ish date first)
        re.compile(
            r'^(\d{4}[-/.]\d{2}[-/.]\d{2}[\sT]\d{2}:\d{2}(?::\d{2})?)'
            r'\s*[-\u2013]\s*(.+?):\s+(.+)$'
        ),
        # 15.01.2024 10:30 - John: text  (day-first date)
        re.compile(
            r'^(\d{2}[-/.]\d{2}[-/.]\d{4}\s+\d{2}:\d{2}(?::\d{2})?)'
            r'\s*[-\u2013]\s*(.+?):\s+(.+)$'
        ),
    ]

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        matched = False
        for pattern in patterns:
            m = pattern.match(line)
            if m:
                counter += 1
                timestamp, author, text = m.group(1), m.group(2), m.group(3)

                results.append({
                    'chat_id': chat_id or '',
                    'message_id': counter,
                    'author_telegram_id': None,
                    'author_username': None,
                    'author_display_name': author.strip(),
                    'text': text.strip(),
                    'timestamp': timestamp.strip(),
                    'thread_id': None,
                    'is_system_message': False,
                    'is_bot_message': False,
                })
                matched = True
                break

        # Unmatched non-empty line: treat as continuation of the last message
        if not matched and results:
            results[-1]['text'] += '\n' + line

    return results


def auto_detect_and_parse(content: str, chat_id: str = None,
                          format_hint: str = None) -> List[Dict]:
    """Auto-detect format and parse.

    Args:
        content: File content as string.
        chat_id: Override chat_id for imported messages.
        format_hint: One of telegram_json, telegram_html, csv, txt.

    Returns:
        List of message dicts with keys: chat_id, message_id,
        author_telegram_id, author_username, author_display_name,
        text, timestamp, is_system_message, is_bot_message.
    """
    if format_hint == 'telegram_json' or (not format_hint and content.lstrip().startswith('{')):
        return parse_telegram_json(content, chat_id)
    elif format_hint == 'telegram_html' or (not format_hint and '<!DOCTYPE' in content[:200].upper()):
        return parse_telegram_html(content, chat_id)
    elif format_hint == 'csv':
        return parse_csv(content, chat_id)
    else:
        return parse_txt(content, chat_id)
