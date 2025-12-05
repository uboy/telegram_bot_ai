"""
Модуль для обработки изображений с помощью мультимодальных моделей
"""
import os
import tempfile
from typing import Optional
from shared.ai_providers import ai_manager


class ImageProcessor:
    """Обработчик изображений"""
    
    def __init__(self, multimodal_provider: Optional[str] = None):
        self.multimodal_provider = multimodal_provider
    
    def describe_image(
        self,
        image_path: str,
        prompt: str = "Опиши подробно, что изображено на этой картинке. Будь детальным и точным.",
        model: Optional[str] = None,
    ) -> str:
        """Описать изображение с помощью мультимодальной модели"""
        # Используем менеджер провайдеров с возможностью указать модель (например, выбранную пользователем)
        result = ai_manager.query_multimodal(
            prompt,
            image_path=image_path,
            provider_name=self.multimodal_provider,
            model=model,
        )
        return result
    
    def extract_text_from_image(self, image_path: str) -> str:
        """Извлечь текст из изображения (OCR)"""
        try:
            import pytesseract
            from PIL import Image
            
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, lang='rus+eng')
            return text.strip()
        except ImportError:
            return "Библиотеки pytesseract и Pillow не установлены для OCR"
        except Exception as e:
            return f"Ошибка OCR: {str(e)}"
    
    def process_image_for_rag(self, image_path: str, model: Optional[str] = None) -> str:
        """Обработать изображение для добавления в RAG"""
        # Получить описание изображения
        description = self.describe_image(
            image_path,
            "Опиши подробно, что изображено на этой картинке. Включи все детали, текст (если есть), объекты, цвета, композицию.",
            model=model,
        )
        
        # Попытаться извлечь текст, если есть
        ocr_text = self.extract_text_from_image(image_path)
        
        # Объединить описание и текст
        result = f"Описание изображения:\n{description}\n\n"
        if ocr_text and len(ocr_text) > 10:
            result += f"Текст на изображении:\n{ocr_text}\n"
        
        return result


# Глобальный обработчик изображений
image_processor = ImageProcessor()

