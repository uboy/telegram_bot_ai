"""Digest text generation from extracted themes."""
import logging
import json
import asyncio
from typing import List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class DigestGeneratorService:
    """Generates formatted digest text from themes using LLM."""

    async def generate_digest_text(self, themes: list,
                                   period_start: datetime,
                                   period_end: datetime,
                                   stats: dict) -> str:
        """Generate complete digest text (HTML for Telegram).

        Args:
            themes: List of theme dicts with keys: emoji, title, summary,
                   key_decisions, unresolved_questions, main_participants,
                   message_count, key_message_links
            period_start: Start of analysis period
            period_end: End of analysis period
            stats: Dict with keys: total_messages, unique_participants, active_threads

        Returns:
            HTML string formatted for Telegram
        """
        # Format themes for LLM prompt
        themes_formatted = self._format_themes_for_prompt(themes)

        # Build LLM prompt for overall summary
        prompt = self._build_summary_prompt(
            themes_formatted=themes_formatted,
            period_start=period_start,
            period_end=period_end,
            stats=stats
        )

        # Call LLM (synchronous call wrapped in asyncio.to_thread)
        try:
            from shared.ai_providers import ai_manager
            response_text = await asyncio.to_thread(ai_manager.query, prompt)

            # Parse JSON response
            summary = self._parse_llm_response(response_text)

        except Exception as e:
            logger.error("LLM summary generation failed: %s", e, exc_info=True)
            # Fallback to basic summary
            summary = {
                "period_summary": "Период анализа чата",
                "key_decisions": [],
                "unresolved_questions": [],
                "theme_count": len(themes)
            }

        # Format as HTML
        html = self.format_digest_html(summary, themes, stats, period_start, period_end)
        return html

    def format_digest_html(self, summary: dict, themes: list,
                          stats: dict = None,
                          period_start: datetime = None,
                          period_end: datetime = None) -> str:
        """Format digest as Telegram-compatible HTML.

        Args:
            summary: Dict with keys: period_summary, key_decisions,
                    unresolved_questions, theme_count
            themes: List of theme dicts (same as generate_digest_text)
            stats: Optional stats dict (total_messages, unique_participants, active_threads)
            period_start: Optional start date
            period_end: Optional end date

        Returns:
            HTML string with Telegram-compatible formatting (b, i, a tags only)
        """
        lines = []

        # Header with period
        period_str = self._format_period(period_start, period_end)
        lines.append(f"<b>Дайджест за {period_str}</b>\n")

        # Overview summary
        total_messages = stats.get('total_messages', 0) if stats else 0
        unique_participants = stats.get('unique_participants', 0) if stats else 0
        theme_count = summary.get('theme_count', len(themes))

        period_summary = summary.get('period_summary', '')
        lines.append(f"{period_summary}")
        lines.append(f"Обсуждено {theme_count} тем, {total_messages} сообщений")
        if unique_participants > 0:
            lines.append(f" от {unique_participants} участников")
        lines.append(".\n")

        # Key decisions section
        key_decisions = summary.get('key_decisions', [])
        if key_decisions:
            lines.append("\n<b>Ключевые решения:</b>")
            for decision in key_decisions:
                lines.append(f"• {decision}")
            lines.append("")

        # Unresolved questions section
        unresolved = summary.get('unresolved_questions', [])
        if unresolved:
            lines.append("\n<b>Нерешенные вопросы:</b>")
            for question in unresolved:
                lines.append(f"• {question}")
            lines.append("")

        # Separator
        lines.append("\n───────\n")

        # Individual themes
        for idx, theme in enumerate(themes, start=1):
            emoji = theme.get('emoji', '📌')
            title = theme.get('title', 'Тема')
            summary_text = theme.get('summary', '')
            participants = theme.get('main_participants', [])
            message_count = theme.get('message_count', 0)
            links = theme.get('key_message_links', [])

            # Theme header
            lines.append(f"{idx}. {emoji} <b>{self._escape_html(title)}</b>")

            # Summary
            lines.append(self._escape_html(summary_text))

            # Metadata
            if participants:
                participants_str = ", ".join(participants[:5])  # Limit to 5
                lines.append(f"Участники: {self._escape_html(participants_str)}")

            if message_count > 0:
                lines.append(f"Сообщений: {message_count}")

            # Links
            if links:
                link_parts = []
                for link_idx, link in enumerate(links[:5], start=1):  # Max 5 links
                    link_parts.append(f'<a href="{link}">#{link_idx}</a>')
                lines.append(f"Ссылки: {' '.join(link_parts)}")

            lines.append("")  # Empty line between themes

        return "\n".join(lines)

    def _format_themes_for_prompt(self, themes: list) -> str:
        """Format themes as text for LLM prompt."""
        parts = []
        for idx, theme in enumerate(themes, start=1):
            emoji = theme.get('emoji', '')
            title = theme.get('title', '')
            summary = theme.get('summary', '')
            message_count = theme.get('message_count', 0)

            parts.append(f"{idx}. {emoji} {title}")
            parts.append(f"   {summary}")
            parts.append(f"   ({message_count} сообщений)")

            # Include decisions and questions from theme if available
            decisions = theme.get('key_decisions', [])
            if decisions:
                parts.append(f"   Решения: {', '.join(decisions)}")

            questions = theme.get('unresolved_questions', [])
            if questions:
                parts.append(f"   Вопросы: {', '.join(questions)}")

            parts.append("")

        return "\n".join(parts)

    def _build_summary_prompt(self, themes_formatted: str,
                             period_start: datetime,
                             period_end: datetime,
                             stats: dict) -> str:
        """Build LLM prompt for generating overall summary."""
        period_str = self._format_period(period_start, period_end)

        total_messages = stats.get('total_messages', 0)
        unique_participants = stats.get('unique_participants', 0)
        active_threads = stats.get('active_threads', 0)

        prompt = f"""You are creating a summary block for a chat digest covering the period {period_str}.

The following themes were identified:
---
{themes_formatted}
---

Statistics:
- Total messages: {total_messages}
- Unique participants: {unique_participants}
- Active topics/threads: {active_threads}

Create a brief overall summary with:
1. Total themes discussed
2. Key decisions made (across all themes)
3. Unresolved questions (across all themes)
4. One-sentence period summary

IMPORTANT:
- Respond in the SAME LANGUAGE as the theme titles/summaries
- Be concise and factual
- Use neutral tone
- Return ONLY valid JSON

Return ONLY valid JSON:
{{
  "period_summary": "<one sentence>",
  "key_decisions": ["<decision>", ...],
  "unresolved_questions": ["<question>", ...],
  "theme_count": <int>
}}"""

        return prompt

    def _parse_llm_response(self, response_text: str) -> dict:
        """Parse JSON response from LLM.

        Extracts JSON from response even if wrapped in markdown or other text.
        """
        # Try direct JSON parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from code blocks
        import re
        json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        match = re.search(json_pattern, response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in text
        json_pattern = r'\{[^{}]*"period_summary"[^{}]*\}'
        match = re.search(json_pattern, response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback
        logger.warning("Could not parse LLM response as JSON: %s", response_text[:200])
        return {
            "period_summary": "Анализ чата завершен",
            "key_decisions": [],
            "unresolved_questions": [],
            "theme_count": 0
        }

    def _format_period(self, start: datetime = None, end: datetime = None) -> str:
        """Format period as human-readable string."""
        if not start or not end:
            return "период"

        # Format as DD.MM - DD.MM.YYYY or DD.MM.YYYY if same year
        start_str = start.strftime("%d.%m")
        end_str = end.strftime("%d.%m.%Y")

        # Include start year if different from end year
        if start.year != end.year:
            start_str = start.strftime("%d.%m.%Y")

        return f"{start_str} - {end_str}"

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters for Telegram."""
        if not text:
            return ""

        # Telegram HTML only supports <b>, <i>, <a>, <code>, <pre>
        # We need to escape & < > but not the tags we use
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")

        return text
