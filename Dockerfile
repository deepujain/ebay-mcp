FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EBAY_HOST=0.0.0.0 \
    EBAY_PORT=8000 \
    EBAY_WATCHLIST_PATH=/data/watchlist.json

RUN useradd -m -u 10001 appuser && mkdir -p /data && chown appuser:appuser /data

WORKDIR /app

# Install the package (needs pyproject + README + src for the setuptools build)
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "ebay_mcp.server"]
