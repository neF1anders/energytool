# Веб-сервис консультанта: API + виджет + страница заявок.
# База знаний и данные в образ не попадают — монтируются томом в /app/data.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    SQLITE_PATH=/app/var/energytool.sqlite \
    LOG_FORMAT=json

WORKDIR /app

# зависимости отдельным слоем: меняются реже кода
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra web --no-install-project

COPY src ./src
RUN chmod -R a+rX /app/src
RUN uv sync --frozen --no-dev --extra web

RUN useradd --create-home --uid 10001 app && mkdir -p /app/data /app/var && chown -R app /app/var
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["/app/.venv/bin/uvicorn", "energytool.interface.http.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
