FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    # Needed for faster-whisper (ctranslate2) to find CUDA/cuDNN libs from pip packages
    LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:$LD_LIBRARY_PATH

WORKDIR /app

# Системные зависимости для numpy / faiss / tesseract / audio
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        git \
        ffmpeg \
        tesseract-ocr \
        libtesseract-dev \
        poppler-utils \
        libz-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

CMD ["python", "frontend/bot.py"]


