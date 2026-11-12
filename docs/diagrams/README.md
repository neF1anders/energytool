# Диаграммы

Источник правды — `*.mmd` (Mermaid) и [`../spec.md`](../spec.md). `rendered/` — SVG для просмотра без Mermaid, пересобираются из `.mmd`.

| Файл | Что показывает |
|---|---|
| [`c4-context.mmd`](c4-context.mmd) | C4 L1: система, клиент, менеджер, платформа, модели, CRM |
| [`c4-containers.mmd`](c4-containers.mmd) | C4 L2–3: контейнеры и компоненты бэкенда, как на макете. Ретрив — use case Retrieve Knowledge в application |
| [`c4-containers.mockup.png`](c4-containers.mockup.png), [`.html`](c4-containers.mockup.html) | Утверждённый макет с ручной раскладкой и разбором (ретрив, скоринг, петля) |
| [`layers.mmd`](layers.mmd) | Чистые слои и направление зависимостей |
| [`turn-sequence.mmd`](turn-sequence.mmd) | Один ход диалога, шаги ①–⑦ |
| [`dialog-state.mmd`](dialog-state.mmd) | Конечный автомат Dialogue Policy и пороги |
| [`retrieval-pipeline.mmd`](retrieval-pipeline.mmd) | Use cases базы знаний: BuildKnowledgeIndex и стадии RetrieveKnowledge |
| [`user-journey.mmd`](user-journey.mmd), [`user-journey.mockup.html`](user-journey.mockup.html) | Путь клиента: сайт → чат → анкета → бриф |
| [`workspace.dsl`](workspace.dsl) | Structurizr DSL, опционально: контекст, контейнеры, компоненты |

## Обозначения

| Цвет | Значение |
|---|---|
| синий | компонент-код, детерминированный |
| синий с толстой рамкой | Turn Controller, точка управления ходом |
| оранжевый | компонент вызывает LLM (Extractor, Responder) |
| фиолетовый, цилиндр | данные: хранилища, реестр полей, таблица правил, корпус |
| серый | внешнее: модели, CRM, платформа, виджет |
| красный | люди |
| ①–⑦ | порядок шагов одного хода |

Пересборка SVG: `npx -y @mermaid-js/mermaid-cli@11.4.0 -i X.mmd -o rendered/X.svg -b white`.
