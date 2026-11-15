# Energytool — ИИ-консультант-квалификатор

Чат-ассистент для сайта МТС Energytool. Понимает задачу клиента, отвечает только по базе знаний, подбирает продукты (оптовый рынок, платформа учёта, солнечные станции, поиск потерь, распознавание показаний), по ходу заполняет опросный лист и передаёт менеджеру заявку с готовым брифом.

- Спецификация и решения: [`docs/spec.md`](docs/spec.md), отчёт о решениях: [`docs/decisions.md`](docs/decisions.md)
- Диаграммы (C4, конвейер хода, автомат диалога, ретрив): [`docs/diagrams/`](docs/diagrams/README.md)

## Архитектура в двух словах

Один конвейер с явным состоянием вместо оркестратора LLM-агентов. На ход — не больше двух вызовов LLM:

```
реплика → Extractor (LLM: факты с цитатами) → Reducer → Scorer (правила из базы) → Policy (автомат)
        → RetrieveKnowledge (BM25 ∥ bge-m3 → RRF → Qwen3-Reranker) → Responder (LLM) → ответ
                                                          ↘ при заявке: Brief (код) → CRM
```

Модель не управляет потоком: что спросить, когда искать, какой продукт ведёт и когда предлагать заявку, решают чистые функции домена. Поэтому поведение объяснимо: у каждого балла продукта есть правило со ссылкой на раздел базы, а в режиме отладки видно время, документы, правила и решение автомата.

Clean Architecture: `domain` (сущности и правила, без зависимостей) ← `application` (use cases, порты) ← `infrastructure` (LLM, индекс, SQLite) / `interface` (HTTP, batch, виджет). Сборка — `bootstrap.py` (ядро) и `bootstrap_web.py` (веб).

## Быстрый старт: Docker (одна команда)

```bash
cp .env.example .env            # вписать LLM_API_KEY (OpenRouter); LLM_MODEL=qwen/qwen3.8-27b
mkdir -p data && cp /путь/к/knowledge-base.zip data/
docker compose up --build       # http://localhost:8000
```

- `http://localhost:8000/` — консоль: чат-консультант (кнопка «Чат-бот» справа внизу), форма заявки, панель «Мониторинг» с трассами и вкладкой «Заявки» (бриф, переписка, скачивание `.txt`);
- `http://localhost:8000/widget-demo` — демо-страница встраиваемого виджета `/widget.js`;
- `http://localhost:8000/leads` — заявки с брифами для менеджера;
- `http://localhost:8000/docs` — OpenAPI.

База знаний и данные в образ не попадают, они монтируются томом `./data`. История сессий хранится в томе `energytool-var` (SQLite), там же лежат файлы заявок `leads/lead-<id>.txt`.

### Порядок проверки основного сценария

1. Откройте `/`, нажмите «Начать консультацию» и откройте «Мониторинг» слева сверху.
2. Напишите: «У нас хлебозавод, счета за свет выросли на треть». Бот ответит по базе и спросит мощность. В мониторинге видны время по шагам, документы базы, переход автомата и лидер (оптовый рынок).
3. «Мощность 1,1 МВт, работаем в три смены» → подтверждение по правилу «≥ 750 кВт» и следующий вопрос анкеты.
4. «1,2 млн кВт·ч в месяц, Тульская область, Тулаэнергосбыт» → бот предлагает оставить контакты (состояние READY).
5. Оставьте имя, телефон и компанию в чате или в форме «Заявка» (она открывается сама в READY) → благодарность и карточка заявки. «Открыть заявку» показывает бриф и переписку: кандидаты с причинами, исключённые продукты, анкета по пунктам опросного листа, что уточнить и что подготовить. «Скачать .txt» отдаёт тот же бриф файлом, копия лежит в `LEADS_DIR`.
6. «Новый диалог» начинает с чистого листа, старая сессия остаётся в базе.

## Встраивание в сайт

```html
<script src="https://<host>/widget.js" data-api="https://<host>" defer></script>
```

Заявки уходят POST JSON на `CRM_WEBHOOK_URL` (бриф текстом и структурой). Без него заявки сохраняются в таблицу `leads` со статусом `mock_sent` и видны на `/leads` и во вкладке «Заявки». Каждая заявка дополнительно пишется файлом `LEADS_DIR/lead-<id>.txt` (по умолчанию `var/leads`).

## HTTP API

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/sessions` | новая сессия → `{session_id}` |
| POST | `/api/sessions/{id}/messages` `{text}` | ход диалога; SSE: `token`…, `meta` (ответ + отладка), `done`; `?stream=false` — JSON |
| POST | `/api/sessions/{id}/reset` | «Новый диалог» → `{session_id}` новой сессии |
| POST | `/api/sessions/{id}/lead` `{name, phone, company, pd_consent}` | заявка из формы |
| GET | `/api/sessions/{id}/messages`, `/debug` | история, трассы ходов |
| GET | `/api/leads`, `/leads` | заявки (JSON, страница) |
| GET | `/api/leads/{id}`, `/api/leads/{id}.txt` | одна заявка (JSON) и её бриф файлом `.txt` |
| GET | `/api/admin/traces?limit=50&since=…` | лента мониторинга: трассы всех сессий + сводка (запросы, среднее и последнее время, вызовы моделей) |
| GET | `/healthz` | число разделов базы, готовность dense-поиска, модель, конфигурация поиска |

## Этап 1: сабмит на платформу

```bash
uv run python tools/build_submission.py                              # dist/submission.zip
uv run python tools/build_submission.py --env BATCH_PIPELINE=full    # вариант с полным конвейером
```

ZIP плоский (модули `energytool__a__b.py` + `solution.py` + `requirements.txt` из разрешённого списка) и без данных. Проверка «как на платформе»:

```bash
mkdir -p /tmp/run/data && unzip dist/submission.zip -d /tmp/run && cp data/knowledge-base.zip questions.csv /tmp/run/data/
cd /tmp/run && uv run --no-project --with-requirements requirements.txt python solution.py   # → submission.csv
```

В батче три режима по времени: полный → без Extractor (после `FAST_PATH_SEC`) → без LLM (после `RUN_BUDGET_SEC`). Пустых ответов не бывает: при любой ошибке остаётся шаблон из лучшего фрагмента или честный отказ.

## Переменные окружения

Полный список с комментариями — [`.env.example`](.env.example). Главные:

| Переменная | Назначение |
|---|---|
| `MODELS_PROFILE` | `openrouter` (разработка) или `platform` (прокси организаторов) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenAI-совместимый эндпоинт. OpenRouter: ключ с openrouter.ai, модель `qwen/qwen3.8-27b` |
| `EMB_MODEL`, `RERANKER_MODEL` | `baai/bge-m3`, `qwen/qwen3-reranker-8b` (тот же ключ и адрес) |
| `DATA_DIR` | каталог с `knowledge-base.zip` (и `questions.csv` для этапа 1) |
| `SQLITE_PATH`, `CRM_WEBHOOK_URL` | хранилище сессий и заявок, вебхук CRM |
| `RETRIEVAL_CONFIG` | конфигурация поиска (по умолчанию `hybrid_rerank`) |
| `LOG_LEVEL`, `LOG_FORMAT` | логи в stderr, `json` или `text`; телефоны, e-mail и ключи маскируются |

Внешние сервисы: только OpenAI-совместимый API (OpenRouter локально, прокси организаторов на платформе). Ключ передаётся через `LLM_API_KEY`, в коде и логах ключей нет.

## Разработка

Нужен [uv](https://docs.astral.sh/uv/), Python 3.11.

```bash
uv sync --extra web
uv run --env-file .env python -m energytool ping                      # проверить chat, embeddings, rerank
uv run --env-file .env uvicorn energytool.interface.http.app:create_app --factory --reload
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run --env-file .env pytest -m live                                 # живые тесты моделей
```

Стенды качества (данные и результаты в `eval/data/`, локально, не в git):

```bash
uv run --env-file .env python -m eval.retrieval      # recall@4 / MRR для конфигураций поиска
uv run --env-file .env python -m eval.answers --team <submission.csv>   # pairwise-судья как у платформы
uv run --env-file .env python -m eval.dialog         # диалоги с LLM-клиентом: анкета, лидер, заявка
```

## Зависимости

Ядро (оно же в ZIP): `httpx`, `numpy`, `bm25s`. Веб (extra `web`): `fastapi`, `uvicorn`, `SQLAlchemy`, `pydantic`. Все версии зафиксированы в `pyproject.toml`/`uv.lock` и совпадают с разрешённым списком организаторов.

## Известные ограничения

- Ход веб-чата занимает 3–10 с (Extractor с каталогом из 49 полей ~4 с, ответ ~3–4 с, поиск ~1–2 с). Ответ отдаётся по SSE кусками, но целиком после генерации: gateway пока не стримит токены.
- Параметр отключения thinking на прокси платформы неизвестен заранее. Gateway калибрует его на старте, а если отключить не удалось, поднимает лимит токенов (медленнее, но ответы не пустые).
- Веса правил и priors стартовые, откалиброваны на 8 синтетических персонах и 18 демо-вопросах, а не на реальных диалогах.
- Локальный судья использует собственный промпт: его цифры годятся для сравнения версий между собой, но не предсказывают лидерборд.
- CRM — мок или вебхук; авторизации на `/leads` нет (демо).
