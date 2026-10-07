FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Optional JavaScript rendering: docker build --build-arg INSTALL_PLAYWRIGHT=true .
# (adds a Chromium browser, roughly 400 MB; then run with RENDER_JS=true)
ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
      pip install "playwright>=1.44" && playwright install --with-deps chromium \
      && chmod -R a+rX /ms-playwright; \
    fi

COPY product_extractor ./product_extractor
COPY scripts ./scripts
COPY fixtures ./fixtures

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

CMD ["uvicorn", "product_extractor.api:app", "--host", "0.0.0.0", "--port", "8000"]
