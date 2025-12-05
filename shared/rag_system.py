"""
RAG система для хранения и поиска знаний
"""
import os
import json
import logging
import threading
from typing import List, Dict, Optional
from datetime import datetime
from collections import defaultdict
import numpy as np
from shared.database import Base, Session, KnowledgeBase, KnowledgeChunk, KnowledgeImportLog, engine, get_session
from sqlalchemy import text, or_

logger = logging.getLogger(__name__)

# Глобальный lock для всех операций записи в БД (SQLite не любит конкурирующие writers)
_db_write_lock = threading.Lock()

HAS_EMBEDDINGS = False
HAS_RERANKER = False
try:
    from sentence_transformers import SentenceTransformer, CrossEncoder
    import faiss
    HAS_EMBEDDINGS = True
    # Подавить предупреждение о hf_xet, если пакет не установлен
    import warnings
    warnings.filterwarnings('ignore', message='.*hf_xet.*', category=RuntimeWarning)
except ImportError:
    logger.warning("sentence-transformers и faiss не установлены. RAG будет работать в упрощенном режиме.")


# Классы KnowledgeBase и KnowledgeChunk импортируются из database.py


class RAGSystem:
    """Система RAG для поиска в базе знаний"""
    
    def __init__(self, model_name: str = None):
        global HAS_EMBEDDINGS, HAS_RERANKER
        
        # Получить имя модели из конфига, если не указано
        if model_name is None:
            try:
                from shared.config import RAG_MODEL_NAME
                model_name = RAG_MODEL_NAME
            except ImportError:
                model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        
        self.model_name = model_name
        self.encoder = None
        self.index = None  # Устаревший: один индекс для всех KB
        self.chunks = []  # Устаревший: все чанки вместе
        # Индексы по базам знаний (для раздельного поиска)
        self.index_by_kb: Dict[int, faiss.Index] = {}
        self.chunks_by_kb: Dict[int, List[KnowledgeChunk]] = {}
        # Сессии создаются на каждую операцию, не храним глобальную сессию
        self.reranker = None
        # Количество кандидатов для векторного поиска перед rerank (минимальный апгрейд)
        # Увеличиваем до 100 для лучшей релевантности при больших базах знаний
        try:
            from shared.config import RAG_MAX_CANDIDATES
            self.max_candidates = RAG_MAX_CANDIDATES
        except ImportError:
            self.max_candidates = int(os.getenv("RAG_MAX_CANDIDATES", "100"))
        
        # Проверить, нужно ли загружать модель
        try:
            from shared.config import RAG_ENABLE
            if RAG_ENABLE is False:
                HAS_EMBEDDINGS = False
                logger.info("ℹ️ RAG отключен в конфигурации, будет использоваться простой поиск")
                return
        except ImportError:
            pass  # RAG_ENABLE не указан, продолжаем
        
        if HAS_EMBEDDINGS:
            try:
                # Определить путь к кэшу моделей (сохраняется между перезапусками)
                # Используем HF_HOME если установлен, иначе BOT_DATA_DIR
                cache_dir = os.getenv("HF_HOME") or os.path.join(os.getenv("BOT_DATA_DIR", "/app/data"), "cache", "huggingface")
                os.makedirs(cache_dir, exist_ok=True)
                
                # Проверить, есть ли модель в кэше
                import glob
                # sentence-transformers кеширует модели в cache_dir/models--model_name
                model_cache_name = model_name.replace("/", "--")
                model_cache_path = os.path.join(cache_dir, f"models--{model_cache_name}")
                
                if os.path.exists(model_cache_path):
                    logger.info(f"📥 Загрузка модели эмбеддингов из кэша: {model_name}")
                    logger.info(f"   Кэш: {model_cache_path}")
                else:
                    logger.info(f"📥 Загрузка модели эмбеддингов: {model_name}")
                    logger.info("   (Это может занять некоторое время при первом запуске)")
                    logger.info(f"   Кэш будет сохранен в: {cache_dir}")
                
                # Определить устройство для моделей (CPU или GPU)
                try:
                    from shared.config import RAG_DEVICE
                    device = RAG_DEVICE
                except ImportError:
                    device = os.getenv("RAG_DEVICE", "cpu")
                
                # Проверить доступность CUDA если указано GPU
                if device.startswith("cuda"):
                    try:
                        import torch
                        
                        # Расширенная диагностика CUDA
                        logger.info(f"🔍 Проверка доступности CUDA...")
                        logger.info(f"   PyTorch версия: {torch.__version__}")
                        logger.info(f"   CUDA доступна в PyTorch: {torch.cuda.is_available()}")
                        
                        if torch.cuda.is_available():
                            logger.info(f"   CUDA версия: {torch.version.cuda}")
                            logger.info(f"   cuDNN версия: {torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else 'недоступна'}")
                            logger.info(f"   Количество GPU: {torch.cuda.device_count()}")
                            for i in range(torch.cuda.device_count()):
                                logger.info(f"   GPU {i}: {torch.cuda.get_device_name(i)}")
                            logger.info(f"🚀 Использование GPU: {device} (доступно {torch.cuda.device_count()} устройств)")
                        else:
                            # Дополнительная диагностика
                            logger.warning(f"⚠️ CUDA запрошена ({device}), но недоступна в PyTorch.")
                            logger.warning(f"   PyTorch версия: {torch.__version__} (с поддержкой CUDA, но GPU недоступен)")
                            
                            # Проверить доступность GPU устройств в контейнере
                            nvidia_devices_found = False
                            try:
                                # Проверить наличие устройств NVIDIA в /dev
                                nvidia_devices = [f for f in os.listdir('/dev') if f.startswith('nvidia')]
                                if nvidia_devices:
                                    nvidia_devices_found = True
                                    logger.warning(f"   ✅ Устройства NVIDIA найдены в контейнере: {', '.join(nvidia_devices)}")
                                else:
                                    logger.warning(f"   ❌ Устройства NVIDIA не найдены в /dev (контейнер не имеет доступа к GPU)")
                            except Exception as e:
                                logger.warning(f"   ⚠️ Не удалось проверить /dev: {e}")
                            
                            # Проверить nvidia-smi
                            nvidia_smi_available = False
                            try:
                                import subprocess
                                result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=5)
                                if result.returncode == 0:
                                    nvidia_smi_available = True
                                    logger.warning(f"   ✅ nvidia-smi работает в контейнере")
                                    logger.warning(f"   ⚠️ GPU обнаружен в системе, но PyTorch не видит CUDA.")
                                    logger.warning(f"   💡 Возможные причины:")
                                    logger.warning(f"      1. Версия CUDA в PyTorch ({torch.version.cuda if hasattr(torch.version, 'cuda') else 'неизвестна'}) не совпадает с версией драйвера")
                                    logger.warning(f"      2. Несовместимость версий CUDA")
                                else:
                                    logger.warning(f"   ❌ nvidia-smi недоступен или не работает")
                            except FileNotFoundError:
                                logger.warning(f"   ❌ nvidia-smi не установлен в контейнере (GPU недоступен в Docker)")
                            except (subprocess.TimeoutExpired, Exception) as e:
                                logger.warning(f"   ⚠️ Ошибка при проверке nvidia-smi: {e}")
                            
                            # Итоговые рекомендации
                            if not nvidia_devices_found and not nvidia_smi_available:
                                logger.warning(f"   🔧 РЕШЕНИЕ: Настройте доступ к GPU в Docker:")
                                logger.warning(f"      1. Установите nvidia-container-toolkit на хосте")
                                logger.warning(f"      2. Раскомментируйте секцию 'deploy' в docker-compose.yml (сервис 'bot')")
                                logger.warning(f"      3. Перезапустите Docker: sudo systemctl restart docker")
                                logger.warning(f"      4. Пересоберите контейнеры: docker-compose up -d --build")
                            elif nvidia_devices_found or nvidia_smi_available:
                                logger.warning(f"   🔧 РЕШЕНИЕ: GPU доступен в контейнере, но PyTorch его не видит.")
                                logger.warning(f"      Проверьте совместимость версий CUDA в PyTorch и драйвера.")
                            
                            logger.warning(f"   ⚠️ Используется CPU.")
                            device = "cpu"
                    except ImportError:
                        logger.warning("⚠️ PyTorch не установлен, невозможно проверить CUDA. Используется CPU.")
                        device = "cpu"
                
                # Загрузить модель с указанием пути к кэшу и устройства
                # SentenceTransformer автоматически использует кеш если модель уже загружена
                self.encoder = SentenceTransformer(model_name, cache_folder=cache_dir, device=device)
                self.dimension = self.encoder.get_sentence_embedding_dimension()
                logger.info(f"✅ Модель эмбеддингов загружена успешно (размерность: {self.dimension}, устройство: {device})")

                # Попробовать загрузить reranker (минимальный апгрейд качества поиска)
                try:
                    from shared.config import RAG_RERANK_MODEL
                    rerank_model_name = RAG_RERANK_MODEL
                except ImportError:
                    rerank_model_name = os.getenv(
                        "RAG_RERANK_MODEL",
                        "cross-encoder/ms-marco-MiniLM-L-6-v2",
                    )
                
                # Проверить кеш для reranker
                rerank_cache_name = rerank_model_name.replace("/", "--")
                rerank_cache_path = os.path.join(cache_dir, f"models--{rerank_cache_name}")
                
                if os.path.exists(rerank_cache_path):
                    logger.info(f"📥 Загрузка reranker из кэша: {rerank_model_name}")
                else:
                    logger.info(f"📥 Загрузка reranker: {rerank_model_name}...")
                
                try:
                    # Используем то же устройство что и для encoder
                    self.reranker = CrossEncoder(rerank_model_name, cache_folder=cache_dir, device=device)
                    HAS_RERANKER = True
                    logger.info(f"✅ Reranker загружен успешно: {rerank_model_name} (устройство: {device})")
                except Exception as rerank_error:
                    logger.warning(f"⚠️ Не удалось загрузить reranker ({rerank_model_name}): {rerank_error}")
                    logger.info("   Поиск будет работать без reranker'а (только векторный поиск)")
                    self.reranker = None
                    HAS_RERANKER = False

            except Exception as e:
                logger.warning(f"⚠️ Не удалось загрузить модель эмбеддингов: {e}")
                logger.info("   Будет использоваться упрощенный поиск по ключевым словам")
                self.encoder = None
                HAS_EMBEDDINGS = False
    
    def reload_models(self) -> Dict[str, bool]:
        """
        Перезагрузить модели эмбеддингов и ранкинга из конфига в рантайме.
        
        Returns:
            dict с ключами 'embedding' и 'reranker' и значениями True/False (успех/ошибка)
        """
        global HAS_EMBEDDINGS, HAS_RERANKER
        result = {'embedding': False, 'reranker': False}
        
        # Проверить, установлены ли библиотеки (не только флаг HAS_EMBEDDINGS)
        try:
            from sentence_transformers import SentenceTransformer, CrossEncoder
            libraries_available = True
        except ImportError:
            libraries_available = False
        
        if not libraries_available:
            logger.warning("⚠️ Библиотеки sentence-transformers не установлены, перезагрузка невозможна")
            return result
        
        # Проверить, не отключен ли RAG в конфиге
        try:
            from shared.config import RAG_ENABLE
            if RAG_ENABLE is False:
                logger.warning("⚠️ RAG отключен в конфигурации (RAG_ENABLE=false), перезагрузка невозможна")
                return result
        except ImportError:
            pass  # RAG_ENABLE не указан, продолжаем
        
        try:
            # Освобождаем старые модели из памяти
            if self.encoder:
                del self.encoder
                self.encoder = None
            if self.reranker:
                del self.reranker
                self.reranker = None
            
            # Принудительная сборка мусора для освобождения памяти GPU
            import gc
            gc.collect()
            
            # Загружаем новые модели из конфига
            try:
                from shared.config import RAG_MODEL_NAME, RAG_RERANK_MODEL, RAG_DEVICE
                new_model_name = RAG_MODEL_NAME
                new_rerank_model = RAG_RERANK_MODEL
                device = RAG_DEVICE
            except ImportError:
                new_model_name = os.getenv("RAG_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
                new_rerank_model = os.getenv("RAG_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
                device = os.getenv("RAG_DEVICE", "cpu")
            
            # Определить путь к кэшу
            cache_dir = os.getenv("HF_HOME") or os.path.join(os.getenv("BOT_DATA_DIR", "/app/data"), "cache", "huggingface")
            os.makedirs(cache_dir, exist_ok=True)
            
            # Проверить доступность CUDA если указано GPU
            if device.startswith("cuda"):
                try:
                    import torch
                    
                    if not torch.cuda.is_available():
                        logger.warning(f"⚠️ CUDA запрошена ({device}), но недоступна при перезагрузке. Используется CPU.")
                        # Проверить nvidia-smi для диагностики
                        try:
                            import subprocess
                            result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=5)
                            if result.returncode == 0:
                                logger.warning(f"   ⚠️ GPU обнаружен в системе, но PyTorch не видит CUDA.")
                                logger.warning(f"   💡 Проверьте: установлена ли версия PyTorch с поддержкой CUDA")
                        except:
                            pass
                        device = "cpu"
                    else:
                        logger.info(f"🚀 Перезагрузка с GPU: {device} (доступно {torch.cuda.device_count()} устройств)")
                except ImportError:
                    logger.warning("⚠️ PyTorch не установлен, невозможно проверить CUDA. Используется CPU.")
                    device = "cpu"
            
            # Загрузить новую модель эмбеддингов
            try:
                logger.info(f"🔄 Перезагрузка модели эмбеддингов: {new_model_name}")
                self.encoder = SentenceTransformer(new_model_name, cache_folder=cache_dir, device=device)
                self.dimension = self.encoder.get_sentence_embedding_dimension()
                self.model_name = new_model_name
                result['embedding'] = True
                logger.info(f"✅ Модель эмбеддингов перезагружена (размерность: {self.dimension}, устройство: {device})")
            except Exception as e:
                logger.error(f"❌ Ошибка перезагрузки модели эмбеддингов: {e}", exc_info=True)
                result['embedding'] = False
            
            # Загрузить новый reranker
            try:
                logger.info(f"🔄 Перезагрузка reranker: {new_rerank_model}")
                self.reranker = CrossEncoder(new_rerank_model, cache_folder=cache_dir, device=device)
                HAS_RERANKER = True
                result['reranker'] = True
                logger.info(f"✅ Reranker перезагружен (устройство: {device})")
            except Exception as rerank_error:
                logger.warning(f"⚠️ Не удалось перезагрузить reranker ({new_rerank_model}): {rerank_error}")
                self.reranker = None
                HAS_RERANKER = False
                result['reranker'] = False
            
            # Пересоздать индекс при следующем поиске (он будет пересоздан автоматически)
            self.index = None
            self.chunks = []
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при перезагрузке моделей RAG: {e}", exc_info=True)
        
        return result
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Получить эмбеддинг текста"""
        if not HAS_EMBEDDINGS or not self.encoder:
            return None
        try:
            return self.encoder.encode(text, convert_to_numpy=True)
        except Exception as e:
            logger.error(f"Ошибка создания эмбеддинга: {e}")
            return None
    
    def _load_index(self, knowledge_base_id: Optional[int] = None):
        """Загрузить индекс из базы данных (по KB или все)"""
        if not HAS_EMBEDDINGS:
            return
        
        with get_session() as session:
            if knowledge_base_id is not None:
                chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                total_chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).count()
            else:
                chunks = session.query(KnowledgeChunk).all()
                total_chunks = session.query(KnowledgeChunk).count()
            
            if not chunks:
                return
        
        # Группировать чанки по knowledge_base_id и подсчитать coverage
        chunks_by_kb = defaultdict(list)
        chunks_with_embedding = 0
        expected_dim = None
        dim_mismatches = 0
        
        for chunk in chunks:
            if chunk.embedding:
                try:
                    embedding = np.array(json.loads(chunk.embedding))
                    embedding_dim = embedding.shape[0] if len(embedding.shape) == 1 else embedding.shape[1]
                    
                    # Запомнить expected_dim при первой валидной эмбеддинге
                    if expected_dim is None:
                        expected_dim = embedding_dim
                    
                    # Пропустить эмбеддинги с другой размерностью
                    if embedding_dim != expected_dim:
                        dim_mismatches += 1
                        logger.warning(
                            f"Skipping chunk {chunk.id}: embedding dimension {embedding_dim} != expected {expected_dim}"
                        )
                        continue
                    
                    chunks_by_kb[chunk.knowledge_base_id].append((chunk, embedding))
                    chunks_with_embedding += 1
                except Exception as e:
                    logger.debug(f"Failed to parse embedding for chunk {chunk.id}: {e}")
                    continue
        
        # Логировать информацию о несоответствиях размерности
        if dim_mismatches > 0:
            logger.warning(
                f"Skipped {dim_mismatches} chunks with dimension mismatch (expected {expected_dim})"
            )
        
        # Логировать coverage для диагностики
        if total_chunks > 0:
            coverage_pct = (chunks_with_embedding / total_chunks) * 100
            kb_info = f"KB {knowledge_base_id}" if knowledge_base_id is not None else "all KBs"
            logger.info(
                f"Index coverage for {kb_info}: {chunks_with_embedding}/{total_chunks} chunks with embeddings ({coverage_pct:.1f}%)"
            )
            if coverage_pct < 50:
                logger.warning(f"Low embedding coverage ({coverage_pct:.1f}%) - many chunks will fall back to keyword search")
        
        # Построить индексы для каждой KB отдельно
        for kb_id, chunk_emb_pairs in chunks_by_kb.items():
            if not chunk_emb_pairs:
                continue
            
            valid_chunks = [pair[0] for pair in chunk_emb_pairs]
            embeddings = np.array([pair[1] for pair in chunk_emb_pairs]).astype('float32')
            
            # Нормализовать эмбеддинги для cosine similarity
            faiss.normalize_L2(embeddings)
            
            dimension = embeddings.shape[1]
            # Использовать IndexFlatIP (Inner Product) для cosine similarity
            index = faiss.IndexFlatIP(dimension)
            index.add(embeddings)
            
            self.index_by_kb[kb_id] = index
            self.chunks_by_kb[kb_id] = valid_chunks
        
        # Для обратной совместимости: если запрошен общий индекс
        if knowledge_base_id is None and chunks_by_kb:
            # Объединить все чанки для старого API
            all_chunks = []
            all_embeddings = []
            for kb_id, chunk_emb_pairs in chunks_by_kb.items():
                for chunk, emb in chunk_emb_pairs:
                    all_chunks.append(chunk)
                    all_embeddings.append(emb)
            
            if all_embeddings:
                self.chunks = all_chunks
                all_embeddings = np.array(all_embeddings).astype('float32')
                faiss.normalize_L2(all_embeddings)
                self.dimension = all_embeddings.shape[1]
                self.index = faiss.IndexFlatIP(self.dimension)
                self.index.add(all_embeddings)
    
    def add_knowledge_base(self, name: str, description: str = "") -> KnowledgeBase:
        """Создать новую базу знаний"""
        with _db_write_lock:
            with get_session() as session:
                kb = KnowledgeBase(name=name, description=description)
                session.add(kb)
                session.flush()  # Получить ID
                session.refresh(kb)
                return kb
    
    def get_knowledge_base(self, name_or_id) -> Optional[KnowledgeBase]:
        """Получить базу знаний по имени или ID"""
        with get_session() as session:
            if isinstance(name_or_id, int):
                return session.query(KnowledgeBase).filter_by(id=name_or_id).first()
            return session.query(KnowledgeBase).filter_by(name=name_or_id).first()
    
    def list_knowledge_bases(self) -> List[KnowledgeBase]:
        """Список всех баз знаний"""
        with get_session() as session:
            return session.query(KnowledgeBase).all()
    
    def add_chunk(self, knowledge_base_id: int, content: str, 
                  source_type: str = "text", source_path: str = "",
                  metadata: Optional[Dict] = None) -> KnowledgeChunk:
        """Добавить фрагмент знания с retry логикой для обработки блокировок БД"""
        import time
        import random
        max_retries = 10  # Увеличено для длительных блокировок
        base_delay = 0.2  # Увеличено с 0.05 до 0.2 секунды
        
        # Подготовить embedding заранее, чтобы минимизировать время транзакции
        embedding = self._get_embedding(content)
        embedding_json = json.dumps(embedding.tolist()) if embedding is not None else None
        
        with _db_write_lock:
            for attempt in range(max_retries):
                try:
                    with get_session() as session:
                        chunk = KnowledgeChunk(
                            knowledge_base_id=knowledge_base_id,
                            content=content,
                            chunk_metadata=json.dumps(metadata or {}),
                            embedding=embedding_json,
                            source_type=source_type,
                            source_path=source_path
                        )
                        session.add(chunk)
                        session.flush()  # Получить ID
                        session.refresh(chunk)
                    
                    # Убрать инкрементальное обновление индекса - индекс будет пересобран по запросу
                    # Это обеспечивает консистентность с cosine similarity и per-KB индексами
                    if embedding is not None and HAS_EMBEDDINGS:
                        self.index = None
                        self.chunks = []
                        self.index_by_kb.clear()
                        self.chunks_by_kb.clear()
                    
                    return chunk
                except Exception as e:
                    if "locked" in str(e).lower() or "database is locked" in str(e):
                        if attempt < max_retries - 1:
                            # Экспоненциальный backoff с джиттером
                            delay = base_delay * (2 ** attempt)
                            jitter = delay * 0.2 * (random.random() * 2 - 1)
                            delay_with_jitter = max(0.1, delay + jitter)
                            logger.warning(
                                f"База данных заблокирована, попытка {attempt + 1}/{max_retries}, "
                                f"повтор через {delay_with_jitter:.2f}с (timeout=60s, busy_timeout=60000ms)"
                            )
                            time.sleep(delay_with_jitter)
                            continue
                        else:
                            logger.error(
                                f"Не удалось добавить чанк после {max_retries} попыток: {e} "
                                f"(timeout=60s, busy_timeout=60000ms)"
                            )
                            raise
                    else:
                        raise
    
    def add_chunks_batch(self, chunks_data: List[Dict]) -> List[KnowledgeChunk]:
        """
        Добавить несколько фрагментов знания пакетно (оптимизировано для SQLite)
        
        Использует двухфазную запись:
        1. Вставка чанков без embedding (быстро)
        2. Обновление embedding батчами (минимизирует блокировки)
        
        Args:
            chunks_data: Список словарей с данными чанков:
                {
                    'knowledge_base_id': int,
                    'content': str,
                    'source_type': str,
                    'source_path': str,
                    'metadata': dict (опционально)
                }
        
        Returns:
            Список созданных KnowledgeChunk объектов
        """
        import time
        import random
        max_retries = 10  # Увеличено для длительных блокировок
        base_delay = 0.2  # Увеличено с 0.02 до 0.2 секунды
        batch_size = 50  # Размер батча для bulk операций
        
        with _db_write_lock:
            # Подготовить все embeddings заранее (до любых транзакций)
            prepared_data = []
            for chunk_data in chunks_data:
                content = chunk_data.get('content', '')
                embedding = self._get_embedding(content)
                embedding_json = json.dumps(embedding.tolist()) if embedding is not None else None
                prepared_data.append((chunk_data, embedding, embedding_json))
            
            all_chunks = []
            chunks_with_embeddings = []  # (chunk_id, embedding_json, embedding) для второй фазы
            
            # Фаза 1: Вставка чанков без embedding (быстро, минимизирует блокировки)
            for batch_start in range(0, len(prepared_data), batch_size):
                batch_data = prepared_data[batch_start:batch_start + batch_size]
                
                for attempt in range(max_retries):
                    try:
                        with get_session() as session:
                            chunks_to_add = []
                            batch_embeddings = []
                            
                            for chunk_data, embedding, embedding_json in batch_data:
                                chunk = KnowledgeChunk(
                                    knowledge_base_id=chunk_data['knowledge_base_id'],
                                    content=chunk_data.get('content', ''),
                                    chunk_metadata=json.dumps(chunk_data.get('metadata') or {}),
                                    embedding=None,  # Вставляем без embedding сначала
                                    source_type=chunk_data.get('source_type', 'text'),
                                    source_path=chunk_data.get('source_path', '')
                                )
                                chunks_to_add.append(chunk)
                                if embedding_json:
                                    batch_embeddings.append((chunk, embedding_json, embedding))
                            
                            # Использовать add_all для вставки
                            session.add_all(chunks_to_add)
                            session.flush()  # Получить IDs
                            
                            # Сохранить все чанки (исправление C)
                            all_chunks.extend(chunks_to_add)
                            
                            # Сохранить для второй фазы (ID доступны после flush)
                            for chunk, emb_json, emb in batch_embeddings:
                                # ID должен быть доступен после flush
                                if hasattr(chunk, 'id') and chunk.id:
                                    chunks_with_embeddings.append((chunk.id, emb_json, emb))
                                else:
                                    logger.warning(f"Не удалось получить ID для чанка, будет пропущен embedding")
                        
                        break  # Успешно добавлено
                    except Exception as e:
                        if "locked" in str(e).lower() or "database is locked" in str(e):
                            if attempt < max_retries - 1:
                                delay = base_delay * (2 ** attempt)
                                if attempt == 0:
                                    logger.warning(f"База данных заблокирована при вставке батча {batch_start//batch_size + 1}, повторная попытка через {delay:.2f}с")
                                time.sleep(delay)
                                continue
                            else:
                                logger.error(f"Не удалось добавить батч после {max_retries} попыток: {e}")
                                raise
                        else:
                            raise
                
                # Небольшая задержка между батчами
                if batch_start + batch_size < len(prepared_data):
                    time.sleep(0.01)
            
            # Фаза 2: Обновление embedding короткими пачками с commit после каждой (исправление B)
            if chunks_with_embeddings:
                embedding_batch_size = 30  # Уменьшено для коротких транзакций
                for batch_start in range(0, len(chunks_with_embeddings), embedding_batch_size):
                    batch_embeddings = chunks_with_embeddings[batch_start:batch_start + embedding_batch_size]
                    
                    for attempt in range(max_retries):
                        try:
                            # Короткая транзакция: update + commit
                            with get_session() as session:
                                for chunk_id, embedding_json, _ in batch_embeddings:
                                    session.query(KnowledgeChunk).filter_by(id=chunk_id).update(
                                        {'embedding': embedding_json}
                                    )
                                # commit выполнится автоматически при выходе из with
                            break
                        except Exception as e:
                            if "locked" in str(e).lower() or "database is locked" in str(e):
                                if attempt < max_retries - 1:
                                    # Экспоненциальный backoff с джиттером
                                    delay = base_delay * (2 ** attempt)
                                    jitter = delay * 0.2 * (random.random() * 2 - 1)
                                    delay_with_jitter = max(0.1, delay + jitter)
                                    logger.warning(
                                        f"База данных заблокирована при обновлении embedding батча, "
                                        f"попытка {attempt + 1}/{max_retries}, повтор через {delay_with_jitter:.2f}с"
                                    )
                                    time.sleep(delay_with_jitter)
                                    continue
                                else:
                                    logger.error(
                                        f"Не удалось обновить embedding батча после {max_retries} попыток: {e} "
                                        f"(timeout=60s, busy_timeout=60000ms)"
                                    )
                                    # Не прерываем процесс, просто логируем ошибку
                                    break
                            else:
                                raise
            
            # Исправление D: отключить инкрементальное обновление индекса после батча
            # Индекс будет пересобран по запросу через _load_index()
            # Это снижает число обращений к БД в момент записи
            # После успешного большого импорта просто сбрасываем индекс
            if HAS_EMBEDDINGS and chunks_with_embeddings:
                self.index = None
                self.chunks = []
                # Очистить индексы по KB
                self.index_by_kb.clear()
                self.chunks_by_kb.clear()
                # Индекс будет пересобран при следующем поиске через _load_index()
        
        return all_chunks
    
    def search(self, query: str, knowledge_base_id: Optional[int] = None, 
               top_k: int = 5) -> List[Dict]:
        """Поиск в базе знаний (dense + keyword с возможным rerank)."""
        import re
        # Токенизация запроса для использования в how-to бустах
        query_words = re.findall(r'\w+', query.lower())
        
        # Если эмбеддинги недоступны – используем только упрощённый поиск
        if not HAS_EMBEDDINGS or not self.encoder:
            return self._simple_search(query, knowledge_base_id, top_k)
        
        # Загрузить индекс если не загружен (исправление логической ошибки)
        if knowledge_base_id is not None:
            if knowledge_base_id not in self.index_by_kb:
                self._load_index(knowledge_base_id)
        else:
            if not self.index_by_kb:
                self._load_index(None)
        
        # Векторный поиск
        query_embedding = self._get_embedding(query)
        if query_embedding is None:
            return self._simple_search(query, knowledge_base_id, top_k)
        
        # Определить, использовать ли индекс по KB или общий
        if knowledge_base_id is not None:
            if knowledge_base_id not in self.index_by_kb:
                return self._simple_search(query, knowledge_base_id, top_k)
            index = self.index_by_kb[knowledge_base_id]
            chunks = self.chunks_by_kb[knowledge_base_id]
        else:
            if self.index is None or len(self.chunks) == 0:
                return self._simple_search(query, knowledge_base_id, top_k)
            index = self.index
            chunks = self.chunks
        
        if len(chunks) == 0:
            return []
        
        # Нормализовать query embedding для cosine similarity
        query_embedding = query_embedding.reshape(1, -1).astype('float32')
        faiss.normalize_L2(query_embedding)
        
        # Определить режим поиска (how-to или обычный)
        is_howto_query = self._is_howto_query(query)
        
        # Dense‑поиск: широкий пул кандидатов
        # Для how-to запросов увеличиваем candidate_k (до 300-500 для больших баз)
        if is_howto_query:
            candidate_k = min(max(300, self.max_candidates * 3), len(chunks), 500)
        else:
            candidate_k = min(self.max_candidates, len(chunks))
        scores, indices = index.search(query_embedding, candidate_k)
        
        dense_candidates: List[Dict] = []
        for i, idx in enumerate(indices[0]):
            if idx < len(chunks):
                chunk = chunks[idx]
                # KB уже отфильтрован через индекс, дополнительная проверка не нужна
                metadata = json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {}
                
                # Для how-to запросов даем буст code/list чанкам
                similarity = float(scores[0][i])  # Это уже cosine similarity (inner product, может быть от -1 до 1)
                if is_howto_query:
                    chunk_kind = metadata.get("chunk_kind", "text")
                    if chunk_kind in ("code", "list"):
                        similarity *= 1.5  # Буст для code/list в how-to режиме
                    
                    # Буст за совпадение в section_path
                    section_path = (metadata.get("section_path") or "").lower()
                    if section_path and any(word in section_path for word in query_words):
                        similarity *= 1.2
                
                # Для cosine similarity: distance = -similarity (сортировка по возрастанию distance = по убыванию similarity)
                distance = -similarity
                
                dense_candidates.append(
                    {
                        "content": chunk.content,
                        "metadata": metadata,
                        "source_type": chunk.source_type,
                        "source_path": chunk.source_path,
                        "distance": distance,
                        "similarity": similarity,  # Сохраняем similarity для отладки
                        "origin": "dense",
                    }
                )

        # Keyword‑поиск (BM25‑подобный) как дополнительный источник кандидатов
        keyword_candidates = self._simple_search(
            query,
            knowledge_base_id=knowledge_base_id,
            top_k=self.max_candidates,
        )
        for kc in keyword_candidates:
            kc.setdefault("origin", "bm25")

        # Объединяем кандидатов и убираем дубли по (source_path, content)
        merged: List[Dict] = []
        seen = set()
        for cand in dense_candidates + keyword_candidates:
            key = (cand.get("source_path") or "", (cand.get("content") or "")[:200])
            if key in seen:
                continue
            seen.add(key)
            merged.append(cand)

        if not merged:
            return []

        # Если есть reranker – пересчитываем релевантность и берём top_k по score
        if HAS_RERANKER and self.reranker is not None:
            try:
                pairs = [[query, c.get("content", "")] for c in merged]
                scores = self.reranker.predict(pairs)

                scored = list(zip(merged, scores))
                scored.sort(key=lambda x: x[1], reverse=True)
                top = scored[: top_k]

                logger.debug(
                    "Reranker применен: обработано %d кандидатов, выбрано top-%d",
                    len(merged),
                    len(top),
                )
                if top:
                    logger.debug(
                        "Лучший rerank_score: %.4f, худший: %.4f",
                        float(top[0][1]),
                        float(top[-1][1]),
                    )

                results = []
                for cand, score in top:
                    results.append(
                        {
                            "content": cand.get("content", ""),
                            "metadata": cand.get("metadata") or {},
                            "source_type": cand.get("source_type"),
                            "source_path": cand.get("source_path"),
                            "distance": float(cand.get("distance", 0.0)),
                            "rerank_score": float(score),
                            "origin": cand.get("origin"),
                        }
                    )
                return results
            except Exception as e:
                logger.warning("⚠️ Ошибка работы reranker, продолжаю без него: %s", e)
                import traceback
                logger.debug("Traceback reranker: %s", traceback.format_exc())
                # fallthrough к сортировке merged (dense + keyword)
        
        # Если reranker недоступен — fallback: смешанный ранжир для how-to запросов
        # Для how-to: приоритет code/list, затем bm25, затем distance
        # Для обычных: сортировка по distance
        if is_howto_query:
            # Для how-to: is_code_or_list (code/list выше) → origin_priority (bm25 выше) → distance
            def sort_key_howto(c):
                metadata = c.get("metadata") or {}
                chunk_kind = metadata.get("chunk_kind", "text")
                is_code_or_list = 0 if chunk_kind in ("code", "list") else 1  # code/list = 0 (выше)
                origin_priority = 0 if c.get("origin") == "bm25" else 1  # bm25 = 0 (выше), dense = 1
                distance = float(c.get("distance", float("inf")))
                return (is_code_or_list, origin_priority, distance)
            merged_sorted = sorted(merged, key=sort_key_howto)[: top_k]
        else:
            # Для обычных запросов: только по distance
            merged_sorted = sorted(merged, key=lambda c: float(c.get("distance", float("inf"))))[: top_k]
        
        results = []
        for cand in merged_sorted:
            results.append(
                {
                    "content": cand.get("content", ""),
                    "metadata": cand.get("metadata") or {},
                    "source_type": cand.get("source_type"),
                    "source_path": cand.get("source_path"),
                    "distance": float(cand.get("distance", 0.0)),
                    "origin": cand.get("origin", "dense"),
                }
            )
        return results
    
    def _is_howto_query(self, query: str) -> bool:
        """Определить, является ли запрос запросом типа 'how-to' (инструкция/процедура)."""
        import re
        query_lower = query.lower()
        
        # Ключевые слова, указывающие на how-to запрос
        howto_keywords = [
            'how to', 'howto', 'how do', 'how can', 'how should',
            'initialize', 'init', 'setup', 'set up', 'install', 'configure',
            'create', 'build', 'compile', 'sync', 'sync and build',
            'run', 'execute', 'start', 'begin', 'get started',
            'tutorial', 'guide', 'steps', 'procedure', 'process',
            'command', 'example', 'demo'
        ]
        
        # Проверка на наличие how-to ключевых слов
        for keyword in howto_keywords:
            if keyword in query_lower:
                return True
        
        # Проверка на паттерны типа "как сделать", "как создать" и т.д.
        russian_howto_patterns = [
            r'как\s+(сделать|создать|настроить|установить|запустить|начать)',
            r'инструкция',
            r'руководство',
            r'шаги',
        ]
        for pattern in russian_howto_patterns:
            if re.search(pattern, query_lower):
                return True
        
        return False
    
    def _simple_search(self, query: str, knowledge_base_id: Optional[int] = None, 
                      top_k: int = 5) -> List[Dict]:
        """Упрощенный поиск по ключевым словам"""
        import re
        import json
        # Улучшенная токенизация: разбиваем по пробелам и специальным символам
        query_lower = query.lower()
        # Разбиваем по пробелам, амперсандам, дефисам и другим разделителям
        query_words = re.findall(r'\w+', query_lower)
        
        # Определить режим поиска
        is_howto = self._is_howto_query(query)
        
        # Сильные токены для how-to запросов (команды, флаги, ключевые слова)
        strong_tokens = ['repo', '--depth', '--reference', 'mkdir', 'cd', 'git', 'init', 'sync', 
                        'build', 'compile', 'install', 'docker', 'npm', 'yarn', 'pip', 'apt', 'yum']
        
        with get_session() as session:
            # Для how-to запросов с сильными токенами: предварительный SQL-фильтр
            if is_howto:
                # Найти сильные токены в запросе
                found_strong_tokens = [token for token in strong_tokens if token in query_lower]
                
                if found_strong_tokens:
                    # Построить SQL-фильтр: content LIKE '%token%' OR content LIKE '%token%' ...
                    filters = []
                    for token in found_strong_tokens:
                        # Ищем токен как отдельное слово или как часть команды
                        filters.append(KnowledgeChunk.content.like(f'%{token}%'))
                    
                    # Базовый запрос с фильтром по KB
                    query_obj = session.query(KnowledgeChunk)
                    if knowledge_base_id is not None:
                        query_obj = query_obj.filter_by(knowledge_base_id=knowledge_base_id)
                    
                    # Применить SQL-фильтр по сильным токенам
                    chunks = query_obj.filter(or_(*filters)).all()
                    logger.debug(f"Pre-filtered {len(chunks)} chunks using strong tokens: {found_strong_tokens}")
                else:
                    # Нет сильных токенов - загружаем все (как раньше)
                    if knowledge_base_id is not None:
                        chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                    else:
                        chunks = session.query(KnowledgeChunk).all()
            else:
                # Для обычных запросов - загружаем все (как раньше)
                if knowledge_base_id is not None:
                    chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                else:
                    chunks = session.query(KnowledgeChunk).all()
        
        scored_chunks = []
        for chunk in chunks:
            content_lower = chunk.content.lower()
            # Также проверяем source_path для лучшего поиска по именам файлов
            source_path_lower = (chunk.source_path or "").lower()
            
            # Извлекаем метаданные для поиска по заголовкам
            metadata = {}
            try:
                if chunk.chunk_metadata:
                    metadata = json.loads(chunk.chunk_metadata)
            except:
                pass
            
            # Поиск в заголовке и section_title (важно для поиска по заголовкам документов)
            title_lower = (metadata.get("title") or "").lower()
            section_title_lower = (metadata.get("section_title") or "").lower()
            section_path_lower = (metadata.get("section_path") or "").lower()
            chunk_kind = metadata.get("chunk_kind", "text")
            
            # Подсчет совпадений в контенте
            content_score = sum(1 for word in query_words if word in content_lower)
            
            # Бонус за совпадение в заголовке (очень важно)
            title_score = sum(2 for word in query_words if word in title_lower)
            
            # Бонус за совпадение в section_title
            section_score = sum(1.5 for word in query_words if word in section_title_lower)
            
            # Бонус за совпадение в section_path
            section_path_score = sum(1.5 for word in query_words if word in section_path_lower)
            
            # Бонус за совпадение в имени файла/пути
            path_score = sum(1 for word in query_words if word in source_path_lower)
            
            # Для how-to запросов: буст для code/list чанков
            chunk_kind_boost = 0
            if is_howto and chunk_kind in ("code", "list"):
                chunk_kind_boost = 3
            
            # Поиск командных строк в контенте (для how-to)
            command_score = 0
            if is_howto:
                command_pattern = r'(^|\n)(repo|git|mkdir|cd|python|docker|npm|yarn|pip|apt|yum)\b'
                if re.search(command_pattern, chunk.content, re.IGNORECASE):
                    command_score = 2
            
            # Также проверяем точное совпадение фразы (для запросов типа "Initialize repository and sync code")
            phrase_in_content = query_lower in content_lower
            phrase_in_title = query_lower in title_lower
            phrase_in_section = query_lower in section_title_lower
            phrase_in_section_path = query_lower in section_path_lower
            
            total_score = (
                content_score + 
                title_score * 3 +  # Заголовок очень важен
                section_score * 2 +
                section_path_score * 2.5 +  # section_path важен для навигации
                path_score * 2 +
                chunk_kind_boost +
                command_score +
                (10 if phrase_in_title else 0) +  # Большой бонус за точное совпадение в заголовке
                (8 if phrase_in_section_path else 0) +
                (5 if phrase_in_section else 0) +
                (3 if phrase_in_content else 0)
            )
            
            if total_score > 0:
                scored_chunks.append((total_score, chunk))
        
        scored_chunks.sort(reverse=True, key=lambda x: x[0])
        
        results = []
        for score, chunk in scored_chunks[:top_k]:
            results.append({
                'content': chunk.content,
                'metadata': json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {},
                'source_type': chunk.source_type,
                'source_path': chunk.source_path,
                'distance': 1.0 / (score + 1)  # Обратное расстояние
            })
        
        return results
    
    def delete_knowledge_base(self, knowledge_base_id: int) -> bool:
        """Удалить базу знаний, все её фрагменты и журнал загрузок"""
        with _db_write_lock:
            with get_session() as session:
                # Удалить все фрагменты
                chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                for chunk in chunks:
                    session.delete(chunk)
                
                # Удалить все записи из журнала загрузок для этой базы знаний
                logs = session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
                for log in logs:
                    session.delete(log)
                
                # Удалить саму базу знаний
                kb = session.query(KnowledgeBase).filter_by(id=knowledge_base_id).first()
                if kb:
                    session.delete(kb)
                    session.flush()
                
                if not kb:
                    return False
            
            # Пересоздать индекс (вне сессии)
            self.chunks = []
            self.index = None
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            self._load_index()
            return True
    
    def clear_knowledge_base(self, knowledge_base_id: int) -> bool:
        """Очистить базу знаний от всех фрагментов и журнала загрузок"""
        with _db_write_lock:
            with get_session() as session:
                # Удалить все фрагменты
                chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                for chunk in chunks:
                    session.delete(chunk)
                
                # Удалить все записи из журнала загрузок для этой базы знаний
                logs = session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
                for log in logs:
                    session.delete(log)
                session.flush()
            
            # Пересоздать индекс (вне сессии)
            self.chunks = []
            self.index = None
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            self._load_index()
            return True

    def delete_chunks_by_source_exact(
        self,
        knowledge_base_id: int,
        source_type: str,
        source_path: str,
    ) -> int:
        """
        Удалить фрагменты знаний для конкретного источника в рамках БЗ.

        Используется при обновлении документов: новая версия заменяет старые данные.
        """
        if not source_path:
            return 0

        with _db_write_lock:
            with get_session() as session:
                q = (
                    session.query(KnowledgeChunk)
                    .filter_by(
                        knowledge_base_id=knowledge_base_id,
                        source_type=source_type,
                        source_path=source_path,
                    )
                )
                chunks = q.all()
                deleted = 0
                for chunk in chunks:
                    session.delete(chunk)
                    deleted += 1
                session.flush()

            if deleted:
                # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
                self.chunks = []
                self.index = None
                # Удалить индекс для этой KB
                if knowledge_base_id in self.index_by_kb:
                    del self.index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.chunks_by_kb:
                    del self.chunks_by_kb[knowledge_base_id]
                # Пересоздать индекс для этой KB
                self._load_index(knowledge_base_id)

            return deleted

    def delete_chunks_by_source_prefix(
        self,
        knowledge_base_id: int,
        source_type: str,
        source_prefix: str,
    ) -> int:
        """
        Удалить фрагменты знаний по префиксу источника (например, все страницы одной вики).
        
        Это используется wiki-скрепером для пересборки вики без очистки всей базы знаний.
        """
        if not source_prefix:
            return 0

        with _db_write_lock:
            with get_session() as session:
                # Найти все фрагменты в указанной базе знаний и с нужным типом источника
                query = (
                    session.query(KnowledgeChunk)
                    .filter_by(knowledge_base_id=knowledge_base_id, source_type=source_type)
                )
                chunks = query.all()
                deleted = 0

                for chunk in chunks:
                    if chunk.source_path and chunk.source_path.startswith(source_prefix):
                        session.delete(chunk)
                        deleted += 1
                session.flush()

            if deleted:
                # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
                self.chunks = []
                self.index = None
                # Удалить индекс для этой KB
                if knowledge_base_id in self.index_by_kb:
                    del self.index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.chunks_by_kb:
                    del self.chunks_by_kb[knowledge_base_id]
                # Пересоздать индекс для этой KB
                self._load_index(knowledge_base_id)

            return deleted


# Глобальный экземпляр RAG системы
rag_system = RAGSystem()

