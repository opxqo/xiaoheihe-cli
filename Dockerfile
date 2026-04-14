FROM python:3.12-slim

LABEL maintainer="xiaoheihe-crawler"
LABEL description="小黑盒爬虫API - Cookie持久化 + Playwright"

WORKDIR /app

# Use Aliyun mirror for Debian packages (faster in China)
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true

# Install system dependencies for Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 libx11-xcb1 libx11-6 libxcb1 \
    libfontconfig1 libfreetype6 libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with mirror
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# Install Playwright Chromium (without system deps, we installed them above)
RUN playwright install chromium

# Copy application code
COPY api_server.py .
COPY browser_manager.py .
COPY api_client.py .
COPY data_parser.py .
COPY models.py .

# Create data directory for downloads
RUN mkdir -p /app/data/images

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')" || exit 1

ENV HEADLESS=true
ENV PORT=8010

CMD ["python", "api_server.py"]
