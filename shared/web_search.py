"""
Модуль для поиска в интернете
"""
import requests
from typing import List, Dict
from shared.config import OLLAMA_BASE_URL


def search_web(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Поиск в интернете через DuckDuckGo
    """
    try:
        try:
            from ddgs import DDGS  # Новый пакет
        except ImportError:
            from duckduckgo_search import DDGS  # Старый пакет (для обратной совместимости)
        
        with DDGS() as ddgs:
            results = []
            for result in ddgs.text(query, max_results=max_results):
                results.append({
                    'title': result.get('title', ''),
                    'url': result.get('href', ''),
                    'snippet': result.get('body', '')
                })
            return results
    except ImportError:
        # Альтернативный метод через requests (если нет библиотеки)
        try:
            # Использовать простой поиск через API (пример)
            # В реальности лучше использовать DuckDuckGo или другой поисковик
            return [{
                'title': 'Поиск в интернете',
                'url': f'https://www.google.com/search?q={query}',
                'snippet': f'Для поиска "{query}" используйте поисковую систему'
            }]
        except Exception as e:
            return [{'title': 'Ошибка', 'url': '', 'snippet': f'Ошибка поиска: {str(e)}'}]
    except Exception as e:
        return [{'title': 'Ошибка', 'url': '', 'snippet': f'Ошибка поиска: {str(e)}'}]


def format_search_results(results: List[Dict[str, str]]) -> str:
    """Форматировать результаты поиска для отправки"""
    if not results:
        return "Результаты поиска не найдены."
    
    formatted = "🔍 Результаты поиска:\n\n"
    for i, result in enumerate(results, 1):
        formatted += f"{i}. {result.get('title', 'Без названия')}\n"
        formatted += f"   {result.get('url', '')}\n"
        formatted += f"   {result.get('snippet', '')[:200]}...\n\n"
    
    return formatted

