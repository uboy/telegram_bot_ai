from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Optional

from datetime import datetime, timezone

from shared.logging_config import logger
from shared.database import Session, AppSettings  # type: ignore

from backend.services.asr_queue import (
    AsrJob,
    mark_done,
    next_job,
    set_job_status,
)

_workers_started = False
_workers_lock = threading.Lock()


def _detect_gpu_count() -> int:
    try:
        import torch
    except Exception:
        return 0
    try:
        if torch.cuda.is_available():
            return int(torch.cuda.device_count() or 0)
    except Exception:
        return 0
    return 0


def _get_settings() -> tuple[str, str, Optional[str]]:
    session = Session()
    try:
        settings = session.query(AppSettings).first()
        if not settings:
            return "transformers", "openai/whisper-small", None
        provider = settings.asr_provider or "transformers"
        model = settings.asr_model_name or "openai/whisper-small"
        device = settings.asr_device or None
        return provider, model, device
    finally:
        session.close()


def _resolve_device(worker_device: Optional[str], settings_device: Optional[str]) -> Optional[str]:
    if settings_device:
        return settings_device
    return worker_device


def _convert_audio_if_needed(audio_path: str) -> str:
    if not shutil.which("ffmpeg"):
        return audio_path

    ext = os.path.splitext(audio_path)[1].lower()
    if ext in (".wav", ".flac"):
        return audio_path

    output_path = f"{audio_path}.wav"
    try:
        import subprocess
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                audio_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                output_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return output_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASR: ffmpeg conversion failed: %s", exc)
        return audio_path


def _transcribe_audio(
    audio_path: str,
    language: Optional[str],
    model_name: str,
    device: Optional[str],
) -> str:
    try:
        import torch
        from transformers import pipeline
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ASR dependencies are not available: {exc}") from exc

    resolved_device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    if resolved_device.startswith("cuda") and torch.cuda.is_available():
        if ":" in resolved_device:
            device_index = int(resolved_device.split(":", 1)[1])
        else:
            device_index = 0
    else:
        device_index = -1

    cache = getattr(_thread_local, "pipelines", {})
    key = (model_name, device_index)
    if key not in cache:
        cache[key] = pipeline(
            task="automatic-speech-recognition",
            model=model_name,
            device=device_index,
        )
        _thread_local.pipelines = cache
    asr_pipeline = cache[key]

    generate_kwargs = {"language": language} if language else None
    if generate_kwargs:
        result = asr_pipeline(audio_path, generate_kwargs=generate_kwargs)
    else:
        result = asr_pipeline(audio_path)
    if isinstance(result, dict) and "text" in result:
        return (result.get("text") or "").strip()
    return str(result).strip()


_thread_local = threading.local()


def _worker_loop(worker_id: int, worker_device: Optional[str]) -> None:
    logger.info("ASR worker %s started (device=%s)", worker_id, worker_device or "auto")
    while True:
        job = next_job()
        started_at = time.monotonic()
        try:
            set_job_status(job.job_id, "processing", queue_position=None)
            try:
                wait_seconds = max(0.0, (datetime.now(timezone.utc) - job.created_at).total_seconds())
                logger.info("ASR job %s dequeued after %.2fs", job.job_id, wait_seconds)
            except Exception:
                pass
            provider, model_name, settings_device = _get_settings()
            if provider != "transformers":
                logger.warning("ASR provider %s not supported, falling back to transformers", provider)
                provider = "transformers"

            resolved_device = _resolve_device(worker_device, settings_device)
            audio_path = _convert_audio_if_needed(job.file_path)
            try:
                text = _transcribe_audio(audio_path, job.language, model_name, resolved_device)
            except Exception as exc:  # noqa: BLE001
                fallback_model = "openai/whisper-small"
                logger.warning(
                    "ASR model %s failed (%s). Falling back to %s.",
                    model_name,
                    exc,
                    fallback_model,
                )
                text = _transcribe_audio(audio_path, job.language, fallback_model, resolved_device)
            if not text:
                raise RuntimeError("Empty transcription result")
            set_job_status(job.job_id, "done", text=text)
            duration = time.monotonic() - started_at
            logger.info("ASR job %s completed in %.2fs", job.job_id, duration)
        except Exception as exc:  # noqa: BLE001
            logger.error("ASR job %s failed: %s", job.job_id, exc, exc_info=True)
            set_job_status(job.job_id, "error", error=str(exc))
        finally:
            for path in {job.file_path, f"{job.file_path}.wav"}:
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            mark_done(job)


def start_asr_workers() -> None:
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return

        gpu_count = _detect_gpu_count()
        if gpu_count <= 0:
            worker_devices = ["cpu"]
        else:
            worker_devices = [f"cuda:{idx}" for idx in range(gpu_count)]

        for idx, device in enumerate(worker_devices, start=1):
            thread = threading.Thread(
                target=_worker_loop,
                args=(idx, device),
                daemon=True,
                name=f"asr-worker-{idx}",
            )
            thread.start()

        _workers_started = True
        logger.info("ASR workers started: %s", len(worker_devices))
