import os
import sys
import time
import torch
import wave
import numpy as np
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.append(os.getcwd())

from backend.services.asr_worker import LegacyTransformersEngine, FasterWhisperEngine
from shared.logging_config import logger

def create_test_wav(path: str, duration_s: float = 1.0):
    """Создает тихий WAV файл для теста."""
    sample_rate = 16000
    num_frames = int(duration_s * sample_rate)
    frames = np.zeros(num_frames, dtype=np.int16)
    
    with wave.open(path, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames.tobytes())

def test_engines():
    model_name = "openai/whisper-tiny"  # Используем tiny для быстрого теста
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    test_file = "test_audio.wav"
    
    logger.info(f"--- ASR Engine Test (Device: {device}) ---")
    create_test_wav(test_file)
    
    try:
        # 1. Тест Transformers
        logger.info("Testing LegacyTransformersEngine...")
        start = time.monotonic()
        t_engine = LegacyTransformersEngine(model_name, device)
        t_engine.transcribe(test_file)
        logger.info(f"✅ Transformers OK (Load + Inference: {time.monotonic() - start:.2f}s)")
        
        # 2. Тест Faster-Whisper
        logger.info("Testing FasterWhisperEngine...")
        start = time.monotonic()
        try:
            f_engine = FasterWhisperEngine(model_name, device)
            f_engine.transcribe(test_file)
            logger.info(f"✅ Faster-Whisper OK (Load + Inference: {time.monotonic() - start:.2f}s)")
        except Exception as e:
            logger.error(f"❌ Faster-Whisper Failed: {e}")
            if "libcublas" in str(e) or "libcudnn" in str(e):
                logger.error("TIP: Library mismatch. Check LD_LIBRARY_PATH in Dockerfile.")
            
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    test_engines()
