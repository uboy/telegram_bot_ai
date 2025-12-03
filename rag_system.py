"""
RAG система для хранения и поиска знаний
"""
import os
import json
import logging
from typing import List, Dict, Optional
from datetime import datetime
import numpy as np
from database import Base, Session, KnowledgeBase, KnowledgeChunk, KnowledgeImportLog

logger = logging.getLogger(__name__)

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
                from config import RAG_MODEL_NAME
                model_name = RAG_MODEL_NAME
            except ImportError:
                model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        
        self.model_name = model_name
        self.encoder = None
        self.index = None
        self.chunks = []
        self.session = Session()
        self.reranker = None
        # Количество кандидатов для векторного поиска перед rerank (минимальный апгрейд)
        # Увеличиваем до 100 для лучшей релевантности при больших базах знаний
        try:
            from config import RAG_MAX_CANDIDATES
            self.max_candidates = RAG_MAX_CANDIDATES
        except ImportError:
            self.max_candidates = int(os.getenv("RAG_MAX_CANDIDATES", "100"))
        
        # Проверить, нужно ли загружать модель
        try:
            from config import RAG_ENABLE
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
                    from config import RAG_DEVICE
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
                    from config import RAG_RERANK_MODEL
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
            from config import RAG_ENABLE
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
                from config import RAG_MODEL_NAME, RAG_RERANK_MODEL, RAG_DEVICE
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
    
    def _load_index(self):
        """Загрузить индекс из базы данных"""
        if not HAS_EMBEDDINGS:
            return
        
        chunks = self.session.query(KnowledgeChunk).all()
        if not chunks:
            return
        
        self.chunks = chunks
        embeddings = []
        valid_chunks = []
        
        for chunk in chunks:
            if chunk.embedding:
                try:
                    embedding = np.array(json.loads(chunk.embedding))
                    embeddings.append(embedding)
                    valid_chunks.append(chunk)
                except:
                    continue
        
        if embeddings:
            self.chunks = valid_chunks
            embeddings = np.array(embeddings).astype('float32')
            self.dimension = embeddings.shape[1]
            self.index = faiss.IndexFlatL2(self.dimension)
            self.index.add(embeddings)
    
    def add_knowledge_base(self, name: str, description: str = "") -> KnowledgeBase:
        """Создать новую базу знаний"""
        kb = KnowledgeBase(name=name, description=description)
        self.session.add(kb)
        self.session.commit()
        return kb
    
    def get_knowledge_base(self, name_or_id) -> Optional[KnowledgeBase]:
        """Получить базу знаний по имени или ID"""
        if isinstance(name_or_id, int):
            return self.session.query(KnowledgeBase).filter_by(id=name_or_id).first()
        return self.session.query(KnowledgeBase).filter_by(name=name_or_id).first()
    
    def list_knowledge_bases(self) -> List[KnowledgeBase]:
        """Список всех баз знаний"""
        return self.session.query(KnowledgeBase).all()
    
    def add_chunk(self, knowledge_base_id: int, content: str, 
                  source_type: str = "text", source_path: str = "",
                  metadata: Optional[Dict] = None) -> KnowledgeChunk:
        """Добавить фрагмент знания"""
        embedding = self._get_embedding(content)
        embedding_json = json.dumps(embedding.tolist()) if embedding is not None else None
        
        chunk = KnowledgeChunk(
            knowledge_base_id=knowledge_base_id,
            content=content,
            chunk_metadata=json.dumps(metadata or {}),
            embedding=embedding_json,
            source_type=source_type,
            source_path=source_path
        )
        self.session.add(chunk)
        self.session.commit()
        
        # Обновить индекс
        if embedding is not None and HAS_EMBEDDINGS:
            if self.index is None:
                self.dimension = embedding.shape[0]
                self.index = faiss.IndexFlatL2(self.dimension)
            self.index.add(embedding.reshape(1, -1).astype('float32'))
            self.chunks.append(chunk)
        
        return chunk
    
    def search(self, query: str, knowledge_base_id: Optional[int] = None, 
               top_k: int = 5) -> List[Dict]:
        """Поиск в базе знаний"""
        if not HAS_EMBEDDINGS or not self.encoder:
            # Упрощенный поиск по ключевым словам
            return self._simple_search(query, knowledge_base_id, top_k)
        
        # Векторный поиск
        query_embedding = self._get_embedding(query)
        if query_embedding is None or self.index is None:
            return self._simple_search(query, knowledge_base_id, top_k)
        
        # Загрузить индекс если не загружен
        if not self.chunks:
            self._load_index()
        
        if self.index is None or len(self.chunks) == 0:
            return []
        
        # Поиск: сначала набираем более широкий пул кандидатов, затем при наличии reranker
        # пересортировываем и оставляем top_k (минимальный апгрейд: top-50 -> rerank -> top-k).
        query_embedding = query_embedding.reshape(1, -1).astype('float32')
        candidate_k = min(self.max_candidates, len(self.chunks))
        distances, indices = self.index.search(query_embedding, candidate_k)
        
        candidates = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.chunks):
                chunk = self.chunks[idx]
                if knowledge_base_id is not None and chunk.knowledge_base_id != knowledge_base_id:
                    continue
                candidates.append(
                    (
                        chunk,
                        float(distances[0][i]),
                    )
                )

        if not candidates:
            return []

        # Если есть reranker, пересчитываем релевантность и берем top_k по score
        if HAS_RERANKER and self.reranker is not None:
            try:
                pairs = [[query, c.content] for (c, _) in candidates]
                scores = self.reranker.predict(pairs)
                # Соединяем кандидатов с их rerank-score
                scored = list(zip(candidates, scores))
                # Сортировка по score по убыванию
                scored.sort(key=lambda x: x[1], reverse=True)
                top = scored[: top_k]

                logger.debug("Reranker применен: обработано %d кандидатов, выбрано top-%d", len(candidates), len(top))
                if top:
                    logger.debug("Лучший rerank_score: %.4f, худший: %.4f", top[0][1], top[-1][1])

                results = []
                for ((chunk, distance), score) in top:
                    results.append(
                        {
                            "content": chunk.content,
                            "metadata": json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {},
                            "source_type": chunk.source_type,
                            "source_path": chunk.source_path,
                            # Оставляем оба показателя для возможной отладки
                            "distance": float(distance),
                            "rerank_score": float(score),
                        }
                    )
                return results
            except Exception as e:
                logger.warning("⚠️ Ошибка работы reranker, продолжаю без него: %s", e)
                import traceback
                logger.debug("Traceback reranker: %s", traceback.format_exc())
                # fallthrough к сортировке только по расстоянию

        # Если reranker недоступен — оставляем поведение по умолчанию (top_k по distance)
        results = []
        for i, (chunk, distance) in enumerate(candidates[: top_k]):
            results.append(
                {
                    "content": chunk.content,
                    "metadata": json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {},
                    "source_type": chunk.source_type,
                    "source_path": chunk.source_path,
                    "distance": float(distance),
                }
            )
        return results
    
    def _simple_search(self, query: str, knowledge_base_id: Optional[int] = None, 
                      top_k: int = 5) -> List[Dict]:
        """Упрощенный поиск по ключевым словам"""
        query_lower = query.lower()
        chunks = self.session.query(KnowledgeChunk).all()
        
        scored_chunks = []
        for chunk in chunks:
            if knowledge_base_id and chunk.knowledge_base_id != knowledge_base_id:
                continue
            
            content_lower = chunk.content.lower()
            score = sum(1 for word in query_lower.split() if word in content_lower)
            if score > 0:
                scored_chunks.append((score, chunk))
        
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
        # Удалить все фрагменты
        chunks = self.session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
        for chunk in chunks:
            self.session.delete(chunk)
        
        # Удалить все записи из журнала загрузок для этой базы знаний
        logs = self.session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
        for log in logs:
            self.session.delete(log)
        
        # Удалить саму базу знаний
        kb = self.session.query(KnowledgeBase).filter_by(id=knowledge_base_id).first()
        if kb:
            self.session.delete(kb)
            self.session.commit()
            
            # Пересоздать индекс
            self.chunks = []
            self.index = None
            self._load_index()
            return True
        return False
    
    def clear_knowledge_base(self, knowledge_base_id: int) -> bool:
        """Очистить базу знаний от всех фрагментов и журнала загрузок"""
        # Удалить все фрагменты
        chunks = self.session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
        for chunk in chunks:
            self.session.delete(chunk)
        
        # Удалить все записи из журнала загрузок для этой базы знаний
        logs = self.session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
        for log in logs:
            self.session.delete(log)
        
        self.session.commit()
        
        # Пересоздать индекс
        self.chunks = []
        self.index = None
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

        q = (
            self.session.query(KnowledgeChunk)
            .filter_by(
                knowledge_base_id=knowledge_base_id,
                source_type=source_type,
                source_path=source_path,
            )
        )
        chunks = q.all()
        deleted = 0
        for chunk in chunks:
            self.session.delete(chunk)
            deleted += 1

        if deleted:
            self.session.commit()
            # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
            self.chunks = []
            self.index = None
            self._load_index()

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

        # Найти все фрагменты в указанной базе знаний и с нужным типом источника
        query = (
            self.session.query(KnowledgeChunk)
            .filter_by(knowledge_base_id=knowledge_base_id, source_type=source_type)
        )
        chunks = query.all()
        deleted = 0

        for chunk in chunks:
            if chunk.source_path and chunk.source_path.startswith(source_prefix):
                self.session.delete(chunk)
                deleted += 1

        if deleted:
            self.session.commit()
            # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
            self.chunks = []
            self.index = None
            self._load_index()

        return deleted


# Глобальный экземпляр RAG системы
rag_system = RAGSystem()

