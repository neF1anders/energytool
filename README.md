# Energytool — ИИ-консультант-квалификатор

Чат-ассистент для сайта Energytool: отвечает по базе знаний, подбирает продукты, по ходу заполняет опросный лист и передаёт менеджеру заявку с брифом.

- Спецификация: [`docs/spec.md`](docs/spec.md)
- Диаграммы: [`docs/diagrams/`](docs/diagrams/README.md)

> Полный README (Docker, сценарий проверки, ограничения) — этап M7.

## Разработка

Нужен [uv](https://docs.astral.sh/uv/), Python 3.11.

```bash
uv sync                                   # окружение по uv.lock
cp .env.example .env                      # заполнить ключ и модель OpenRouter
uv run --env-file .env python -m energytool ping   # проверить chat, embeddings, rerank
```

Проверки перед коммитом:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run --env-file .env pytest -m live     # живые тесты, нужен .env
```

Логи пишутся в stderr: `LOG_FORMAT=json` (по умолчанию) или `text`, уровень `LOG_LEVEL`. Телефоны, e-mail и ключи маскируются.
