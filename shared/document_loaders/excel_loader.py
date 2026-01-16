"""
Загрузчик для Excel файлов
"""
from typing import List, Dict
from .base import DocumentLoader


class ExcelLoader(DocumentLoader):
    """Загрузчик для Excel файлов"""
    
    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        """Загрузить Excel файл с учетом структуры листов и таблиц"""
        try:
            import pandas as pd
            chunks: List[Dict[str, str]] = []
            
            excel_file = pd.ExcelFile(source)
            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                
                header_text = f"Лист: {sheet_name}\n\nЗаголовки: {', '.join(df.columns.astype(str).tolist())}\n\n"
                table_text = df.to_string(index=False)
                full_text = header_text + table_text
                
                if len(full_text) <= 2000:
                    chunks.append({
                        "content": full_text,
                        "title": f"Лист: {sheet_name}",
                        "metadata": {"type": "excel", "sheet": sheet_name, "rows": len(df), "cols": len(df.columns)},
                    })
                else:
                    rows_text = table_text.split('\n')
                    current_chunk = header_text
                    
                    for row in rows_text:
                        if len(current_chunk) + len(row) + 1 > 2000:
                            chunks.append({
                                "content": current_chunk.strip(),
                                "title": f"Лист: {sheet_name}",
                                "metadata": {"type": "excel", "sheet": sheet_name},
                            })
                            current_chunk = header_text + row + '\n'
                        else:
                            current_chunk += row + '\n'
                    
                    if current_chunk.strip() and current_chunk.strip() != header_text.strip():
                        chunks.append({
                            "content": current_chunk.strip(),
                            "title": f"Лист: {sheet_name}",
                            "metadata": {"type": "excel", "sheet": sheet_name},
                        })
            
            return chunks if chunks else [
                {"content": "Excel файл пуст", "title": "", "metadata": {"type": "excel"}}
            ]
        except ImportError:
            return [{'content': 'Библиотека pandas не установлена', 'title': '', 'metadata': {}}]
        except Exception as e:
            return [{'content': f"Ошибка загрузки Excel: {str(e)}", 'title': '', 'metadata': {}}]

