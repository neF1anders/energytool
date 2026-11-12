# Ассистент-квалификатор Energytool — спецификация

Статус: v1.0 · 2026-11-12 · M0–M7 готовы, отчёт о решениях — [`decisions.md`](decisions.md). Изменения по ходу разработки помечены **[Mn]**.
Диаграммы: [`docs/diagrams/`](diagrams/README.md) · сущности домена: [`src/energytool/domain/`](../src/energytool/domain/) · порты и use cases: [`src/energytool/application/`](../src/energytool/application/)

---

## 1. Цель и рамки

Чат-ассистент для сайта Energytool. За несколько ходов он понимает задачу клиента, отвечает только по базе знаний, ведёт к подходящим продуктам и по ходу заполняет опросный лист. Менеджер получает заявку вместе с брифом и звонит уже с расчётом.

Два способа использования одного ядра:

| | Этап 1: автосабмит | Этап 2: веб-чат |
|---|---|---|
| Вход | `DATA_DIR/questions.csv` (`id,question`, вопрос или история диалога в ячейке) | сообщения из виджета |
| Выход | `submission.csv` (`id,answer`) | ответы, заявка, бриф |
| Оценка | pairwise LLM-судья против baseline с опорой на golden | эксперты: работоспособность, качество, скорость, архитектура |
| Состояние | нет, собирается из истории в ячейке | Session Store |
| Ограничения | 8 CPU, 16 ГБ, **20 минут на прогон**, ZIP ≤ 128 МБ, в корне ZIP только `.py` + `requirements.txt`, без вложенных папок, данных и индексов; `pip install` из кода запрещён | Docker + compose, README, `.env.example` |

Обязательное поведение (из кейса): факты только из базы знаний; уточняющий вопрос при неясности; честный отказ «вне компетенции → специалист»; контекст в рамках диалога и переход между темами; «Новый диалог» сбрасывает всё; история всех сессий хранится и без заявки; режим отладки (время ответа + использованные документы); ответы ≤ ~150 слов (эталоны в среднем ~38 слов).

Вне рамок: настройка приборов, техдокументация, реальная CRM.

## 2. Ключевые решения

1. **Один конвейер с явным состоянием**, а не оркестратор с доменными LLM-агентами. На ход ≤ 2 вызова LLM: Extractor (факты в JSON) и Responder (текст). Всё остальное делает код.
2. **Модель не управляет потоком.** Что спросить, когда идти в базу, какой продукт ведёт, когда предлагать заявку — решают доменные функции. У LLM нет инструментов.
3. **Слушаем всё, спрашиваем по лидерам.** Extractor видит весь каталог полей, Policy спрашивает только поля активного контракта (общий блок + кандидаты).
4. **Объяснимый скоринг.** Баллы продуктов считаются правилами из разделов базы «Подходит / Не подходит, если». Каждое срабатывание — со ссылкой на раздел. Жёсткие факты пересчитываются с нуля каждый ход.
5. **Ретрив — обычный use case** `RetrieveKnowledge(query: KnowledgeQuery) → KnowledgeResult` в application. Запрос — кортеж сниппетов в терминах домена, пайплайн — список стадий, индексы и модели — за портами. Не знает про диалог (запрос из состояния собирает отдельная функция), поэтому гоняется на eval напрямую и масштабируется заменой адаптеров.
6. **Одно ядро для сабмита и веба.** `solution.py` — второй вход в тот же use case, без FastAPI и SQLite.

## 3. Глоссарий

| Термин | Значение |
|---|---|
| Слот, поле | Пункт анкеты (`SlotSpec`), например `max_power_kw` |
| Контракт | Набор полей. Активный контракт = общий блок + блоки кандидатов |
| Кандидат | Продукт с P ≥ 0.25 (максимум 2). Лидер — P ≥ 0.45 и отрыв ≥ 0.15 |
| Пропускное поле (gating) | Без него нельзя решить, подходит ли продукт (ОРЭМ — мощность) |
| READY | Порог полноты анкеты: бот предлагает оставить контакты |
| Evidence | Фрагмент базы в терминах домена |
| Snippet | Часть `KnowledgeQuery`: текст, область, буст, закрепление, выборка по id, бюджет |
| Chunk | Раздел базы (`##`/`###`) с областью (`Product | Topic`) и типом (`SectionKind`) |

## 4. Слои и раскладка репозитория

Правило зависимостей: `interface → application → domain`; `infrastructure → application (ports), domain`. Домен не делает IO, application делает IO только через порты. Схема: [`layers.mmd`](diagrams/layers.mmd).

```
src/energytool/
  domain/                      # чистые сущности и правила, без зависимостей
    products.py  slots.py  contract.py  extraction.py  state.py
    scoring.py  policy.py  knowledge.py  reply.py  lead.py     # сущности и параметры
    reducer.py  rules.py  scorer.py  dialog_policy.py  brief.py # сервисы
  application/
    ports.py                   # Protocol-интерфейсы к внешнему миру
    retrieve_knowledge.py      # use case: KnowledgeQuery → KnowledgeResult
    build_index.py             # use case: индексация базы на старте
    knowledge_request.py       # state → KnowledgeQuery (чистая функция)
    retrieval_stages.py        # стадии пайплайна + RRF, нормализация текста
    handle_turn.py             # use case: один ход веб-чата
    qualification.py           # общий шаг ②–⑤ для веба и батча [M4]
    transcript.py              # разбор ячейки этапа 1 в историю [M2]
    answer_question.py         # use case: один вопрос этапа 1 (stateless)
    submit_lead.py             # use case: заявка + бриф
    trace.py                   # TurnTrace — DTO телеметрии; сборщик вызовов LLM за ход [M0]
  infrastructure/
    config.py                  # env → Settings
    llm/gateway.py  llm/extractor.py  llm/responder.py  llm/prompts.py
    llm/embedder.py  llm/reranker.py                           # порты Embedder, Reranker
    kb/loader.py  kb/splitter.py  kb/tagmap.py                 # знание о структуре базы Energytool
    index/memory_store.py  index/bm25.py  index/vectors.py  index/factory.py   # in-memory, bm25s, numpy
    storage/database.py  storage/state_codec.py  storage/sqlite_sessions.py  storage/leads.py   # CRM-вебхук внутри leads.py [M5–M6]
    brief_render.py  labels.py                                 # бриф текстом и JSON, подписи вариантов [M6]
    logs.py                    # настройка logging, JSON, маскирование ПДн, контекст хода [M0]
    telemetry.py               # порт Telemetry → лог (turn_trace) [M0]
  interface/
    http/app.py  http/routes.py  http/schemas.py
    batch/run.py               # логика solution.py
    web/static/                # виджет
    cli.py                     # dev-команды: ping [M0]
  bootstrap.py                 # composition root: Settings → адаптеры → use cases
  bootstrap_web.py             # composition root веба: SQLite, HandleTurn, SubmitLead (не попадает в ZIP) [M5]
  __main__.py                  # uv run python -m energytool <команда> [M0]
solution.py                    # 5 строк: from energytool.interface.batch.run import main
tools/build_submission.py      # плоская сборка ZIP (раздел 13)
eval/                          # стенды: retrieval, ответы, анкета (раздел 12)
tests/
docs/
```

## 5. Domain

Сущности и параметры — `src/energytool/domain/`. Сервисы ниже — чистые детерминированные функции, покрываются юнит-тестами без сети.

### 5.1 Contract Registry — `contract.py`

49 полей: общий блок (5), контакты (7), ОРЭМ (18, из них 6 документов), платформа учёта (6), фото (3), потери (3), солнце (7). У поля: тип, единица, варианты, веса по продуктам, `required_for`, `gating_for`, пункт опросного листа, шаблон вопроса и «зачем», раздел базы.

Источники: «Опросный лист ОРЭМ» (пункты 1–16), `00-reference/03-calculation-and-requests`, звонок пресейлу. Документы бот не собирает, а отмечает наличие (`have / no / later`). Из них строится список «что подготовить к звонку».

### 5.2 State Reducer — `reducer.py`

```python
def apply(state: ConversationState, ex: Extraction, registry: ContractRegistry, turn_text: str) -> ConversationState
```
- поле без `quote` или с цитатой, которой нет в тексте клиента (rapidfuzz `partial_ratio ≥ 85`), отбрасывается;
- значение приводится к типу и базовой единице (`МВт → кВт`, «тыс.» → ×1000, «≈ 3,9 млн ₽» в `monthly_consumption_kwh` не пишем: рубли ≠ кВт·ч);
- новое значение заменяет старое, старое уходит в `SlotValue.previous`;
- `declined` → статус `DECLINED`;
- `mentions` → `SoftSignal(source="mention")`;
- `turns_without_progress` сбрасывается, если закрылось хотя бы одно поле активного контракта.


Реализация **[M3]**: `apply(...) → Reduction(state, accepted, rejected[(slot_id, reason)], progressed)` — причины отказа (`unknown_slot`, `no_quote`, `quote_mismatch`, `bad_value`, `unit_mismatch`) идут в `TurnTrace.extraction`. Исходное состояние не мутируется. Совпадение цитаты — **stdlib `difflib`** (скользящее окно длиной с цитату, порог 0.85, нормализация регистра, `ё`, пунктуации), поэтому домен без внешних зависимостей и `rapidfuzz` удалён из ядра. Единицы: `млн`/`тыс`, `МВт`/`ГВт` относительно кВт/кВт·ч, «в год» → /12 для месячного объёма, рубли в кВт·ч → `unit_mismatch`. `turns_without_progress` обнуляется, если закрылось любое неконтактное поле (активный контракт на этом шаге ещё не известен). Номер хода (`state.turn`) увеличивает use case, не reducer.
### 5.3 Product Scorer — `rules.py`, `scorer.py`

```python
def score(state: ConversationState, rules: Sequence[Rule], priors: Mapping[Segment, Mapping[Product, float]], params: ScoringParams) -> ScoreBoard
```
```
score[p] = prior[p | segment]
         + Σ rule.weight   по правилам, сработавшим на текущих слотах
         + Σ λ^(T − t) · soft[p, t]            λ = 0.7
excluded[p] = сработало правило с weight = EXCLUDE
P = softmax(score / T) по неисключённым,  T = 2
```
Жёсткие факты считаются заново каждый ход, поэтому правка клиента («не 1,1 МВт, а 600 кВт») сразу меняет картину. Затухают только мягкие сигналы.

Стартовая таблица правил (веса калибруются на eval):

| id | Условие | Продукт | Вес | Раздел базы |
|---|---|---|---|---|
| `OREM_GATE` | `max_power_kw < 750` | ОРЭМ | исключить | 21-orem › Порог доступа |
| `OREM_POWER` | `max_power_kw ≥ 750` | ОРЭМ | +2 | 21-orem › Порог доступа |
| `OREM_VOLUME` | потребление ≥ 1 млн кВт·ч/мес | ОРЭМ | +1.5 | 21-orem › Экономическая целесообразность |
| `OREM_BORDER` | потребление < 1 млн | ОРЭМ | флаг | там же: «ориентир, а не фильтр» |
| `OREM_ALREADY` | уже на опте | ОРЭМ / платформа | исключить / +1 | 21-orem › Не подходит, если |
| `OREM_SECTOR` | энергоёмкая отрасль, многосменный график | ОРЭМ | +1 | 21-orem › Подходит, если |
| `OREM_RETAIL` | торговля, офис, ИТ | ОРЭМ | −2 | 21-orem › Не подходит, если |
| `PAIN_BILLS` | цель «снизить платёж» | ОРЭМ, СЭС | +1, +0.5 | 00-product-map |
| `SOLAR_SITE` | указана кровля или площадка | СЭС, гибрид | +1.5, +1 | 23-solar › Размещение |
| `SOLAR_DAY` | дневная нагрузка высокая; склад | СЭС | +1.5 / +1 | 23-solar › Лучший профиль |
| `SOLAR_NIGHT` | работает в основном ночью | СЭС | исключить | 23-solar › Не подходит, если |
| `SOLAR_SLATE` | единственная кровля — шифер > 5–10 лет, площадки нет | СЭС | исключить | 23-solar › Не подходит, если |
| `HYBRID_OUTAGE` | отключения, цель «надёжность» | гибрид | +2.5 | 00-product-map |
| `PLATFORM_SPLIT` | учёт по цехам, объектам, арендаторам | платформа | +2.5 | 22-askue › Кому подходит |
| `PLATFORM_MANUAL` | показания вручную / в Excel | платформа | +1 | 22-askue › Что даёт клиенту |
| `LOSSES_GATE` | промпотребитель или сбыт | потери | исключить | 24-losses › Не подходит |
| `LOSSES_GRID` | сетевая компания | потери | +3 | 24-losses › Кому подходит |
| `PHOTO_ORG` | РСО, ТСО, УК; показания от абонентов | фото | +2 | 25-meter-reading › Кому подходит |
| `HOUSING_LOAD` | СНТ / УК, неплательщики, отключение | платформа | +2 | 22-askue › Кому подходит |

Priors по сегменту — из `20-portfolio › Навигация по типу компании` (таблица в `rules.py`).


Реализация **[M3]** — `domain/rules.py` (`RULES`, `PRIORS`), `domain/scorer.py`. К таблице выше добавлены: `PAIN_BILLS_SOLAR` (+0.5 СЭС), `GOAL_METERING` (+1.5), `GOAL_LOSSES` (+2), `SOLAR_SITE_HYBRID` (+1), `SOLAR_NIGHT_HYBRID` (+1 гибриду при вечернем профиле — демо 14), `SOLAR_SLATE_HYBRID` (исключить), `SOLAR_SLATE_UNKNOWN` (флаг «уточнить возраст»), `OREM_ALREADY_PLATFORM` (+1 платформе), `PHOTO_MANUAL` (+0.5: часть приборов без связи — `20-portfolio › Как продукты связаны`), `HOUSING_LOAD`. Каждое правило и его отрицание покрыты тестом (`tests/domain/test_rules_and_scorer.py`). Priors по сегментам — по `20-portfolio › Навигация по типу компании` (например, склад: СЭС +1.2, платформа +0.4; сетевая: потери +1.2, платформа +1.0, фото +0.8). Тексты правил подставляют значения слотов (`мощность 1 100 кВт…`); флаги (`flag=True`) на балл не влияют.
### 5.4 Dialogue Policy — `dialog_policy.py`

```python
def decide(state, ex: Extraction, board: ScoreBoard, registry: ContractRegistry, th: Thresholds, mode: Literal["chat", "batch"]) -> PolicyDecision
```
Автомат: [`dialog-state.mmd`](diagrams/dialog-state.mmd). Пороги — `Thresholds`.

| Переход | Условие |
|---|---|
| DISCOVERY → QUALIFY | лидер: P ≥ 0.45 и отрыв ≥ 0.15, или два кандидата P ≥ 0.25 |
| QUALIFY → DISCOVERY | лидер потерян (смена лидера только при отрыве — гистерезис) |
| QUALIFY → READY | у каждого кандидата закрыты пропускные поля и ≥ 75% обязательных (`DECLINED` = закрыто) |
| любое → CONTACT | `wants_contact`; вопрос вне базы (`below_floor`); раздел «нужен специалист»; 8 ходов без прогресса |
| READY → CONTACT / NO_CONTACT | бот один раз предлагает заявку; отказ → NO_CONTACT |
| NO_CONTACT → CONTACT | повторный призыв не раньше чем через 3 хода |
| CONTACT → SUBMITTED | ФИО, телефон (формат), компания, согласие ПДн |

`need_facts = intent ∈ {question, mixed, out_of_scope}`. Иначе поиска нет, только выборки по id:

| Событие | Что берём |
|---|---|
| сменился лидер | карточка лидера: `not_fits`, `forbidden` |
| READY / NO_CONTACT | `03-calculation › Для <продукта>` |
| вне темы | `02 › Когда требуется специалист` |

Выбор следующего поля:
```
value(slot) = Σ_{p ∈ кандидаты} P(p) · weight(slot, p)
            + gating_bonus        если поле пропускное для кандидата
            + discriminating_bonus если поле различает двух лидеров
            − ∞                   если закрыто, спрошено 2 раза или контактное
DISCOVERY: только common + поля с весом у ≥ 2 продуктов (segment, goal, max_power_kw…)
next_slot = argmax(value); None, если ничего не осталось или dialog ∈ {CONTACT, SUBMITTED}
```
В `mode="batch"` CONTACT не предлагается, а `next_slot` остаётся: эталоны в 2/3 случаев заканчиваются уточняющим вопросом.


Реализация **[M3]**:
- `decide(...)` + отдельная `after_retrieval(decision, below_floor, mode)`: `below_floor` известен только после ретрива (шаг ⑤ после ④), поэтому переход «ответа в базе нет → CONTACT» делается второй функцией; в batch только пометка в `reason`.
- Порядок проверок в чате: SUBMITTED остаётся; CONTACT + контакты полные → SUBMITTED; `wants_contact`/`Intent.CONTACT` → CONTACT; CONTACT держится до заполнения; `OUT_OF_SCOPE` → CONTACT (+ pinned «Когда требуется специалист»); 8 ходов без прогресса → CONTACT; READY после показанного призыва → NO_CONTACT (клиент продолжил без контакта); NO_CONTACT держится. Затем DISCOVERY/QUALIFY/READY по лидеру и порогу полноты; в batch READY не наступает (остаётся QUALIFY).
- Гистерезис: прежний лидер сохраняется, пока он кандидат (P ≥ 0.25) и новый не оторвался на `leader_margin`.
- `contact_complete`: ФИО, компания, телефон (10 цифр, опционально `+7`/`8`), `pd_consent is True`.  Полные контакты переводят в SUBMITTED из любого состояния чата (клиент может прислать всё одним сообщением) **[M4]**. В CONTACT `next_slot` — первое недостающее из `contact_name → phone → company → pd_consent`.
- `next_slot`: `value(slot)` по формуле; пул DISCOVERY — общий блок + поля с весом у ≥ 2 продуктов; общие поля без весов (`segment`, `goal`, `pain`) получают +0.5, чтобы бот сначала понял тип клиента и задачу; без кандидатов берутся два лучших продукта доски.
- `pinned_sections` — id разделов `03-calculation › Какие сведения могут понадобиться › Для <продукта>` (READY/NO_CONTACT) и `02 › Когда требуется специалист` (вне темы); id строятся так же, как в `BuildKnowledgeIndex`.
### 5.5 Параметры правил

Пороги (`Thresholds`), коэффициенты скоринга (`ScoringParams`), priors и таблица правил — бизнес-правила квалификации, поэтому живут в domain как типы со значениями по умолчанию. Это единственный источник правды: у каждого значения комментарий со ссылкой на раздел базы или результат eval.
- Сервисы принимают параметры аргументом и не читают их из окружения.
- Eval перебирает варианты, создавая `Thresholds(...)` / `ScoringParams(...)` напрямую.
- Подобранные значения коммитятся в domain с комментарием (метрика, дата). Переопределения через env нет: сабмит и веб ведут себя одинаково.
- Инфраструктурные параметры (таймауты, `MAX_WORKERS`, бюджет прогона, модели) — в `Settings`, не в domain.

### 5.6 Brief — `brief.py`

Реализация **[M3]**: `build(state, board, registry, contact, created_at)` — время передаётся аргументом (домен без часов). Кандидаты — top-3 неисключённых с текстами положительных правил и флагами; исключённые — с текстом и разделом базы `EXCLUDE`-правила; поля — без контактных, по `opros_item`, затем по порядку реестра; `missing` — обязательные поля кандидатов, не закрытые (`DECLINED` = закрыто); документы — `DOCUMENT`-поля кандидатов, у которых нет статуса `have`. `contact_from_state(state)` собирает `Contact` из контактных слотов.

`build(state, board, registry, contact) -> Brief`. Кандидаты с причинами (тексты правил), исключённые с причинами, поля в порядке пунктов опросного листа, пробелы, документы. Текст и JSON рендерит адаптер. LLM не используется.

## 6. Application

### 6.1 Порты — `application/ports.py`

Описаны кодом. Сигнатуры только на доменных типах.

| Группа | Порты |
|---|---|
| LLM | `FactExtractor.extract(history, state, catalog) → Extraction`, `ReplyWriter.write(plan, history) → Reply` |
| Модели базы | `Embedder.embed(texts)`, `Reranker.rerank(query, docs, top_n)` |
| Индексы | `ChunkStore` (all / get / where(scopes, kinds)), `LexicalIndex.search`, `VectorIndex.search`, `IndexFactory.build(chunks, vectors)` |
| Источник базы | `KnowledgeSource.load() → SourceFile[]`, `SectionSplitter.split`, `SectionTagger.tag → (Scope, SectionKind)` |
| Прочее | `SessionRepo`, `LeadSink`, `Telemetry`, `Clock` |

### 6.2 HandleTurn — один ход веб-чата

Последовательность: [`turn-sequence.mmd`](diagrams/turn-sequence.mmd).

| Шаг | Кто | Вход → выход | LLM |
|---|---|---|---|
| 0 | SessionRepo | session_id → state | — |
| ① | FactExtractor | последние 6 реплик + state + весь каталог → `Extraction` | да |
| ② | reducer.apply | state + extraction → state' | — |
| ③ | scorer.score | state' → `ScoreBoard` | — |
| ④ | dialog_policy.decide | → `PolicyDecision` | — |
| ⑤ | build_query → RetrieveKnowledge | `need_facts` → поиск по сниппетам, иначе только `IdSnippet(pinned)` | эмбеддинг, реранк / нет |
| ⑥ | ReplyWriter | `ReplyPlan` → `Reply`. Нет вопроса клиента → шаблон без LLM | да / нет |
| ⑦ | brief.build + LeadSink | только при переходе в SUBMITTED | — |
| 8 | SessionRepo, Telemetry | state', TurnTrace | — |

Деградация: Extractor упал или таймаут → `Extraction(intent=QUESTION, search_query=последняя реплика)`, состояние не меняется. Responder упал → ответ из лучшего Evidence + шаблон вопроса. Ретрив упал → «уточню у специалиста» + предложение заявки. Ни один ход не возвращает пустой ответ.


Реализация **[M4]**: `application/handle_turn.py` + общий шаг `application/qualification.py` (`Qualifier.step`: reduce → score → decide → build_query → retrieve → after_retrieval → `ReplyPlan` → `_advance`: `asked[next_slot]+1`, `last_cta_turn`, `leader_history`, `dialog`). Номер хода и реплики (клиента и бота) добавляет `HandleTurn`. План: `answer_question` — только при `need_facts` (иначе шаблон без LLM: подтверждение + вопрос анкеты), `acknowledge` — тексты правил, впервые сработавших на этом ходе (до 2), `offer_contact` → фиксированная фраза-призыв добавляется кодом после текста LLM. ⑦ — внедряемый `submit_lead(step) → lead_id` (реализация — M6), его отказ не ломает ход (`lead_failed`). Каждый шаг замеряется в `TurnTrace.timings_ms` (включая `retrieve.<стадия>`). Сквозной тест «хлебозавод: боль → мощность → объём/регион/сбыт → контакты → SUBMITTED» — `tests/application/test_handle_turn.py`; он же нашёл, что полные контакты одним сообщением из READY должны сразу давать SUBMITTED (исправлено в `dialog_policy`).
### 6.3 AnswerQuestion — один вопрос этапа 1

Ячейка `question` → `Message[]` (разбор `Пользователь: … / Ассистент: …`). Extractor вызывается один раз по всей истории, состояние собирается из одного `Extraction`. Дальше шаги ②–⑥ с `mode="batch"`. Без Session Store, Brief, CONTACT.

Бюджет прогона: параллельно по `MAX_WORKERS`; при `elapsed > 15 мин` переключаемся на быстрый путь (без Extractor: поиск по последней реплике + Responder); при `elapsed > 18 мин` — ответ из лучшего Evidence без LLM. Пустых ответов быть не должно.

Реализация M2 (наивный путь) **[M2]** — `application/answer_question.py`:
- `application/transcript.py`: метки `Пользователь/Клиент/User` и `Ассистент/Консультант/Бот/Assistant` без учёта регистра, строка без метки продолжает реплику; без меток — один вопрос.
- Запрос без Extractor — `knowledge_request.query_from_history`: последняя реплика клиента (вес 1.0), предыдущие реплики клиента одной строкой (0.5), последний вопрос ассистента — `role="context"` для реранкера. В M4 его заменяет `build_query`, он остаётся как деградация.
- Режимы `AnswerMode.FULL` / `NO_LLM` (ретрив + шаблон). Ошибка ретрива → пустой Evidence + ответ (`retrieval_failed`), любая ошибка кейса в раннере → запасной ответ. `TurnTrace` на каждый кейс: тайминги, вызовы LLM, Evidence, деградации.
- Бюджет M2: один порог `RUN_BUDGET_SEC` (1080 с) → `NO_LLM`; порог «быстрого пути без Extractor» (15 мин) появится в M4 вместе с Extractor.


Реализация **[M4]**: режимы `AnswerMode.FULL` (Extractor по всей истории → `Qualifier.step(mode="batch")`, `need_facts` в батче принудительно `True`), `FAST` (наивный путь M2), `NO_LLM`. `Deadline`: `FAST_PATH_SEC` (900) → FAST, `RUN_BUDGET_SEC` (1080) → NO_LLM. Отказ Extractor → FAST для этого кейса (`extractor_failed`). Конвейер и вопрос анкеты в батче — настройки `BATCH_PIPELINE` (`naive`|`full`) и `BATCH_ASK`; умолчания выбраны по eval (§12).
### 6.4 SubmitLead

Валидация контактов (телефон по маске, согласие обязательно) → `brief.build` → рендер текста и JSON → `LeadSink.send` → запись в `leads`. Бриф уходит синхронно с заявкой: перезвон быстрый.

## 7. Use cases базы знаний

Ретрив — такой же use case application, как `HandleTurn`. Код: [`application/retrieve_knowledge.py`](../src/energytool/application/retrieve_knowledge.py), [`build_index.py`](../src/energytool/application/build_index.py), [`knowledge_request.py`](../src/energytool/application/knowledge_request.py); типы — [`domain/knowledge.py`](../src/energytool/domain/knowledge.py). Схема — [`retrieval-pipeline.mmd`](diagrams/retrieval-pipeline.mmd).

### 7.1 Устройство

- `RetrieveKnowledge(query: KnowledgeQuery) → KnowledgeResult`. Не знает про диалог, лидеров и анкету — только сниппеты. Поэтому запускается напрямую на размеченных запросах (`eval/retrieval`) без Extractor и Policy.
- Запрос из состояния собирает `knowledge_request.build_query` — отдельная чистая функция. Её тестируют отдельно, пайплайн от неё не зависит.
- Масштабирование: корпус сейчас в памяти (~235 разделов). FAISS/Chroma/внешний сервис = другая реализация `IndexFactory` / `ChunkStore` / `VectorIndex` в infrastructure; use case не меняется.

### 7.2 Запрос — кортеж сниппетов

| Сниппет | Смысл |
|---|---|
| `TextSnippet(text, weight, role)` | текст для поиска; несколько текстов ищутся раздельно и сливаются RRF с весами; `role="context"` — только для реранкера |
| `ScopeSnippet(scopes)` | область: `scope IN (продукты ∪ темы)`; нет сниппета — вся база |
| `BoostSnippet(scope, factor)` | мягкий буст области |
| `PinSnippet(scope, kinds, limit)` | закрепить разделы вне ранжирования (карточка лидера: `forbidden`, `not_fits`) |
| `IdSnippet(ids)` | выборка по id без поиска |
| `BudgetSnippet(top_k, recall_k, fallback_k, max_tokens, min_rerank_score)` | бюджет и порог |

Ответ — `KnowledgeResult(evidence, below_floor, scope_mass, timings_ms, trace)`; `Evidence(chunk, score, origin, stage_scores)`, `origin ∈ ranked | pinned | fallback | by_id`. `scope_mass` — доля реранка по областям, из неё Policy делает `SoftSignal(source="retrieval_mass")`.

### 7.3 Сборка запроса — `build_query`

| Источник | → Сниппет |
|---|---|
| `extraction.search_query` | `TextSnippet(weight=1.0)` |
| последняя реплика клиента | `TextSnippet(weight=0.5)` |
| кандидаты `ScoreBoard` ∪ `{general, energy}` | `ScopeSnippet` (нет кандидатов — без области) |
| лидер | `BoostSnippet(leader, 1.2)` + `PinSnippet(leader, {forbidden, not_fits})` |
| `decision.pinned_sections` | `IdSnippet` |
| `need_facts=False` | только `IdSnippet` — без поиска и вызовов моделей |
| `top_k, fallback_k, max_tokens`, `Thresholds.rerank_floor` | `BudgetSnippet` |


Реализация **[M4]**: `build_query(extraction, state, decision, budget, *, restrict_scope=True)` — бюджет собирает вызывающий (`Qualifier` подставляет `min_rerank_score = Thresholds.rerank_floor`), доска не нужна: кандидаты и лидер уже в решении. Солнечные продукты переводятся в область через `scope_for`. `restrict_scope=False` в батче: у одиночного вопроса нет накопленного контекста, и узкая область чаще вредит, поэтому лидер только бустится и закрепляется. Пустой `search_query` → последняя реплика клиента.
### 7.4 Стадии по умолчанию

`parse → by_id → scope → recall (BM25 ∥ dense по каждому тексту) → fuse (RRF, k=60) → boost → rerank → pin → fallback → pack`. Стадия — чистая функция `(Candidates, Ctx) → Candidates`, конфигурация — кортеж стадий (`PipelineConfig`). Эксперимент = другой кортеж или параметры.

- BM25 (`bm25s`): нижний регистр, `ё → е`, обрезка слов до 6 букв (стеммера в разрешённом списке нет).
- Dense: bge-m3 через `Embedder`, косинус в numpy по разрешённым id.
- Rerank: Qwen3 Reranker 8B через `Reranker`; лучший скор ниже `min_rerank_score` → `below_floor=True`. `reranker=None` → ранжирование по fused (деградация, эксперименты).
- Fallback: recall + fuse + rerank по всей базе без области, `fallback_k=2` — страховка от ошибки роутинга.
- Pack: дедупликация, порядок `by_id → pinned → ranked → fallback`, обрезка по `max_tokens` (оценка токенов `len(text) / 3.5` без tiktoken: ему нужна сеть для загрузки словаря, а в контейнере проверки её может не быть; `Chunk.tokens` считается так же), `scope_mass`. **[M0]**

Реализация **[M1]** — `application/retrieval_stages.py` (стадии, `ParsedQuery`, `Candidates`, `Ctx`, `PipelineConfig`, `CONFIGS`), `application/retrieve_knowledge.py` (use case):
- `parse` — не стадия, а `parse_query()` один раз до стадий: результат лежит в `Ctx.parsed`. Пустые тексты отбрасываются.
- Конфигурации (`RETRIEVAL_CONFIG`): `hybrid_rerank` (по умолчанию), `hybrid`, `bm25_rerank`, `bm25`, `dense_rerank`, `dense`. Флаги `use_reranker` / `use_embedder` в `PipelineConfig` — модели не вызываются вовсе.
- Запрос реранкера = текст с наибольшим весом + `TextSnippet(role="context")` через перевод строки. Документы для реранкера обрезаются до 2000 символов (`infrastructure/llm/reranker.py`): справочные разделы до ~5 тыс. символов иначе раздувают время. Буст применяется и к скору реранкера.
- Fallback: recall + RRF по всей базе, 10 лучших вне области → реранк → берём не больше `fallback_k`, **и только если скор не ниже худшего ranked-раздела**: иначе страховка от ошибки роутинга становится шумом в ответе.
- Деградации пишутся в `KnowledgeResult.trace["notes"]` и в лог: `dense_failed` (эмбеддер упал → только BM25), `rerank_failed` / `no_reranker` (ранжирование по RRF; `below_floor` в этом случае не выставляется), `fallback_added:n`, `budget_cut:<id>`. Ловится только `application.ports.ExternalServiceError` — общий предок ошибок адаптеров (в т.ч. `LlmGatewayError`), ошибки программирования не маскируются.
- `Evidence.stage_scores = {rrf, final}`; `trace` — конфигурация, заметки, top-5 каждого списка и RRF. DEBUG-лог на каждую стадию, INFO-лог `retrieval done` с итоговыми id, origin и скорами.
- BM25: стоп-слова (короткий список в коде), `get_scores` по маске `allowed`, запрос без известных слов → пусто.

### 7.5 Индексация — `BuildKnowledgeIndex`

`load → split → tag → embed → build`, на старте веба и `solution.py`. Знание о структуре базы Energytool — в адаптерах портов:
- `infrastructure/kb/loader.py` (`KnowledgeSource`): `knowledge-base.zip` без распаковки (поддержать `knowledge_base.zip`, `kb.zip`), только `.md` и `.txt`.
- `infrastructure/kb/splitter.py` (`SectionSplitter`): раздел `##` или `###`; текст = `файл › раздел › подраздел` + тело; id = `файл#slug(пути)`.
- `infrastructure/kb/tagmap.py` (`SectionTagger`): ручная карта `файл или раздел → Product | Topic` и правила `заголовок → SectionKind`. Словарь в коде, поэтому проходит в ZIP. Примеры: `12-power-thresholds › Пороги оптового рынка → orem`, `15-microgeneration → solar_grid`, `13-metering → metering`, `«Чего утверждать нельзя» → forbidden`.
- `infrastructure/index/factory.py` (`IndexFactory`): `ChunkStore` + bm25s + матрица numpy.
- Эмбеддинги батчами по 32, кэш по sha1 текста в памяти процесса.

Реализация **[M1]**:
- Нарезка: раздел = заголовок `#`/`##`/`###` и текст до следующего заголовка любого уровня; разделы без тела отбрасываются (их заголовок остаётся в пути детей); вступление под `#` — отдельный раздел (`#intro`); заголовки внутри ```-блоков игнорируются. На базе: 233 раздела, медиана тела 333 символа, максимум ~4.9 тыс.
- Id = `файл#slug(h2)/slug(h3)`, например `20-products/21-orem.md#кому-подходит/порог-доступа-определяется-присоединённой-мощностью`; повтор получает суффикс `-2`. Внешняя папка архива (`knowledge-base/…`) отрезается, поэтому id не зависят от упаковки.
- Loader читает и распакованный каталог (`knowledge-base/`) — для разработки; архив имеет приоритет. Поиск: `DATA_DIR`, `cwd/DATA_DIR`, `cwd`.
- Солнечные разделы помечены `solar_grid`; гибрид ищется там же через `domain.knowledge.scope_for(product)`.
- Справочники 01/02/03 размечены по продуктам на уровне `##`/`###` (`_HEADING_SCOPES`). Типы: префиксы заголовков от глубокого к корню, затем тип файла (`01 → numbers`, `03 → requirements`, `17-faq → faq`).
- Эмбеддер: кэш по `sha1(модель + текст)`, дедупликация в батче; для разработки — дисковый кэш `EMB_CACHE_DIR` (jsonl, в `.gitignore`), на платформе не задаётся. Индексация базы на OpenRouter: 8 батчей, несколько секунд.

### 7.6 Эксперименты

**Стенд [M1]:** `uv run --env-file .env python -m eval.retrieval [--configs …] [--sets faq paraphrase demo]`. Наборы и результаты — в `eval/data/` (производные материалов, в `.gitignore`):
- `faq` — 79 разделов, чей последний заголовок — вопрос (FAQ продуктов, `17-faq`, `###` в `26-general`); лёгкий набор: заголовок входит в текст раздела;
- `paraphrase` — те же 79 вопросов, переписанные Qwen «словами клиента» (`faq_paraphrases.json`, генерируется один раз);
- `demo` — 18 демо-вопросов, разметка руками по фактам эталонов (`demo_labels.json`, поле `fact` — какой факт ищем); диалоги идут через `query_from_history`.

Результаты 2026-11-12 (top_k=10, без fallback; hit@1, recall@4, MRR@10):

| config | faq | paraphrase | demo | p50, мс |
|---|---|---|---|---|
| `bm25` | 0.861 / 0.962 / 0.912 | 0.241 / 0.418 / 0.323 | 0.611 / 0.722 / 0.680 | <1 |
| `dense` | 0.848 / 0.975 / 0.911 | 0.392 / 0.671 / 0.534 | 0.611 / 0.833 / 0.708 | <1 (кэш) |
| `hybrid` | 0.873 / 0.975 / 0.923 | 0.342 / 0.595 / 0.472 | 0.722 / 0.833 / 0.769 | <1 (кэш) |
| `bm25_rerank` | 0.797 / 0.975 / 0.883 | 0.418 / 0.557 / 0.485 | 0.667 / 0.889 / 0.745 | ~600 |
| `dense_rerank` | 0.810 / 0.975 / 0.890 | 0.608 / 0.810 / 0.706 | 0.611 / 0.889 / 0.726 | ~650 |
| **`hybrid_rerank`** | 0.797 / 0.975 / 0.883 | **0.595 / 0.823 / 0.702** | **0.667 / 0.944 / 0.773** | ~700–1300 |

Выводы: реранкер — главный прирост на честных наборах (paraphrase recall@4 0.60 → 0.82); `hybrid_rerank` остаётся по умолчанию. На `faq` реранкер чуть снижает hit@1: в базе есть дубли вопросов (`21-orem` и `26-general` «что если невыгодно»), а метка — только свой раздел. Промахи demo в основном — соседние верные разделы; системный — общие «Следующий шаг» в `10-energy/*` поднимаются на вопросах «что проверить» (кандидат на понижение весом в следующих итерациях).

`eval/retrieval/`: размеченный набор `запрос → ожидаемые разделы`. Источники: FAQ-подзаголовки `20-products/*` (метка = свой раздел и файл), 18 демо-вопросов (разметка руками), синтетические перефразы. Метрики: recall@k, MRR, доля попадания в нужный продукт, время. Конфигурации сравниваются вызовом `RetrieveKnowledge` с разными `PipelineConfig`, без диалога.

## 8. Infrastructure

### 8.1 LLM Gateway — `llm/gateway.py`
httpx-клиент к OpenAI-совместимому прокси: `chat/completions`, `embeddings`, `rerank`. `llm/embedder.py` и `llm/reranker.py` — тонкие реализации портов `Embedder`, `Reranker` поверх gateway. Ретраи на 429/5xx с экспоненциальной задержкой (максимум 3), таймауты (chat 30 с, embed/rerank 10 с), общий семафор по `MAX_WORKERS`, замер времени и токенов в `TurnTrace`. Thinking-режим Qwen выключен, температура 0–0.2.

Реализация **[M0]**:
- повторяются timeout, сетевая ошибка, HTTP 408/409/425/429/5xx; пауза `0.5·2^n` с, `Retry-After` учитывается (не дольше 8 с); `LLM_MAX_RETRIES` (по умолчанию 3). Прочие 4xx (например, 401 `token_not_found_in_db`) не повторяются;
- ошибки: `LlmUnavailableError` (повторы исчерпаны), `LlmRequestError` (4xx, нет адреса/ключа), `LlmResponseError` (ответ не по протоколу) — общий предок `LlmGatewayError`; на них строится деградация (§6.2);
- каждый вызов — `LlmCall(kind, model, ms, tokens_in, tokens_out, retries, ok, error)` в лог и в сборщик хода `application.trace.collecting_llm_calls()` (ContextVar): use case собирает вызовы в `TurnTrace`, не зная про gateway;
- `<think>…</think>` вырезается из ответа; пустой ответ логируется с `finish_reason`.
- Thinking выключается по профилю (`LLM_DISABLE_THINKING=true` по умолчанию): `openrouter` — `reasoning: {enabled: false}` (проверено 2026-11-12: «Да» за 2 выходных токена и ~0.5 с вместо ~70–90 токенов и ~2.5 с; `chat_template_kwargs` и `/no_think` OpenRouter игнорирует); `platform` — `chat_template_kwargs: {enable_thinking: false}` (предположение: прокси на vLLM). Если прокси отвечает 400 на этот параметр, gateway один раз пишет warning и дальше шлёт запросы без него. **[M0]**

### 8.2 Extractor — `llm/extractor.py`
Промпт: роль «извлекатель фактов», каталог полей (`id`, тип, варианты, единица; ~1k токенов), текущее состояние (заполненные поля), последние 6 реплик. Выход — JSON по схеме `Extraction`, сгенерированной из dataclass и реестра (`response_format=json_schema`, если прокси поддерживает; иначе JSON в тексте + валидация + один повтор). Требование: у каждого поля дословная `quote`.


Реализация **[M4]** (`extractor-v1`): каталог строится из реестра — `id | подпись | тип | варианты (id=смысл) / единица`, подписи вариантов в `prompts.CHOICE_LABELS`. `response_format` не используется: JSON вырезается из текста (`{…}`), элементы валидируются поштучно (неизвестный слот, не-скаляр, неизвестный продукт — отбрасываются), битый JSON — один повтор, затем `ExtractionParseError` (наследник `ExternalServiceError`) → деградация use case. Температура 0, `max_tokens=900`. Наблюдение на живом прогоне: модель сама пересчитывает «1,1 МВт» в 1100 и всё равно пишет `unit="МВт"` — поэтому reducer берёт число и множитель из **цитаты клиента**, а уже пересчитанное значение (×10³/×10⁶ от числа цитаты) не умножает повторно (5.2).
### 8.3 Responder — `llm/responder.py`
Шаблон без LLM, если вопроса клиента нет: `acknowledge + ask(+why)`. Иначе LLM: роль консультанта Energytool, «мы», факты только из Evidence, формат «прямой ответ → 1–2 факта → оговорка → один вопрос по плану», 30–70 слов, до 150. Few-shot: 3–4 пары из демо. Пост-проверка кодом: один вопросительный знак максимум в части вопроса, длина, нет чисел, которых нет в Evidence (regex по числам).

Реализация **[M2]** (`llm/responder.py`, `llm/prompts.py`, версия `responder-v1`):
- Few-shot — **2 синтетических примера** (вопрос не по профилю бизнеса и вопрос вне компетенции), а не пары из демо: демо-эталоны — данные хакатона и в код/git не копируются.
- В промпт идут до 4 фрагментов по ≤ 1800 символов (`[i] путь\nтекст`), до 6 предыдущих реплик, вопрос клиента; `max_tokens=450`.
- Пост-обработка: снятие markdown, одна строка, обрезка по последнему целому предложению на 150 словах; числа ответа, которых нет во фрагментах и вопросе, пишутся в лог (`unsupported_numbers`) — пока без перегенерации.
- Шаблон без LLM (`template_reply`): первые два предложения связного текста лучшего фрагмента (без таблиц и списков) + просьба уточнить или оставить заявку; без Evidence — честный отказ со специалистом. Используется при ошибке/пустом ответе LLM и в `NO_LLM`.
- **Thinking**: у Qwen на OpenRouter `chat_template_kwargs` игнорируется — ответ пустой (`finish_reason=length`), всё уходит на рассуждение. Поэтому gateway на старте прогона калибрует параметры (`calibrate_thinking`: пробный запрос на каждый вариант — `chat_template_kwargs`, `reasoning.enabled=false`, без параметров — выбирается первый, давший короткий непустой ответ); если ни один не сработал — без параметров и пол `max_tokens=2048`. Плюс страховка на каждый вызов: пустой ответ с `length` повторяется с лимитом ×4 (≥ 2048).

### 8.4 Хранилище — `storage/`
SQLite через SQLAlchemy Core:
```
sessions(id, created_at, updated_at, dialog_state, state_json)
messages(id, session_id, turn, role, text, created_at)
traces(id, session_id, turn, trace_json)
leads(id, session_id, created_at, brief_text, brief_json, sent_at, status)
```
CRM: `crm_webhook.py` — POST JSON на `CRM_WEBHOOK_URL` или запись в `leads` со статусом `mock_sent`.


Реализация **[M5–M6]** — `infrastructure/storage/`: `database.py` (схема, WAL, внешние ключи; в `sessions` добавлено `replaced_by` — ссылка «Новый диалог» → новая сессия), `state_codec.py` (`ConversationState` ⇄ JSON с версией формата, история правок слотов сохраняется), `sqlite_sessions.py` (`SessionRepo` + `create/replace/exists/messages/traces`; ошибки драйвера → `StorageError(ExternalServiceError)`), `leads.py` (`SqliteLeadSink`: заявка всегда в `leads`, затем вебхук; статусы `mock_sent | sent | send_failed`, ошибка вебхука заявку не теряет). Бриф — `infrastructure/brief_render.py` (`TextBriefRenderer`: текст по порядку разговора менеджера + JSON для CRM; варианты выборов — русскими подписями из `infrastructure/labels.py`, общими с промптом Extractor). `SubmitLead` — `application/submit_lead.py`: `from_step` (⑦ в чате) и `from_form` (форма); после заявки бот отвечает фиксированной благодарностью. Живой прогон хлебозавода (5 ходов, OpenRouter): лидер ОРЭМ с 1-го хода, READY на 3-м, заявка на 5-м, бриф с 4 причинами, исключённым «поиском потерь», 8 полями анкеты с цитатами клиента.
### 8.5 Телеметрия — `telemetry.py`
`TurnTrace` на каждый ход, JSON-строкой в лог и в `traces`:
```
session_id, turn, mode, timings_ms{extract, reduce, score, policy, retrieve{…по стадиям}, respond, total},
llm_calls[{kind, model, tokens_in, tokens_out, ms, retries}],
extraction{intent, slot_updates, rejected_updates, mentions},
dialog{from, to, reason}, scores_before, scores_after, rule_hits[{id, weight, kb_source}],
next_slot, need_facts, evidence[{section_id, origin, score}], below_floor, fallback_used, reply_words
```
На прогон этапа 1: общее время против 20 минут, p50/p95 на вопрос, доля быстрого пути, число пустых ответов (должно быть 0). Телефоны и e-mail в логах маскируются.

Логи — `infrastructure/logs.py` **[M0]**:
- stdlib `logging`, вывод в stderr, `LOG_FORMAT=json` (одна запись — одна JSON-строка) или `text` для локальной отладки, уровень — `LOG_LEVEL`;
- domain не логирует, его решения попадают в `TurnTrace`; application и infrastructure пишут в `logging.getLogger(__name__)`, структурированные поля — через `extra={"data": {...}}`;
- контекст (`session_id`, `turn`, `qid`) задаёт interface через `log_context(...)`, он попадает во все записи блока;
- `log_duration(logger, event)` замеряет шаг и пишет `ms`; при исключении пишет ERROR с traceback;
- фильтр на хендлере маскирует телефоны (`+7***-**-NN`), e-mail (`a***@domain`), `sk-…` и `Bearer …` в сообщениях, полях и traceback — значит, и в логах сторонних библиотек; `httpx`/`httpcore` понижены до WARNING;
- `infrastructure/telemetry.py` (`LogTelemetry`) пишет `TurnTrace` событием `turn_trace`.

## 9. Interface

### 9.1 HTTP — FastAPI
```
POST /api/sessions                         → {session_id}
POST /api/sessions/{id}/messages {text}    → SSE: токены ответа, затем event "meta" {debug}
POST /api/sessions/{id}/reset              → новая сессия («Новый диалог»)
POST /api/sessions/{id}/lead {contact}     → {lead_id}   (то же, что ввод контактов в чате)
GET  /api/sessions/{id}/debug              → TurnTrace[] сессии
GET  /api/leads                            → список заявок с брифами (страница менеджера)
GET  /healthz
```


Реализация **[M5–M6]** — `interface/http/{app,routes,schemas,debug_view}.py`, сборка — `bootstrap_web.py` (отдельно от `bootstrap.py`: тот достижим из `solution.py`, а в ZIP не должно быть SQLAlchemy/FastAPI). Индекс строится и thinking калибруется в lifespan. SSE отдаёт уже готовый ответ кусками по 3 слова (`token`), затем `meta` (`TurnOut`: ответ, состояние, `lead_id`, `DebugInfo`) и `done`; `?stream=false` — тот же `TurnOut` JSON. Дополнительно к таблице выше: `GET /api/sessions/{id}/messages`, `GET /leads` (HTML), `/`, `/widget.js`. Неизвестная сессия → 404, невалидная заявка (телефон, согласие) → 422.
### 9.2 Виджет
Минимум, без дизайна: один HTML + JS, стрим ответа, 4 кнопки-сценария («Растут счета», «Учёт по объектам», «Солнечная станция», «Потери в сети»), «Новый диалог», переключатель отладки (время, документы, правила), форма контакта в чате. Встраивание одним `<script>`.


Реализация **[M5]** — `interface/web/static/widget.js` (без зависимостей, ~250 строк) + `index.html` (демо-страница) + `leads.html` (страница менеджера). Сессия в `localStorage`; SSE читается через `fetch` + `ReadableStream` (EventSource не умеет POST). Отладка под каждым ответом: время по шагам (извлечение/поиск/ответ), переход автомата, лидер, следующий вопрос, топ-3 продукта, сработавшие правила, вызовы LLM, деградации, документы базы с путём раздела и скором. Форма заявки открывается сама в READY/CONTACT.
### 9.3 Batch — `interface/batch/run.py`
Читает `DATA_DIR/questions.csv`, строит индекс, гонит `AnswerQuestion` параллельно, пишет `submission.csv` (`id,answer`, UTF-8, кавычки, без переносов строк, ≤ 10 000 символов, без пустых и дублей). Атомарная запись, код возврата 0.

Реализация **[M2]**: `interface/batch/submission.py` (поиск `questions.csv`/`train.csv` в `DATA_DIR`, `cwd/DATA_DIR`, `cwd`, каталоге скрипта; запись через временный файл и `os.replace`; запасной ответ для пустых), `interface/batch/run.py` (`Deadline`, пул потоков, `log_context(qid=…)`, прогресс в stdout каждые 10 вопросов, итог `batch summary`: время, p50/p95, режимы, число запасных ответов). Даже если всё упало до ответов, CSV пишется с запасными ответами для всех id.

## 10. Конфигурация

Профили окружения (`MODELS_PROFILE`):

| Профиль | Где | Модели |
|---|---|---|
| `platform` | прогон этапа 1, демо | прокси организаторов из env платформы |
| `openrouter` | локальная разработка, тесты и eval | та же генеративная модель (Qwen 27B) через OpenRouter |

`openrouter`: `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_API_KEY` — ключ OpenRouter, `LLM_MODEL` — id той же Qwen 27B в каталоге OpenRouter (проверено: `qwen/qwen3.8-27b` — тот же id, что у платформы) **[M0]**. Эмбеддер и реранкер настраиваются отдельно: `EMB_BASE_URL`, `EMB_API_KEY`, `RERANKER_BASE_URL`, `RERANKER_API_KEY` (по умолчанию — как у LLM). На OpenRouter работают `baai/bge-m3` (размерность 1024) и `qwen/qwen3-reranker-8b` через `/rerank` — локальные модели и группа `local-models` пока не нужны **[M0]**. Если OpenRouter их не отдаёт, `EMB_PROVIDER=local` / `RERANKER_PROVIDER=local` — bge-m3 и Qwen3-Reranker-0.6B на CPU из группы `local-models` (только разработка, в ZIP не попадает). Код ядра от профиля не зависит: меняются только адаптеры в `bootstrap.py`.

Файлы: `.env.example` (все переменные без значений, оба профиля с комментариями), `.env` — локальный, в `.gitignore`. Подключение `.env` — `uv run --env-file .env …`, без `python-dotenv` в ядре. **[M0]**

Переменные платформы (из примера сабмита): `DATA_DIR`, `LLM_BASE_URL` (`BASE_URL`, `OPENAI_BASE_URL`), `LLM_API_KEY` (`OPENAI_API_KEY`), `EMBEDDER_API_KEY`, `RERANKER_API_KEY`, `LLM_MODEL` (`GEN_MODEL`, `OPENAI_MODEL`), `EMB_MODEL`, `RERANKER_MODEL`, `TOP_K`, `RERANKER_TOP_N`, `MAX_TOKENS`, `TEMPERATURE`, `MAX_WORKERS`.
Свои: `SQLITE_PATH`, `CRM_WEBHOOK_URL`, `RETRIEVAL_CONFIG` (имя конфигурации пайплайна), `LOG_LEVEL`, `LOG_FORMAT` (`json` | `text`), `LLM_MAX_RETRIES` (3), `EMB_PROVIDER` / `RERANKER_PROVIDER` (`remote` | `local`), `RUN_BUDGET_SEC` (по умолчанию 1080).
Чтение env — как в примере сабмита: пустое значение или значение, равное имени переменной, считается отсутствующим; числа обрезаются по границам. Ключи в `Settings` скрыты из `repr`, в лог старта идёт `safe_summary()` (только `has_key`). Для `openrouter` `LLM_MODEL` обязателен; для `platform` при пустом env берутся имена моделей из примера сабмита. **[M0]**
Все читаются в `infrastructure/config.py` → `Settings`. Ключей в репозитории нет, `.env.example`.

## 11. Зависимости

Управление — `uv`, описание — `pyproject.toml` + `uv.lock` (оба в git). Python 3.11 (`.python-version`).

| Группа | Состав | Куда идёт |
|---|---|---|
| `[project.dependencies]` | `httpx`, `numpy`, `bm25s` (версии = разрешённый список; tiktoken убран, см. 7.4 **[M0]**; rapidfuzz убран — reducer на `difflib`, см. 5.2 **[M3]**) | ядро, ZIP, Docker |
| extra `web` | `fastapi`, `uvicorn`, `SQLAlchemy`, `pydantic`, `python-dotenv` | Docker |
| group `dev` | `pytest`, `ruff`, `mypy` | локально, CI |
| group `local-models` | `sentence-transformers`, `torch` (CPU) | только профиль `openrouter` с локальными моделями |

Версии ядра совпадают с разрешённым `requirements.txt` организаторов. `requirements.txt` для ZIP — только прямые зависимости ядра с `==` из `pyproject.toml`: транзитивных (`anyio`, `certifi`, `h11`…) нет в разрешённом списке, их ставит pip платформы **[M0]**. `tools/check_requirements.py` сверяет файл с разрешённым списком. Pydantic в ядре не используется: домен на dataclasses.

## 12. Тесты и eval

- `tests/domain/`: reducer (цитаты, единицы, правки), scorer (каждое правило, пересчёт после правки), policy (все переходы, выбор поля), brief.
- `tests/application/`: стадии `RetrieveKnowledge` на игрушечном корпусе с фейковыми индексами и моделями, `build_query`, бюджет.
- `tests/integration/`: HandleTurn и AnswerQuestion с фейковыми LLM и ретривом; сборка ZIP + запуск в чистом каталоге на демо-вопросах с фейковым прокси.
- `tests/live/` (маркер `live`, по умолчанию пропускаются): те же сценарии на профиле `openrouter` — `uv run pytest -m live`. Нужен `.env` с ключом.
- `eval/retrieval/` — раздел 7.6.
- `eval/answers/`: локальный pairwise-судья (Qwen через OpenRouter) против `sample_submission` на 18 демо + синтетике; метрики как у платформы: 0.7·MatchRate + 0.3·WinRate.
  **[M2]** Стенд: `uv run --env-file .env python -m eval.answers --team <submission.csv>`; судья `judge-v1` (промпт наш — платформенный неизвестен, цифры для сравнения версий между собой). Baseline — учебный пример организаторов, прогнанный на демо локально (`eval/data/baseline/`; на OpenRouter ему нужен явный `LLM_BASE_URL` и `reasoning.enabled=false`, иначе пустые ответы). Санити-проверка: baseline против себя = 0.500. Результат M2 (наивный путь, 18 демо): **Score 0.896**, MatchRate 0.875, WinRate 0.944 (17/1/0). Единственный проигрыш — demo 15 («с чего начать»: ответ общий, без связки «платформа + фото»).
  **[M4] M2 против M4 на демо** (18 вопросов, тот же судья; «A vs B» — Score ответа A при B в роли baseline):

  | Сравнение | Score | W/L/T |
  |---|---|---|
  | против учебного baseline: naive (M2) / full+ask / full без ask | 0.896 / 0.906 / 0.884 | 17/1, 17/1, 16/2 |
  | full + вопрос анкеты против naive | 0.446 | 6/9/3 |
  | full без вопроса анкеты против naive | 0.505 | 8/7/3 |
  | full + вопрос против full без вопроса | 0.469 | 6/7/5 |

  Решение: по умолчанию в батче `BATCH_PIPELINE=naive`, `BATCH_ASK=false` — на одиночных вопросах полный конвейер качества не добавляет, навязанный вопрос анкеты слегка вредит, а время вдвое больше (30 с против 13 с на 18 вопросов, 4 потока). Полный конвейер — основной путь веб-чата (анкета, лидер, заявка). Вариант для платформы собирается без правки кода: `tools/build_submission.py --env BATCH_PIPELINE=full`.
- `eval/dialog/`: 20–30 персон с эталонной анкетой (легенда хлебозавода — первая), клиента играет LLM с «не знаю», путаницей терминов и сменой темы. Метрики: полнота и точность полей, ходов до READY, доля ложных полей, верный ли лидер.
  **[M7]** Стенд: `uv run --env-file .env python -m eval.dialog [--personas …] [--turns 8]`; 8 синтетических персон в `eval/dialog/personas.py` (хлебозавод, склад с кровлей, сеть магазинов, сетевая компания, СНТ, водоканал, ферма с отключениями, ИТ-офис), клиента играет Qwen по легенде, бот — настоящий `HandleTurn`. Результат 2026-11-12: полнота анкеты 28/31 (90%), ошибочных полей 0, верный лидер 7/8, заявок 4/8 за 8 ходов. Промах — СНТ без лидера (платформа и фото почти равны) → правило фото для УК/ТСЖ/СНТ выделено в `PHOTO_HOUSING` с весом +1 вместо +2; после правки СНТ: верный лидер, READY на 6-м ходу, заявка на 7-м.

## 13. Сборка сабмита

В ZIP нельзя вложенные папки, поэтому `tools/build_submission.py` раскладывает пакет плоско:
- каждый модуль `energytool/a/b.py` → `energytool__a__b.py` в корне ZIP;
- импорты переписываются регуляркой: `from energytool.a.b import X` → `from energytool__a__b import X`. Поэтому в коде **только абсолютные импорты** `from energytool…`; относительные запрещены линтером (ruff `TID252`);
- в ZIP идут только модули, достижимые из `interface/batch/run.py` (без `http/`, `storage/`, `web/`);
- `solution.py` + `requirements.txt` (подмножество разрешённого списка);
- тест: распаковать в пустой каталог, запустить на демо с фейковым прокси, сверить формат `submission.csv`.

Реализация **[M2]**: `uv run python tools/build_submission.py` → `dist/submission.zip` (в `.gitignore`).
- Достижимые модули ищутся по AST от `solution.py` (в т.ч. `from energytool.a import b`, где `b` — подмодуль); пакет `a/__init__.py` → `energytool__a.py`; относительный импорт — ошибка сборки; после переписывания проверяется, что `from energytool.` не осталось.
- `requirements.txt` = прямые зависимости `[project.dependencies]` (все с `==`), каждая сверяется с `materials/data/requirements.txt`; транзитивных нет — их ставит pip платформы.
- Сейчас: 36 модулей + `solution.py` + `requirements.txt`, ~61 КБ.
- `tests/integration/test_submission_zip.py`: сборка → распаковка в пустой каталог → запуск `python -E -s solution.py` на игрушечной базе с фейковым OpenAI-совместимым прокси (штатный и «прокси отдаёт 500») → проверка формата.
- Ручная проверка «как на платформе»: распаковать ZIP и запустить `uv run --no-project --with-requirements requirements.txt python solution.py` — одноразовое окружение только с зависимостями из ZIP.

Docker: `Dockerfile` (python:3.11-slim, без компиляторов), `compose.yaml` (backend + статика виджета), `.dockerignore` (материалы и база знаний не попадают в образ), команда запуска одна.

Реализация **[M7]**: образ `python:3.11-slim` + `uv` из `ghcr.io/astral-sh/uv:0.13.1`, `uv sync --frozen --no-dev --extra web` (слой зависимостей отдельно от кода), пользователь без root, `HEALTHCHECK` на `/healthz`, `CMD uvicorn … --factory`. `compose.yaml`: один сервис (API + виджет + страница заявок), `env_file: .env` (необязательный), `./data → /app/data:ro` (база знаний монтируется, в образ не входит), том `energytool-var` для SQLite, `EMB_CACHE_DIR` обнулён. `.dockerignore` исключает `materials/`, `data/`, `eval/`, `.env`, тесты и инструменты. Проверено: `docker build` + `docker run` на OpenRouter — health OK, ответ по базе, ошибок в логе нет. Нюанс: локальный umask даёт исходникам права 600 — в образе `chmod -R a+rX /app/src`.

## 14. Правила разработки

Окружение и команды:
- Только `uv`: `uv sync` — установка, `uv add <pkg>` / `uv add --group dev <pkg>` — новая зависимость, `uv run …` — любой запуск. `pip install` и ручная правка `requirements.txt` запрещены.
- Новая зависимость ядра допустима, только если она есть в разрешённом списке организаторов (иначе не попадёт в ZIP).
- Проверка перед коммитом: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` (mypy берёт `src` и `tests` из `pyproject.toml`) **[M0]**.

Код:
- Слои по разделу 4; импорт против направления зависимостей — ошибка ревью. Домен без IO, application — IO только через порты.
- Только абсолютные импорты `from energytool…` (ruff `TID252`), иначе ломается плоская сборка ZIP.
- Сущности и DTO — dataclasses `frozen=True, slots=True`; типы обязательны, `mypy --strict` для всего `src` (в тестах разрешены функции без аннотаций) **[M0]**.
- LLM вызывается только из `infrastructure/llm`; промпты — в `llm/prompts.py`.
- Бизнес-параметры — в domain (раздел 5.5), инфраструктурные — в `Settings`.
- Тексты и комментарии на русском, идентификаторы на английском.

Тесты:
- Юнит-тесты domain и application без сети, с фейковыми портами.
- Каждое правило скоринга и каждый переход автомата покрыты тестом.
- Изменения ретрива и промптов сопровождаются прогоном соответствующего `eval/` и цифрами в описании PR.

Данные и секреты:
- `materials/`, база знаний, `questions.csv`, индексы и `.env` не попадают в git, Docker-образ и ZIP.
- Ключи только из env. В логах телефоны и e-mail маскируются.

Git: ветки `feat/…`, `fix/…`; один PR — один этап раздела 15.

## 15. Этапы

| # | Шаг | Готово, когда |
|---|---|---|
| M0 ✅ | Каркас: `pyproject.toml` + `uv.lock`, слои, `bootstrap.py`, `config.py` с профилями, LLM Gateway, логи, ruff + mypy + pytest | `uv run pytest` зелёный, gateway отвечает на профиле `openrouter` (`uv run --env-file .env python -m energytool ping`) |
| M1 ✅ | База знаний: `BuildKnowledgeIndex`, `RetrieveKnowledge` + стадии, адаптеры kb/index/llm, `eval/retrieval` | recall@4 и MRR на FAQ-наборе посчитаны для 2+ конфигураций |
| M2 ✅ | Наивный путь этапа 1: AnswerQuestion = ретрив + Responder, сборка ZIP | первый сабмит, локальный судья против sample_submission |
| M3 ✅ | Domain-сервисы: reducer, rules, scorer, dialog_policy, brief + тесты | все переходы автомата покрыты |
| M4 ✅ | Extractor + полный HandleTurn, batch на полном конвейере | сравнение M2 vs M4 на eval ответов |
| M5 ✅ | HTTP, SQLite, виджет, режим отладки | путь «вопрос → заявка» проходит руками |
| M6 ✅ | SubmitLead, бриф, мок CRM, страница заявок | бриф хлебозавода совпадает с ожидаемым |
| M7 ✅ | Docker, README, `.env.example`, `eval/dialog` | одна команда поднимает всё |

## 16. Открытые вопросы

- Как прокси платформы выключает thinking и есть ли `response_format=json_schema`. Закрыто страховкой: калибровка на старте прогона + повтор пустого ответа с бо́льшим лимитом (§8.1, §8.3) **[M2]**. Что выбрала калибровка на платформе — смотреть в логе прогона (`thinking calibrated`).
- ~~Есть ли на OpenRouter та же Qwen 27B, bge-m3 и реранкер~~ — есть, все три отвечают **[M0]**.
- Есть ли у прокси лимит RPS — от этого зависит `MAX_WORKERS` и бюджет 20 минут. Наивный путь: ~0.7 с/вопрос при 4 потоках на OpenRouter (18 демо — 13 с); при 429 gateway повторяет с паузой.
- Порог реранка τ (`Thresholds.rerank_floor`) пока `None`: на демо нет вопросов вне базы, подобрать на синтетике «вне компетенции».
- ~~Нужен ли второй Extractor-вызов в батче~~ — в батче по умолчанию наивный путь без Extractor (§12) **[M4]**.
- ~~Оставлять ли `next_slot`-вопрос в ответах этапа 1~~ — нет: на локальном судье слегка хуже (`BATCH_ASK=false`) **[M4]**.
- Насколько локальный судья совпадает с платформенным — проверить по первым посылкам (`submission-*.zip` в `dist/`).
