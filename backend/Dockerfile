FROM python:3.12-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libxml2-dev \
    libxslt-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Зависимости Python (кешируются отдельным слоем)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка ML-модели в образ при сборке.
# Модель paraphrase-multilingual-MiniLM-L12-v2 (~120 MB, ONNX формат).
# Без этого первый запрос к сервису ждал бы ~30 сек скачивания.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
RUN python -c "\
from fastembed import TextEmbedding; \
m = TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'); \
list(m.embed(['warmup'])); \
print('ML model ready')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
