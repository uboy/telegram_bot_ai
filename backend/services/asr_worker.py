from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Optional, List

from datetime import datetime, timezone
import wave

import numpy as np

from shared.logging_config import logger
from shared.database import Session, AppSettings  # type: ignore

from backend.services.asr_queue import (
    AsrJob,
    get_queue_size,
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


def _get_worker_devices(gpu_count: int, max_workers: Optional[int]) -> List[str]:
    if gpu_count <= 0:
        devices = ["cpu"]
    else:
        devices = [f"cuda:{idx}" for idx in range(gpu_count)]

    if max_workers and max_workers > 0:
        return devices[:max_workers]
    return devices


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


def _ensure_ffmpeg_available(audio_path: str) -> None:
    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".wav":
        return
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found in PATH. Install ffmpeg and restart the backend to enable audio decoding."
        )


def _probe_audio_metadata(audio_path: str) -> dict:
    info = {
        "path": audio_path,
        "ext": os.path.splitext(audio_path)[1].lower(),
        "size_bytes": None,
        "format": None,
        "codec": None,
        "sample_rate": None,
        "channels": None,
        "duration_s": None,
        "source": None,
    }
    try:
        info["size_bytes"] = os.path.getsize(audio_path)
    except Exception:
        pass

    if info["ext"] == ".wav":
        try:
            with wave.open(audio_path, "rb") as wav:
                channels = wav.getnchannels()
                sample_rate = wav.getframerate()
                frames = wav.getnframes()
            info.update(
                {
                    "format": "wav",
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "duration_s": (frames / sample_rate) if sample_rate else None,
                    "source": "wave",
                }
            )
            return info
        except Exception:
            pass

    if not shutil.which("ffprobe"):
        return info

    try:
        import subprocess

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                audio_path,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        payload = json.loads(result.stdout or "{}")
        fmt = (payload.get("format") or {}).get("format_name")
        duration = (payload.get("format") or {}).get("duration")
        stream = None
        for s in payload.get("streams") or []:
            if s.get("codec_type") == "audio":
                stream = s
                break
        info.update(
            {
                "format": fmt,
                "codec": stream.get("codec_name") if stream else None,
                "sample_rate": int(stream.get("sample_rate")) if stream and stream.get("sample_rate") else None,
                "channels": int(stream.get("channels")) if stream and stream.get("channels") else None,
                "duration_s": float(duration) if duration else None,
                "source": "ffprobe",
            }
        )
    except Exception:
        pass
    return info


def _log_audio_metadata(audio_path: str, stage: str) -> None:
    info = _probe_audio_metadata(audio_path)
    logger.info(
        "ASR audio %s: ext=%s format=%s codec=%s sample_rate=%s channels=%s duration_s=%s size_bytes=%s source=%s",
        stage,
        info.get("ext"),
        info.get("format"),
        info.get("codec"),
        info.get("sample_rate"),
        info.get("channels"),
        info.get("duration_s"),
        info.get("size_bytes"),
        info.get("source"),
    )


def _convert_audio_if_needed(audio_path: str) -> str:
    if not shutil.which("ffmpeg"):
        return audio_path

    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".wav":
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
        raise RuntimeError(
            "Неподдерживаемый или поврежденный аудиоформат. "
            "Пожалуйста, отправьте голосовое сообщение в формате OGG/Opus или WAV."
        ) from exc


def _transcribe_with_model(
    audio: dict,
    language: Optional[str],
    model_name: str,
    device_index: int,
) -> str:
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ASR dependencies are not available: {exc}") from exc

    cache = getattr(_thread_local, "models", {})
    key = (model_name, device_index)
    if key not in cache:
        processor = AutoProcessor.from_pretrained(model_name)
        model = AutoModelForSpeechSeq2Seq.from_pretrained(model_name)
        device = torch.device("cpu" if device_index < 0 else f"cuda:{device_index}")
        model.to(device)
        model.eval()
        cache[key] = (processor, model, device)
        _thread_local.models = cache
    else:
        processor, model, device = cache[key]

    inputs = processor(
        audio["raw"],
        sampling_rate=audio["sampling_rate"],
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    generate_kwargs = {}
    if language and hasattr(processor, "get_decoder_prompt_ids"):
        try:
            generate_kwargs["forced_decoder_ids"] = processor.get_decoder_prompt_ids(
                language=language,
                task="transcribe",
            )
        except Exception:
            pass

    with torch.no_grad():
        generated_ids = model.generate(**inputs, **generate_kwargs)
    text = processor.batch_decode(generated_ids, skip_special_tokens=True)
    return (text[0] if text else "").strip()


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
    if getattr(asr_pipeline, "type", None) == "seq2seq_whisper":
        feature_extractor = getattr(asr_pipeline, "feature_extractor", None)
        if feature_extractor and hasattr(feature_extractor, "return_attention_mask"):
            if not getattr(feature_extractor, "return_attention_mask", False):
                feature_extractor.return_attention_mask = True

    generate_kwargs = {"language": language} if language else None
    audio = _load_audio(audio_path)
    try:
        if generate_kwargs:
            result = asr_pipeline(
                audio["raw"],
                sampling_rate=audio["sampling_rate"],
                generate_kwargs=generate_kwargs,
                return_timestamps=False,
            )
        else:
            result = asr_pipeline(
                audio["raw"],
                sampling_rate=audio["sampling_rate"],
                return_timestamps=False,
            )
    except KeyError as exc:
        if str(exc) == "'num_frames'" or getattr(exc, "args", [None])[0] == "num_frames":
            logger.warning("ASR pipeline missing num_frames; retrying with file path input")
            try:
                if generate_kwargs:
                    result = asr_pipeline(audio_path, generate_kwargs=generate_kwargs, return_timestamps=False)
                else:
                    result = asr_pipeline(audio_path, return_timestamps=False)
            except KeyError as exc2:
                if str(exc2) == "'num_frames'" or getattr(exc2, "args", [None])[0] == "num_frames":
                    logger.warning("ASR pipeline still missing num_frames; falling back to direct model")
                    return _transcribe_with_model(audio, language, model_name, device_index)
                raise
        else:
            raise
    if isinstance(result, dict) and "text" in result:
        return (result.get("text") or "").strip()
    return str(result).strip()


def _load_audio(audio_path: str) -> dict:
    try:
        with wave.open(audio_path, "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except Exception as exc:
        raise RuntimeError(
            "Неподдерживаемый или поврежденный аудиоформат. "
            "Пожалуйста, отправьте голосовое сообщение в формате OGG/Opus или WAV."
        ) from exc

    if sample_width != 2:
        raise RuntimeError(
            "Неподдерживаемый WAV формат (ожидалась 16-bit PCM). "
            "Пожалуйста, отправьте голосовое сообщение в формате OGG/Opus или WAV."
        )

    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    audio = audio.astype(np.float32) / 32768.0
    return {"raw": audio, "sampling_rate": sample_rate}


_thread_local = threading.local()


def _worker_loop(worker_id: int, worker_device: Optional[str]) -> None:
    logger.info("ASR worker %s started (device=%s)", worker_id, worker_device or "auto")
    while True:
        job = next_job()
        started_at = time.monotonic()
        model_in_use = None
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
            _ensure_ffmpeg_available(job.file_path)
            _log_audio_metadata(job.file_path, "input")
            audio_path = _convert_audio_if_needed(job.file_path)
            if audio_path != job.file_path:
                _log_audio_metadata(audio_path, "converted")
            model_in_use = model_name
            logger.info(
                "ASR job %s started model=%s queue_size=%s",
                job.job_id,
                model_in_use,
                get_queue_size(),
            )
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
                model_in_use = fallback_model
                text = _transcribe_audio(audio_path, job.language, fallback_model, resolved_device)
            if not text:
                raise RuntimeError("Empty transcription result")
            set_job_status(job.job_id, "done", text=text)
            duration = time.monotonic() - started_at
            logger.info(
                "ASR job %s completed in %.2fs model=%s queue_size=%s",
                job.job_id,
                duration,
                model_in_use,
                get_queue_size(),
            )
        except Exception as exc:  # noqa: BLE001
            duration = time.monotonic() - started_at
            logger.error(
                "ASR job %s failed in %.2fs model=%s queue_size=%s: %s",
                job.job_id,
                duration,
                model_in_use,
                get_queue_size(),
                exc,
                exc_info=True,
            )
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
        max_workers_env = os.getenv("ASR_MAX_WORKERS", "").strip()
        max_workers = int(max_workers_env) if max_workers_env.isdigit() else None
        worker_devices = _get_worker_devices(gpu_count, max_workers)

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
