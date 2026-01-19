"""
Модуль для загрузки документов различных форматов

Каждый загрузчик готовит данные в едином формате:
{
    "content": str,      # Текст фрагмента
    "title": str,        # Заголовок/название фрагмента
    "metadata": dict     # Дополнительные метаданные
}
"""
from typing import List, Dict, Optional
from pathlib import Path

from .base import DocumentLoader
from .markdown_loader import MarkdownLoader
from .pdf_loader import PDFLoader
from .word_loader import WordLoader
from .excel_loader import ExcelLoader
try:
    from .web_loader import WebLoader
except ImportError:  # pragma: no cover - optional dependency for web loading
    WebLoader = None
from .text_loader import TextLoader
from .image_loader import ImageLoader


class DocumentLoaderManager:
    """Менеджер для загрузчиков документов"""
    
    def __init__(self):
        text_loader = TextLoader()
        self.loaders = {
            'markdown': MarkdownLoader(),
            'md': MarkdownLoader(),
            'pdf': PDFLoader(),
            'docx': WordLoader(),
            'doc': WordLoader(),
            'xlsx': ExcelLoader(),
            'xls': ExcelLoader(),
            'txt': text_loader,
            'text': text_loader,
            'image': ImageLoader(),
            'jpg': ImageLoader(),
            'jpeg': ImageLoader(),
            'png': ImageLoader(),
            'gif': ImageLoader(),
        }
        if WebLoader is not None:
            self.loaders['web'] = WebLoader()
            self.loaders['url'] = WebLoader()
    
    def get_loader(self, file_type: str) -> Optional[DocumentLoader]:
        """Получить загрузчик по типу файла"""
        return self.loaders.get(file_type.lower())
    
    def load_document(
        self,
        source: str,
        file_type: Optional[str] = None,
        options: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, str]]:
        """
        Загрузить документ
        
        Args:
            source: Путь к файлу или URL
            file_type: Тип файла (опционально, определяется автоматически)
            
        Returns:
            Список фрагментов документа в едином формате
        """
        if file_type is None:
            # Определить тип по расширению или URL
            if source.startswith('http://') or source.startswith('https://'):
                file_type = 'web'
            else:
                ext = Path(source).suffix.lower().lstrip('.')
                file_type = ext if ext else 'text'
        
        loader = self.get_loader(file_type)
        if loader:
            return loader.load(source, options=options)
        else:
            # Попытка загрузить как текстовый файл (fallback)
            text_loader = TextLoader()
            return text_loader.load(source, options=options)


# Глобальный менеджер загрузчиков
document_loader_manager = DocumentLoaderManager()

# Экспорт для обратной совместимости
__all__ = [
    'DocumentLoader',
    'DocumentLoaderManager',
    'MarkdownLoader',
    'PDFLoader',
    'WordLoader',
    'ExcelLoader',
    'WebLoader',
    'TextLoader',
    'ImageLoader',
    'document_loader_manager',
]

