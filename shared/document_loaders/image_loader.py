"""
Загрузчик для изображений
"""
import os
from typing import List, Dict
from .base import DocumentLoader


class ImageLoader(DocumentLoader):
    """Загрузчик для изображений (для мультимодальной обработки)"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить изображение (вернет путь к файлу для обработки)"""
        return [{
            'content': source,
            'title': os.path.basename(source),
            'metadata': {'type': 'image', 'path': source}
        }]

