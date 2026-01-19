"""
Модуль для работы с различными провайдерами ИИ
"""
import os
import requests
import json
import logging
from typing import Optional, Dict, List
from shared.config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)


class AIProvider:
    """Базовый класс для провайдеров ИИ"""
    
    def __init__(self, name: str):
        self.name = name
    
    def query(self, prompt: str, **kwargs) -> str:
        """Отправка запроса к ИИ"""
        raise NotImplementedError
    
    def query_multimodal(self, prompt: str, image_path: Optional[str] = None, **kwargs) -> str:
        """Мультимодальный запрос с изображением"""
        raise NotImplementedError


class OllamaProvider(AIProvider):
    """Провайдер для Ollama"""
    
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        super().__init__("Ollama")
        self.base_url = base_url
        self.model = model
    
    def query(self, prompt: str, **kwargs) -> str:
        """Запрос к Ollama"""
        try:
            if os.getenv("OLLAMA_DEBUG_PROMPT", "false").lower() == "true":
                preview = (prompt or "")[:800]
                logger.debug("Ollama prompt preview (len=%d): %s", len(prompt or ""), preview)
            # Получить настройку фильтрации thinking tokens из конфига
            try:
                from shared.config import OLLAMA_FILTER_THINKING
                filter_thinking = OLLAMA_FILTER_THINKING
            except ImportError:
                filter_thinking = os.getenv("OLLAMA_FILTER_THINKING", "true").lower() == "true"
            
            # Подготовить параметры запроса
            request_params = {
                "model": self.model,
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
        import re
        
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
    
    def query_multimodal(self, prompt: str, image_path: Optional[str] = None, **kwargs) -> str:
        """Мультимодальный запрос с изображением"""
        if not image_path:
            return self.query(prompt, **kwargs)
        
        try:
            import base64
            import os
            
            # Получить настройку фильтрации thinking tokens из конфига
            try:
                from shared.config import OLLAMA_FILTER_THINKING
                filter_thinking = OLLAMA_FILTER_THINKING
            except ImportError:
                filter_thinking = os.getenv("OLLAMA_FILTER_THINKING", "true").lower() == "true"
            
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            request_params = {
                "model": self.model,
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
    
    def __init__(self, api_key: str, model: str = "gpt-4"):
        super().__init__("OpenAI")
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1"
    
    def query(self, prompt: str, **kwargs) -> str:
        """Запрос к OpenAI"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    **kwargs
                },
                timeout=120
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            return f"Ошибка при обращении к OpenAI: {resp.status_code}"
        except Exception as e:
            return f"Ошибка подключения к OpenAI: {str(e)}"
    
    def query_multimodal(self, prompt: str, image_path: Optional[str] = None, **kwargs) -> str:
        """Мультимодальный запрос с изображением"""
        if not image_path:
            return self.query(prompt, **kwargs)
        
        try:
            import base64
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": "gpt-4-vision-preview",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                        ]
                    }],
                    **kwargs
                },
                timeout=120
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            return f"Ошибка при обращении к OpenAI: {resp.status_code}"
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
            # Если указана модель и провайдер - Ollama, использовать её
            if model and isinstance(provider, OllamaProvider):
                old_model = provider.model
                provider.model = model
                result = provider.query(prompt, **kwargs)
                provider.model = old_model  # Восстановить оригинальную модель
                return result
            return provider.query(prompt, **kwargs)
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
            # Аналогично текстовому запросу: позволяем временно переопределить модель Ollama
            if model and isinstance(provider, OllamaProvider):
                old_model = provider.model
                provider.model = model
                result = provider.query_multimodal(prompt, image_path, **kwargs)
                provider.model = old_model
                return result
            return provider.query_multimodal(prompt, image_path, **kwargs)
        return "Провайдер ИИ не найден"


# Глобальный менеджер провайдеров
ai_manager = AIProviderManager()

# Инициализация провайдеров по умолчанию
def init_default_providers():
    """Инициализация провайдеров по умолчанию"""
    ollama = OllamaProvider()
    ai_manager.register_provider("ollama", ollama)
    ai_manager.set_provider("ollama")

init_default_providers()

