"""
LLM client abstraction. Позже сюда будет перенесена логика ai_providers.
"""

from typing import Optional


class LLMClient:
    """Placeholder for future unified LLM client."""

    def query(self, prompt: str, *, model: Optional[str] = None) -> str:
        raise NotImplementedError("LLMClient.query не реализован")


