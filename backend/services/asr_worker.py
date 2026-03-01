from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Optional, List, Dict, Any, Protocol

from datetime import datetime, timezone
import wave
import numpy as np

from shared.logging_config import logger
from shared.database import Session, AppSettings

from backend.services.asr_queue import (
    AsrJob,
    get_queue_size,
    mark_done,
    next_job,
    set_job_status,
)

# Глобальный кэш движков (ленивая загрузка)
_engine_cache: Dict[str, ASREngine] = {}
_engine_lock = threading.Lock()

_workers_started = False
_workers_lock = threading.Lock()


class ASREngine(Protocol):
    """Интерфейс для движков распознавания речи."""
    def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        ...


class LegacyTransformersEngine:
    """Движок на базе стандартной библиотеки transformers (оптимизированный)."""
    def __init__(self, model_name: str, device: str):
        self.model_name = model_name
        self.device = device
        self.processor = None
        self.model = None
        self._load_model()

    def _load_model(self):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        
        logger.info("ASR: Loading Transformers model '%s' on %s (FP16)", self.model_name, self.device)
        
        # Определяем тип данных: FP16 для GPU, FP32 для CPU
        dtype = torch.float16 if "cuda" in self.device else torch.float32
        
        self.processor = AutoProcessor.from_pretrained(self.model_name)
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.model_name,
            dtype=dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True
        )
        self.model.to(self.device)
        self.model.eval()

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        import torch
        
        audio_data = self._load_audio(audio_path)
        inputs = self.processor(
            audio_data["raw"], 
            sampling_rate=audio_data["sampling_rate"], 
            return_tensors="pt"
        )
        
        # Переносим все входные данные на устройство и нужный тип
        input_features = inputs.input_features.to(self.device).to(self.model.dtype)
        attention_mask = getattr(inputs, "attention_mask", None)
        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)
        
        generate_kwargs = {"task": "transcribe"}
        if language:
            generate_kwargs["language"] = language
        
        if attention_mask is not None:
            generate_kwargs["attention_mask"] = attention_mask

        with torch.no_grad():
            generated_ids = self.model.generate(input_features, **generate_kwargs)
        
        text = self.processor.batch_decode(generated_ids, skip_special_tokens=True)
        return (text[0] if text else "").strip()

    def _load_audio(self, audio_path: str) -> dict:
        with wave.open(audio_path, "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        
        audio = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        audio = audio.astype(np.float32) / 32768.0
        return {"raw": audio, "sampling_rate": sample_rate}


class FasterWhisperEngine:
    """Движок на базе faster-whisper (движок CTranslate2)."""
    def __init__(self, model_name: str, device: str):
        self.model_name = model_name
        # faster-whisper ожидает 'cuda' или 'cpu', а не 'cuda:0'
        self.device = "cuda" if "cuda" in device else "cpu"
        self.device_index = int(device.split(":")[1]) if ":" in device else 0
        self.model = None
        self._load_model()

    def _load_model(self):
        from faster_whisper import WhisperModel
        
        # Определяем тип вычислений: float16 для GPU, int8 для CPU
        compute_type = "float16" if self.device == "cuda" else "int8"
        
        logger.info(
            "ASR: Loading Faster-Whisper model '%s' on %s (compute=%s)", 
            self.model_name, self.device, compute_type
        )
        
        # ВАЖНО: model_name для faster-whisper должен быть либо коротким (medium), 
        # либо полным путем к CTranslate2 модели. Мы пробуем использовать как есть.
        self.model = WhisperModel(
            self.model_name, 
            device=self.device, 
            device_index=self.device_index,
            compute_type=compute_type
        )

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        # faster-whisper работает напрямую с путем к файлу или байтами
        segments, info = self.model.transcribe(
            audio_path, 
            language=language, 
            beam_size=5
        )
        
        text_parts = [segment.text for segment in segments]
        return " ".join(text_parts).strip()


def get_asr_engine(provider: str, model_name: str, device: str) -> ASREngine:
    """Фабрика для получения движка ASR с кэшированием."""
    engine_key = f"{provider}:{model_name}:{device}"
    
    with _engine_lock:
        if engine_key in _engine_cache:
            return _engine_cache[engine_key]
        
        # Очищаем старые движки, если сменился провайдер или модель (экономия памяти)
        if _engine_cache:
            logger.info("ASR: Clearing engine cache to free memory")
            _engine_cache.clear()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        if provider == "faster-whisper":
            try:
                engine = FasterWhisperEngine(model_name, device)
            except Exception as e:
                logger.error("ASR: Failed to load Faster-Whisper, fallback to transformers: %s", e)
                engine = LegacyTransformersEngine(model_name, device)
        else:
            engine = LegacyTransformersEngine(model_name, device)
            
        _engine_cache[engine_key] = engine
        return engine


def _detect_gpu_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass
    return "cpu"


def get_system_info() -> dict:
    """Получить детальную информацию о железе для ASR."""
    info = {
        "device_type": "cpu",
        "cpu_info": "Unknown",
        "gpu_info": None,
        "vram_total": 0,
        "vram_free": 0
    }
    
    try:
        import torch
        if torch.cuda.is_available():
            info["device_type"] = "cuda"
            info["gpu_info"] = torch.cuda.get_device_name(0)
            info["vram_total"] = torch.cuda.get_device_properties(0).total_memory // (1024**2) # MB
            info["vram_free"] = (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated(0)) // (1024**2)
        
        # Получаем инфо о CPU (упрощенно для Linux)
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["cpu_info"] = line.split(":")[1].strip()
                        break
    except Exception:
        pass
    return info


def _get_settings() -> tuple[str, str, str]:
    session = Session()
    try:
        settings = session.query(AppSettings).first()
        if not settings:
            return "transformers", "openai/whisper-large-v3-turbo", _detect_gpu_device()
        
        provider = settings.asr_provider or "transformers"
        model = settings.asr_model_name or "openai/whisper-large-v3-turbo"
        device = settings.asr_device or _detect_gpu_device()
        return provider, model, device
    finally:
        session.close()


def _worker_loop(worker_id: int):
    logger.info("ASR worker %s started", worker_id)
    
    while True:
        job = next_job()
        started_at = time.monotonic()
        try:
            set_job_status(job.job_id, "processing")
            
            provider, model_name, device = _get_settings()
            
            # Подготовка аудио (конвертация в 16kHz mono WAV)
            audio_path = _prepare_audio(job.file_path)
            
            # Получение движка
            engine = get_asr_engine(provider, model_name, device)
            
            # Транскрибация
            logger.info("ASR job %s: processing with %s (%s)", job.job_id, provider, model_name)
            text = engine.transcribe(audio_path, language=job.language)
            
            if not text:
                logger.warning("ASR job %s: result is empty", job.job_id)
            
            duration = time.monotonic() - started_at
            
            # Метаданные для ответа
            audio_info = _probe_audio(audio_path)
            audio_meta = dict(job.audio_meta or {})
            audio_meta.update(audio_info)
            
            timing_meta = {
                "processing_s": duration,
                "finished_at": datetime.now(timezone.utc).isoformat()
            }
            
            set_job_status(
                job.job_id, "done", 
                text=text, 
                audio_meta=audio_meta, 
                timing_meta=timing_meta
            )
            logger.info("ASR job %s: completed in %.2fs", job.job_id, duration)
            
        except Exception as e:
            logger.error("ASR job %s: failed: %s", job.job_id, e, exc_info=True)
            set_job_status(job.job_id, "error", error=str(e))
        finally:
            _cleanup_files(job.file_path)
            mark_done(job)


def _prepare_audio(input_path: str) -> str:
    """Конвертация в формат, который понимают все движки (WAV 16kHz Mono)."""
    if not shutil.which("ffmpeg"):
        return input_path
        
    output_path = f"{input_path}.wav"
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-f", "wav",
        output_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_path


def _probe_audio(path: str) -> dict:
    try:
        with wave.open(path, "rb") as wav:
            return {
                "duration_s": wav.getnframes() / wav.getframerate(),
                "sample_rate": wav.getframerate(),
                "channels": wav.getnchannels()
            }
    except Exception:
        return {}


def _cleanup_files(base_path: str):
    for path in [base_path, f"{base_path}.wav"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def start_asr_workers():
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        
        # Запускаем один воркер (для ASR обычно достаточно одного на GPU)
        t = threading.Thread(target=_worker_loop, args=(1,), daemon=True)
        t.start()
        _workers_started = True
        logger.info("ASR workers started")
