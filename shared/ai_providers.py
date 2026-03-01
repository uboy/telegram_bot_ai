"""
Модуль для работы с различными провайдерами ИИ
"""
import os
import base64
import mimetypes
import requests
import logging
import re
import time
from typing import Optional, Dict, List, Any
from shared.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from shared.ai_metrics import build_request_id, estimate_tokens, record_ai_metric

logger = logging.getLogger(__name__)


class AIProvider:
    """Базовый класс для провайдеров ИИ"""
    
    def __init__(self, name: str):
        self.name = name
    
    def query(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        """Отправка запроса к ИИ"""
        raise NotImplementedError
    
    def query_multimodal(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Мультимодальный запрос с изображением"""
        raise NotImplementedError

    def list_models(self) -> List[str]:
        """Получить список доступных моделей (если поддерживается провайдером)"""
        return []


class OllamaProvider(AIProvider):
    """Провайдер для Ollama"""
    
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        super().__init__("Ollama")
        self.base_url = base_url
        self.model = model
    
    def query(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        """Запрос к Ollama"""
        try:
            if os.getenv("OLLAMA_DEBUG_PROMPT", "false").lower() == "true":
                preview = (prompt or "")[:800]
                logger.debug("Ollama prompt preview (len=%d): %s", len(prompt or ""), preview)
            effective_model = model or self.model
            # Получить настройку фильтрации thinking tokens из конфига
            try:
                from shared.config import OLLAMA_FILTER_THINKING
                filter_thinking = OLLAMA_FILTER_THINKING
            except ImportError:
                filter_thinking = os.getenv("OLLAMA_FILTER_THINKING", "true").lower() == "true"
            
            # Подготовить параметры запроса
            request_params = {
                "model": effective_model,
                "prompt": prompt,
                "stream": False,
            }
            
            # Если включена фильтрация thinking, используем format для структурированного вывода
            # или добавляем параметры для фильтрации
            if filter_thinking:
                # Для reasoning моделей можно использовать format: json для структурированного вывода
                # или обработать ответ для удаления thinking tokens
                # Пока используем обработку ответа, так как format может изменить структуру ответа
                pass
            
            # Добавить дополнительные параметры из kwargs
            request_params.update(kwargs)
            
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=request_params,
                timeout=120
            )
            if resp.status_code == 200:
                response_text = resp.json().get("response", "").strip()
                
                # Фильтровать thinking tokens если включено
                if filter_thinking:
                    response_text = self._filter_thinking_tokens(response_text)
                
                return response_text
            return f"Ошибка при обращении к Ollama: {resp.status_code}"
        except Exception as e:
            return f"Ошибка подключения к Ollama: {str(e)}"
    
    def _filter_thinking_tokens(self, text: str) -> str:
        """Удалить thinking tokens из ответа reasoning моделей"""
        # Удалить блоки между <think>...</think> (включая сами маркеры)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Удалить блоки между <reasoning>...</reasoning>
        text = re.sub(r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Удалить блоки между ```thinking...``` (code blocks с thinking)
        text = re.sub(r'```thinking.*?```', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'```reasoning.*?```', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Удалить строки, начинающиеся с "Thinking:" или "Reasoning:"
        lines = text.split('\n')
        filtered_lines = []
        skip_thinking_block = False
        
        for line in lines:
            # Пропускаем строки, которые явно являются thinking
            if re.match(r'^\s*(Thinking|Reasoning|Размышление|Рассуждение)[:：]\s*', line, re.IGNORECASE):
                skip_thinking_block = True
                continue
            
            # Если строка пустая после thinking блока, пропускаем её
            if skip_thinking_block and not line.strip():
                continue
            
            # Если строка не пустая и не thinking, сбрасываем флаг
            if line.strip() and skip_thinking_block:
                skip_thinking_block = False
            
            if not skip_thinking_block:
                filtered_lines.append(line)
        
        text = '\n'.join(filtered_lines)
        
        # Убрать множественные пустые строки
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()
    
    def query_multimodal(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Мультимодальный запрос с изображением"""
        if not image_path:
            return self.query(prompt, model=model, **kwargs)
        
        try:
            effective_model = model or self.model
            
            # Получить настройку фильтрации thinking tokens из конфига
            try:
                from shared.config import OLLAMA_FILTER_THINKING
                filter_thinking = OLLAMA_FILTER_THINKING
            except ImportError:
                filter_thinking = os.getenv("OLLAMA_FILTER_THINKING", "true").lower() == "true"
            
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            request_params = {
                "model": effective_model,
                "prompt": prompt,
                "images": [image_data],
                "stream": False,
            }
            request_params.update(kwargs)
            
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=request_params,
                timeout=120
            )
            if resp.status_code == 200:
                response_text = resp.json().get("response", "").strip()
                
                # Фильтровать thinking tokens если включено
                if filter_thinking:
                    response_text = self._filter_thinking_tokens(response_text)
                
                return response_text
            return f"Ошибка при обращении к Ollama: {resp.status_code}"
        except Exception as e:
            return f"Ошибка обработки изображения: {str(e)}"
    
    def list_models(self) -> List[str]:
        """Получить список доступных моделей"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return [m.get("name", "") for m in models]
            return []
        except:
            return []


class OpenAIProvider(AIProvider):
    """Провайдер для OpenAI API"""
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        provider_name: str = "OpenAI",
    ):
        super().__init__(provider_name)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _extract_chat_content(self, payload: Dict[str, Any]) -> str:
        choices = payload.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(parts).strip()
        return str(content).strip()
    
    def query(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        """Запрос к OpenAI-совместимому API"""
        try:
            request_payload = {
                "model": model or self.model,
                "messages": [{"role": "user", "content": prompt}],
                **kwargs
            }
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=request_payload,
                timeout=120
            )
            if resp.status_code == 200:
                content = self._extract_chat_content(resp.json())
                return content or "Пустой ответ от модели"
            return f"Ошибка при обращении к {self.name}: {resp.status_code}"
        except Exception as e:
            return f"Ошибка подключения к {self.name}: {str(e)}"
    
    def query_multimodal(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Мультимодальный запрос с изображением"""
        if not image_path:
            return self.query(prompt, model=model, **kwargs)
        
        try:
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            media_type, _ = mimetypes.guess_type(image_path)
            media_type = media_type or "image/jpeg"
            request_payload = {
                "model": model or self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}}
                    ]
                }],
                **kwargs
            }
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=request_payload,
                timeout=120
            )
            if resp.status_code == 200:
                content = self._extract_chat_content(resp.json())
                return content or "Пустой ответ от модели"
            return f"Ошибка при обращении к {self.name}: {resp.status_code}"
        except Exception as e:
            return f"Ошибка обработки изображения: {str(e)}"

    def list_models(self) -> List[str]:
        """Получить список моделей (если API поддерживает endpoint /models)"""
        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("data", [])
            return [item.get("id", "") for item in items if item.get("id")]
        except Exception:
            return []


class OpenWebUIProvider(OpenAIProvider):
    """Провайдер для Open WebUI OpenAI-compatible API"""

    def __init__(
        self,
        api_key: str = "",
        model: str = OLLAMA_MODEL,
        base_url: str = "http://localhost:3000/api",
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="Open WebUI",
        )


class DeepSeekProvider(OpenAIProvider):
    """Провайдер для DeepSeek API"""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="DeepSeek",
        )


class AnthropicProvider(AIProvider):
    """Провайдер для Anthropic Messages API"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-latest",
        base_url: str = "https://api.anthropic.com",
    ):
        super().__init__("Anthropic")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _extract_text(self, payload: Dict[str, Any]) -> str:
        content = payload.get("content", [])
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    def query(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        try:
            max_tokens = kwargs.pop("max_tokens", 1024)
            request_payload = {
                "model": model or self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                **kwargs,
            }
            resp = requests.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=request_payload,
                timeout=120,
            )
            if resp.status_code == 200:
                content = self._extract_text(resp.json())
                return content or "Пустой ответ от модели"
            return f"Ошибка при обращении к Anthropic: {resp.status_code}"
        except Exception as e:
            return f"Ошибка подключения к Anthropic: {str(e)}"

    def query_multimodal(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        if not image_path:
            return self.query(prompt, model=model, **kwargs)

        try:
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            media_type, _ = mimetypes.guess_type(image_path)
            media_type = media_type or "image/jpeg"
            max_tokens = kwargs.pop("max_tokens", 1024)
            request_payload = {
                "model": model or self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                        ],
                    }
                ],
                **kwargs,
            }
            resp = requests.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=request_payload,
                timeout=120,
            )
            if resp.status_code == 200:
                content = self._extract_text(resp.json())
                return content or "Пустой ответ от модели"
            return f"Ошибка при обращении к Anthropic: {resp.status_code}"
        except Exception as e:
            return f"Ошибка обработки изображения: {str(e)}"


class AIProviderManager:
    """Менеджер для управления провайдерами ИИ"""
    
    def __init__(self):
        self.providers: Dict[str, AIProvider] = {}
        self.current_provider: Optional[str] = None
        self.default_provider = "ollama"
    
    def register_provider(self, name: str, provider: AIProvider):
        """Регистрация провайдера"""
        self.providers[name] = provider
        if not self.current_provider:
            self.current_provider = name
    
    def set_provider(self, name: str) -> bool:
        """Установить текущий провайдер"""
        if name in self.providers:
            self.current_provider = name
            return True
        return False
    
    def get_provider(self, name: Optional[str] = None) -> Optional[AIProvider]:
        """Получить провайдер"""
        provider_name = name or self.current_provider or self.default_provider
        return self.providers.get(provider_name)
    
    def list_providers(self) -> List[str]:
        """Список доступных провайдеров"""
        return list(self.providers.keys())
    
    def query(self, prompt: str, provider_name: Optional[str] = None, model: Optional[str] = None, **kwargs) -> str:
        """Отправить запрос через текущий или указанный провайдер"""
        provider = self.get_provider(provider_name)
        if provider:
            telemetry_meta = kwargs.pop("telemetry_meta", None) or kwargs.pop("_telemetry_meta", None) or {}
            request_id = str(telemetry_meta.get("request_id") or build_request_id())
            started = time.monotonic()
            response_text = ""
            status = "ok"
            error_type = None
            error_message = None
            try:
                response_text = provider.query(prompt, model=model, **kwargs)
                if isinstance(response_text, str) and response_text.lower().startswith("ошибка"):
                    status = "error"
                    error_type = "provider_error"
                    error_message = response_text
                return response_text
            except Exception as exc:
                status = "error"
                error_type = exc.__class__.__name__
                error_message = str(exc)
                raise
            finally:
                try:
                    prompt_chars = int(telemetry_meta.get("prompt_chars") or len(prompt or ""))
                    prompt_tokens_est = int(telemetry_meta.get("prompt_tokens_est") or estimate_tokens(prompt))
                    context_chars = int(telemetry_meta.get("context_chars") or 0)
                    context_tokens_est = int(telemetry_meta.get("context_tokens_est") or 0)
                    history_turns_used = int(telemetry_meta.get("history_turns_used") or 0)
                    predicted_latency_ms = telemetry_meta.get("predicted_latency_ms")
                    latency_ms = int((time.monotonic() - started) * 1000)
                    record_ai_metric(
                        request_id=request_id,
                        feature=str(telemetry_meta.get("feature") or "unknown"),
                        user_telegram_id=str(telemetry_meta.get("user_telegram_id")) if telemetry_meta.get("user_telegram_id") else None,
                        conversation_id=int(telemetry_meta["conversation_id"]) if telemetry_meta.get("conversation_id") else None,
                        provider_name=provider_name or self.current_provider or self.default_provider,
                        model_name=model or getattr(provider, "model", None),
                        request_kind="text",
                        prompt_chars=prompt_chars,
                        prompt_tokens_est=prompt_tokens_est,
                        context_chars=context_chars,
                        context_tokens_est=context_tokens_est,
                        history_turns_used=history_turns_used,
                        predicted_latency_ms=int(predicted_latency_ms) if predicted_latency_ms is not None else None,
                        latency_ms=latency_ms,
                        response_chars=len(response_text or ""),
                        response_tokens_est=estimate_tokens(response_text),
                        status=status,
                        error_type=error_type,
                        error_message=error_message,
                    )
                except Exception as exc:
                    logger.debug("Skip telemetry logging for text query: %s", exc)
        return "Провайдер ИИ не найден"
    
    def query_multimodal(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        provider_name: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Мультимодальный запрос"""
        provider = self.get_provider(provider_name)
        if provider:
            telemetry_meta = kwargs.pop("telemetry_meta", None) or kwargs.pop("_telemetry_meta", None) or {}
            request_id = str(telemetry_meta.get("request_id") or build_request_id())
            started = time.monotonic()
            response_text = ""
            status = "ok"
            error_type = None
            error_message = None
            try:
                response_text = provider.query_multimodal(prompt, image_path, model=model, **kwargs)
                if isinstance(response_text, str) and response_text.lower().startswith("ошибка"):
                    status = "error"
                    error_type = "provider_error"
                    error_message = response_text
                return response_text
            except Exception as exc:
                status = "error"
                error_type = exc.__class__.__name__
                error_message = str(exc)
                raise
            finally:
                try:
                    prompt_chars = int(telemetry_meta.get("prompt_chars") or len(prompt or ""))
                    prompt_tokens_est = int(telemetry_meta.get("prompt_tokens_est") or estimate_tokens(prompt))
                    context_chars = int(telemetry_meta.get("context_chars") or 0)
                    context_tokens_est = int(telemetry_meta.get("context_tokens_est") or 0)
                    history_turns_used = int(telemetry_meta.get("history_turns_used") or 0)
                    predicted_latency_ms = telemetry_meta.get("predicted_latency_ms")
                    latency_ms = int((time.monotonic() - started) * 1000)
                    record_ai_metric(
                        request_id=request_id,
                        feature=str(telemetry_meta.get("feature") or "unknown"),
                        user_telegram_id=str(telemetry_meta.get("user_telegram_id")) if telemetry_meta.get("user_telegram_id") else None,
                        conversation_id=int(telemetry_meta["conversation_id"]) if telemetry_meta.get("conversation_id") else None,
                        provider_name=provider_name or self.current_provider or self.default_provider,
                        model_name=model or getattr(provider, "model", None),
                        request_kind="multimodal",
                        prompt_chars=prompt_chars,
                        prompt_tokens_est=prompt_tokens_est,
                        context_chars=context_chars,
                        context_tokens_est=context_tokens_est,
                        history_turns_used=history_turns_used,
                        predicted_latency_ms=int(predicted_latency_ms) if predicted_latency_ms is not None else None,
                        latency_ms=latency_ms,
                        response_chars=len(response_text or ""),
                        response_tokens_est=estimate_tokens(response_text),
                        status=status,
                        error_type=error_type,
                        error_message=error_message,
                    )
                except Exception as exc:
                    logger.debug("Skip telemetry logging for multimodal query: %s", exc)
        return "Провайдер ИИ не найден"


# Глобальный менеджер провайдеров
ai_manager = AIProviderManager()

# Инициализация провайдеров по умолчанию
def init_default_providers():
    """Инициализация провайдеров по умолчанию (из env)"""
    ai_manager.providers = {}
    ai_manager.current_provider = None
    ai_manager.default_provider = "ollama"

    ollama = OllamaProvider()
    ai_manager.register_provider("ollama", ollama)

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_api_key:
        ai_manager.register_provider(
            "openai",
            OpenAIProvider(
                api_key=openai_api_key,
                model=os.getenv("OPENAI_MODEL", "gpt-4"),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            ),
        )

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_api_key:
        ai_manager.register_provider(
            "anthropic",
            AnthropicProvider(
                api_key=anthropic_api_key,
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            ),
        )

    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if deepseek_api_key:
        ai_manager.register_provider(
            "deepseek",
            DeepSeekProvider(
                api_key=deepseek_api_key,
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            ),
        )

    open_webui_base_url = os.getenv("OPEN_WEBUI_BASE_URL", "").strip()
    open_webui_api_key = os.getenv("OPEN_WEBUI_API_KEY", "").strip()
    open_webui_model = os.getenv("OPEN_WEBUI_MODEL", "").strip()
    if open_webui_base_url or open_webui_api_key or open_webui_model:
        ai_manager.register_provider(
            "open_webui",
            OpenWebUIProvider(
                api_key=open_webui_api_key,
                model=open_webui_model or OLLAMA_MODEL,
                base_url=open_webui_base_url or "http://localhost:3000/api",
            ),
        )

    requested_default = (os.getenv("AI_DEFAULT_PROVIDER", "ollama").strip().lower() or "ollama")
    if requested_default in ai_manager.providers:
        ai_manager.default_provider = requested_default
        ai_manager.set_provider(requested_default)
    else:
        ai_manager.set_provider("ollama")

init_default_providers()

