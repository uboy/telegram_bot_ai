"""
Модули для загрузки документов различных форматов

ВНИМАНИЕ: Этот файл устарел и сохранен только для обратной совместимости.
Все загрузчики теперь находятся в shared/document_loaders/ (отдельные модули).

Новый импорт (рекомендуется):
    from shared.document_loaders import document_loader_manager, MarkdownLoader, etc.

Старый импорт (работает, но не рекомендуется):
    from shared.document_loaders import document_loader_manager
"""
# Реэкспорт из новой структуры для обратной совместимости
from shared.document_loaders import (
    DocumentLoader,
    DocumentLoaderManager,
    MarkdownLoader,
    PDFLoader,
    WordLoader,
    ExcelLoader,
    WebLoader,
    TextLoader,
    ImageLoader,
    document_loader_manager,
)

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
