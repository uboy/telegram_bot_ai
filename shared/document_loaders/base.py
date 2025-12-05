"""
Базовый класс для загрузчиков документов
"""
from typing import List, Dict
from abc import ABC, abstractmethod


class DocumentLoader(ABC):
    """Базовый класс для загрузчиков документов"""
    
    @abstractmethod
    def load(self, source: str) -> List[Dict[str, str]]:
        """
        Загрузить документ и вернуть список фрагментов в едином формате.
        
        Формат каждого фрагмента:
        {
            "content": str,      # Текст фрагмента
            "title": str,        # Заголовок/название фрагмента
            "metadata": dict     # Дополнительные метаданные (type, page, sheet, section_title и т.д.)
        }
        
        Args:
            source: Путь к файлу или URL
            
        Returns:
            Список словарей с фрагментами документа
        """
        raise NotImplementedError

