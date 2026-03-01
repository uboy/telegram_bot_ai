# Design Spec: ASR Performance Optimization and Faster-Whisper (v1)

## Context
Standard transcription using the `transformers` library is reliable but slow for production use cases, especially when using larger models like Whisper-Large. To provide a better user experience, the system needs to leverage the full power of modern GPUs (like NVIDIA RTX 3090) and more efficient inference engines.

## Goals
- Achieve 4-10x faster transcription speeds.
- Support high-performance engines like `faster-whisper`.
- Enable FP16 (Half Precision) calculations on compatible GPUs.
- Resolve Docker environment compatibility issues with CUDA libraries.

## Proposed Solution

### ASR Engine Abstraction (Strategy Pattern)
The ASR worker logic is refactored to use an abstract `ASREngine` interface. This allows seamless switching between different implementation strategies:
1. **LegacyTransformersEngine:** Uses the standard Hugging Face library. Optimized with `torch_dtype=torch.float16` and `use_safetensors=True`.
2. **FasterWhisperEngine:** Uses the `ctranslate2` backend. Offers significant performance gains and lower VRAM usage via INT8/FP16 quantization.

### Docker & Infrastructure Fixes
To avoid "missing library" errors in `python-slim` images:
- Use `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` pip packages to provide CUDA runtimes.
- Configure `LD_LIBRARY_PATH` in the `Dockerfile` to include paths to these pip-installed libraries.
- Ensure `ffmpeg` is available for high-quality audio pre-processing.

### Optimization Techniques
- **FP16 (Half Precision):** Enabled by default on GPU to utilize Tensor Cores.
- **Memory Management:** Added `torch.cuda.empty_cache()` calls when switching models or engines to prevent VRAM fragmentation.
- **Improved Polling:** Increased bot polling timeout to 10 minutes to accommodate heavy model initializations or long audio files.

## Implementation Details
- **`backend/services/asr_worker.py`**: Implementation of the factory and engine classes.
- **`Dockerfile`**: Added `LD_LIBRARY_PATH` and system deps.
- **`requirements.txt`**: Added `faster-whisper` and NVIDIA runtimes.
- **`frontend/bot_handlers.py`**: Increased retry limits and added HTML escaping for ASR results.

## Acceptance Criteria
- [x] Faster-Whisper engine is available and functional on GPU.
- [x] Transformers engine is optimized with FP16.
- [x] Docker image builds and runs successfully with GPU acceleration.
- [x] Transcription of 1-minute audio is significantly faster (aiming for <5s on RTX 3090).
- [x] Automatic fallback to standard engine if high-performance engine fails.

## Risks
- **Library Version Drift:** Pip-installed NVIDIA packages might mismatch host drivers. (Mitigation: Target CUDA 12 which is broadly compatible).
- **VRAM Contention:** ASR engines share VRAM with RAG models. (Mitigation: Added explicit cache clearing).
