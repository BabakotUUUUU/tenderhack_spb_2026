FROM python:3.12-slim

WORKDIR /app

# Системные зависимости: lxml + полный набор для Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libxml2-dev \
    libxslt-dev \
    curl \
    # Playwright Chromium runtime deps
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxshmfence1 \
    fonts-liberation \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Python-зависимости (кешируются отдельным слоем)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium для Playwright (парсинг JS-сайтов: Ozon, Яндекс Маркет)
RUN playwright install chromium --with-deps 2>/dev/null || playwright install chromium

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
