/*
 * Виджет консультанта Energytool. Встраивание одной строкой:
 *   <script src="https://<host>/widget.js" data-api="https://<host>" defer></script>
 * Без зависимостей. Сессия хранится в localStorage; «Новый диалог» создаёт новую.
 */
(function () {
  "use strict";
  const script = document.currentScript;
  const API = (script && script.dataset.api) || "";
  const STORE = "energytool.session";
  const SCENARIOS = [
    ["Растут счета", "Счета за электроэнергию сильно выросли. Как их снизить?"],
    ["Учёт по объектам", "Хотим видеть потребление по каждому объекту и цеху онлайн."],
    ["Солнечная станция", "Думаем о солнечной станции на крыше склада. С чего начать?"],
    ["Потери в сети", "Мы сетевая компания, хотим находить коммерческие потери."],
  ];

  const css = `
  .et-launch{position:fixed;right:24px;bottom:24px;width:60px;height:60px;border-radius:50%;
    background:#e30611;color:#fff;border:0;font-size:26px;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);z-index:99998}
  .et-box{position:fixed;right:24px;bottom:96px;width:390px;max-width:calc(100vw - 32px);height:600px;
    max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25);
    display:none;flex-direction:column;overflow:hidden;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
    color:#1d2023;z-index:99999}
  .et-box.open{display:flex}
  .et-head{background:#1d2023;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:8px}
  .et-head b{flex:1}
  .et-head button{background:transparent;border:1px solid #555;color:#fff;border-radius:8px;padding:4px 8px;cursor:pointer;font-size:12px}
  .et-head button.on{background:#e30611;border-color:#e30611}
  .et-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f6f7f8}
  .et-msg{max-width:85%;padding:8px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
  .et-user{align-self:flex-end;background:#e30611;color:#fff;border-bottom-right-radius:4px}
  .et-bot{align-self:flex-start;background:#fff;border:1px solid #e3e5e8;border-bottom-left-radius:4px}
  .et-debug{align-self:stretch;font:11px/1.4 ui-monospace,Menlo,monospace;background:#fffbe6;border:1px dashed #d9b400;
    border-radius:8px;padding:6px 8px;color:#4a3d00;display:none}
  .et-box.debug .et-debug{display:block}
  .et-debug b{color:#000}
  .et-scen{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-top:1px solid #eee}
  .et-scen button{border:1px solid #e30611;color:#e30611;background:#fff;border-radius:14px;padding:4px 10px;cursor:pointer;font-size:12px}
  .et-form{display:flex;gap:8px;padding:10px 12px;border-top:1px solid #eee}
  .et-form input{flex:1;border:1px solid #ccd;border-radius:10px;padding:8px 10px;font:inherit}
  .et-form button{background:#e30611;color:#fff;border:0;border-radius:10px;padding:0 14px;cursor:pointer}
  .et-contact{padding:10px 12px;border-top:1px solid #eee;display:none;flex-direction:column;gap:6px;background:#fff}
  .et-contact.open{display:flex}
  .et-contact input{border:1px solid #ccd;border-radius:8px;padding:6px 8px;font:inherit}
  .et-contact label{font-size:12px;display:flex;gap:6px;align-items:flex-start}
  .et-contact button{background:#1d2023;color:#fff;border:0;border-radius:8px;padding:8px;cursor:pointer}
  .et-typing{color:#888;font-style:italic}`;

  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const launch = el("button", "et-launch", "💬");
  launch.title = "Консультант Energytool";
  const box = el("div", "et-box");
  box.innerHTML = `
    <div class="et-head"><b>Консультант Energytool</b>
      <button data-act="debug" title="Режим отладки: время, документы, правила">Отладка</button>
      <button data-act="contact" title="Оставить заявку">Заявка</button>
      <button data-act="reset" title="Начать заново">Новый диалог</button></div>
    <div class="et-log"></div>
    <div class="et-scen"></div>
    <form class="et-contact">
      <input name="name" placeholder="Имя и фамилия" required>
      <input name="phone" placeholder="Телефон" required>
      <input name="company" placeholder="Компания" required>
      <label><input type="checkbox" name="pd_consent" required> Согласен на обработку персональных данных</label>
      <button type="submit">Отправить заявку</button>
    </form>
    <form class="et-form"><input placeholder="Опишите задачу…" autocomplete="off"><button>→</button></form>`;
  document.body.append(launch, box);

  const log = box.querySelector(".et-log");
  const input = box.querySelector(".et-form input");
  const scen = box.querySelector(".et-scen");
  const contactForm = box.querySelector(".et-contact");
  let sessionId = localStorage.getItem(STORE);
  let busy = false;

  SCENARIOS.forEach(([label, text]) => {
    const b = el("button", "", label);
    b.type = "button";
    b.onclick = () => send(text);
    scen.appendChild(b);
  });

  launch.onclick = async () => {
    box.classList.toggle("open");
    if (box.classList.contains("open")) {
      await ensureSession();
      if (!log.children.length) greet();
      input.focus();
    }
  };
  box.querySelector("[data-act=debug]").onclick = (e) => {
    box.classList.toggle("debug");
    e.target.classList.toggle("on");
  };
  box.querySelector("[data-act=contact]").onclick = () => contactForm.classList.toggle("open");
  box.querySelector("[data-act=reset]").onclick = reset;
  box.querySelector(".et-form").onsubmit = (e) => {
    e.preventDefault();
    send(input.value);
  };
  contactForm.onsubmit = submitContact;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function greet() {
    add("et-bot", "Здравствуйте! Я консультант Energytool. Расскажите, какую задачу хотите решить: снизить счета, наладить учёт, найти потери или поставить солнечную станцию?");
  }

  function add(cls, text) {
    const node = el("div", "et-msg " + cls, text);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  async function api(path, body) {
    const res = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(res.status + " " + (await res.text()));
    return res;
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const data = await (await api("/api/sessions")).json();
    sessionId = data.session_id;
    localStorage.setItem(STORE, sessionId);
    return sessionId;
  }

  async function reset() {
    if (sessionId) {
      try {
        sessionId = (await (await api(`/api/sessions/${sessionId}/reset`)).json()).session_id;
      } catch (e) {
        sessionId = null;
      }
    }
    if (!sessionId) await ensureSession();
    localStorage.setItem(STORE, sessionId);
    log.innerHTML = "";
    contactForm.classList.remove("open");
    greet();
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true;
    input.value = "";
    add("et-user", text);
    const bot = add("et-bot et-typing", "Печатает…");
    try {
      await ensureSession();
      const res = await api(`/api/sessions/${sessionId}/messages`, { text });
      await readStream(res, bot);
    } catch (e) {
      bot.classList.remove("et-typing");
      bot.textContent = "Не получилось отправить сообщение. Попробуйте ещё раз.";
      console.error("[energytool]", e);
    } finally {
      busy = false;
    }
  }

  async function readStream(res, bot) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let started = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const name = (raw.match(/^event: (.*)$/m) || [])[1];
        const data = JSON.parse((raw.match(/^data: (.*)$/m) || [])[1] || "{}");
        if (name === "token") {
          if (!started) { bot.textContent = ""; bot.classList.remove("et-typing"); started = true; }
          bot.textContent += data.text;
          log.scrollTop = log.scrollHeight;
        } else if (name === "meta") {
          onMeta(data);
        }
      }
    }
  }

  function onMeta(meta) {
    if (meta.dialog === "contact" || meta.dialog === "ready") contactForm.classList.add("open");
    if (meta.lead_id) contactForm.classList.remove("open");
    log.appendChild(debugNode(meta.debug, meta.dialog));
    log.scrollTop = log.scrollHeight;
  }

  function debugNode(d, dialog) {
    const node = el("div", "et-debug");
    const docs = d.documents.map((x) => `• ${x.title} [${x.origin} ${x.score}]`).join("\n") || "—";
    const rules = d.rule_hits.map((r) => `${r.id} (${r.weight})`).join(", ") || "—";
    const top = Object.entries(d.scores).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([p, v]) => `${p} ${(v * 100).toFixed(0)}%`).join(", ");
    const llm = d.llm_calls.map((c) => `${c.kind} ${Math.round(c.ms)}мс`).join(", ");
    const t = d.timings_ms;
    node.textContent =
      `⏱ ${Math.round(d.total_ms)} мс (извлечение ${Math.round(t.extract || 0)}, поиск ${Math.round(t.retrieve || 0)}, ответ ${Math.round(t.respond || 0)})\n` +
      `🧭 ${d.dialog.from || "?"} → ${dialog}; лидер: ${d.dialog.leader || "—"}; следующий вопрос: ${d.next_slot || "—"}\n` +
      `📊 ${top}\n📐 правила: ${rules}\n🤖 ${llm || "без LLM"}` +
      (d.degraded.length ? `\n⚠ деградации: ${d.degraded.join(", ")}` : "") +
      `\n📚 документы:\n${docs}`;
    return node;
  }

  async function submitContact(e) {
    e.preventDefault();
    const f = new FormData(contactForm);
    const body = {
      name: f.get("name"), phone: f.get("phone"), company: f.get("company"),
      pd_consent: f.get("pd_consent") === "on",
    };
    try {
      await ensureSession();
      const data = await (await api(`/api/sessions/${sessionId}/lead`, body)).json();
      add("et-bot", data.reply);
      if (data.lead_id) contactForm.classList.remove("open");
    } catch (err) {
      add("et-bot", "Не удалось отправить заявку. Проверьте телефон и согласие.");
      console.error("[energytool]", err);
    }
  }
})();
