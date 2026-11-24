FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libmupdf-dev \
        mupdf-tools \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY docs ./docs
COPY forms ./forms
COPY experiments ./experiments

RUN mkdir -p /app/output/csharp_pdf_cache

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 2+ workers: C# import-fields calls back into /internal/extract on this same host.
# A single worker blocks on the outbound C# HTTP call and deadlocks the extract callback.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
