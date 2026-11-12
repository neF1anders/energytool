/*
 * Structurizr DSL (опционально). Источник правды — *.mmd и docs/spec.md.
 * Просмотр: docker run -it --rm -p 8080:8080 -v $(pwd):/usr/local/structurizr structurizr/lite
 */
workspace "Energytool qualifier" "Ассистент-квалификатор Energytool" {

  model {
    client = person "Клиент" "Посетитель сайта Energytool"
    manager = person "Менеджер пресейла" "Получает заявку и бриф"

    models = softwareSystem "Прокси моделей" "Qwen 27B, bge-m3, Qwen3 Reranker 8B" "External"
    crm = softwareSystem "CRM (мок)" "Приём заявки и брифа" "External"
    platform = softwareSystem "Платформа хакатона" "Запускает solution.py, LLM-судья" "External"

    assistant = softwareSystem "Ассистент-квалификатор" {
      widget = container "Веб-виджет" "Чат, «Новый диалог», режим отладки" "HTML + JS, SSE"
      batch = container "solution.py" "Батч этапа 1: questions.csv → submission.csv" "Python, плоский ZIP"
      db = container "Session Store" "sessions, messages, traces, leads" "SQLite" "Store"

      backend = container "Backend API" "Ядро квалификатора" "Python 3.11, FastAPI" {
        chatapi = component "Chat API" "Сессии, SSE, /leads, /debug" "interface · FastAPI"
        controller = component "Turn Controller" "Конвейер хода ①–⑦" "application · use case"
        extractor = component "Extractor" "Текст → JSON с цитатами" "infrastructure · LLM #1" "LLM"
        reducer = component "State Reducer" "Цитаты, единицы, правки" "domain"
        registry = component "Contract Registry" "49 полей, 6 блоков" "domain · код" "Store"
        scorer = component "Product Scorer" "prior + правила + затухание → softmax" "domain"
        rules = component "Rule Table" "~30 правил со ссылкой на базу" "domain · код" "Store"
        policy = component "Dialogue Policy" "Автомат, next_slot, READY" "domain"
        kreq = component "Knowledge Request" "state → KnowledgeQuery (кортеж сниппетов)" "application · функция"
        retrieval = component "Retrieve Knowledge" "KnowledgeQuery → KnowledgeResult" "application · use case"
        buildidx = component "Build Knowledge Index" "load → split → tag → embed, на старте" "application · use case"
        corpus = component "Разделы + индексы" "~235 разделов, bm25, векторы" "infrastructure · в памяти" "Store"
        responder = component "Responder" "Ответ ~40 слов + 1 вопрос" "infrastructure · LLM #2 / шаблон" "LLM"
        brief = component "Brief Renderer" "Текст + JSON из состояния" "domain + шаблон"
        gateway = component "LLM Gateway" "Ретраи, таймауты, семафор" "infrastructure · httpx"
        telemetry = component "Debug & Telemetry" "TurnTrace" "infrastructure"
      }
    }

    client -> widget "Пишет в чат"
    widget -> chatapi "POST /messages" "HTTPS, SSE"
    platform -> batch "Запускает"
    batch -> controller "handle_turn(history), stateless"
    chatapi -> controller "turn"
    controller -> db "load / save state"
    controller -> extractor "① извлечь факты"
    controller -> reducer "② применить"
    controller -> scorer "③ пересчитать"
    controller -> policy "④ решить"
    controller -> kreq "⑤ собрать запрос"
    controller -> retrieval "⑤ найти факты"
    controller -> responder "⑥ ответить"
    controller -> brief "⑦ при SUBMITTED"
    controller -> telemetry "трасса"
    registry -> reducer "схема полей"
    registry -> policy "веса, шаблоны"
    rules -> scorer "правила"
    buildidx -> corpus "строит"
    buildidx -> gateway "эмбеддинги"
    retrieval -> corpus "поиск"
    retrieval -> gateway "embed, rerank"
    extractor -> gateway "chat"
    responder -> gateway "chat"
    gateway -> models "HTTPS"
    brief -> crm "webhook JSON"
    crm -> manager "Показывает заявку"
  }

  views {
    systemContext assistant "context" {
      include *
      autolayout lr
    }
    container assistant "containers" {
      include *
      autolayout lr
    }
    component backend "components" {
      include *
      autolayout tb
    }
    styles {
      element "Person" { shape Person; background #fde8e9; color #1b1f27 }
      element "Component" { background #e9effc; color #1b1f27 }
      element "Container" { background #e9effc; color #1b1f27 }
      element "LLM" { background #fbf0dc; color #1b1f27 }
      element "Store" { shape Cylinder; background #efeafc; color #1b1f27 }
      element "External" { background #eceef2; color #1b1f27 }
    }
  }
}
