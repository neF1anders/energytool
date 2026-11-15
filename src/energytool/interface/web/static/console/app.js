// Консоль Energytool: чат-консультант, форма заявки, просмотр заявок и мониторинг трасс.
// Контракт API — src/energytool/interface/http/routes.py.
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const SESSION_KEY = 'energytool_console_session';
const CLEARED_KEY = 'energytool_console_cleared_at';
const LEAD_STATES = new Set(['ready', 'contact']);
const DIALOG_LABELS = {
  open: 'онлайн', discovery: 'выясняем задачу', qualify: 'подбираем решение', ready: 'готов передать менеджеру',
  contact: 'ждём контакты', no_contact: 'без заявки', submitted: 'заявка отправлена',
};
const GREETING = 'Здравствуйте! Я помогу разобраться в энергопотреблении и подобрать решение Energytool. С чего начнём?';
const SCENARIOS = [
  ['Почему вырос счёт?', 'Почему у нас мог вырасти счёт за электроэнергию?'],
  ['Учёт по объектам', 'Можно ли видеть расход отдельно по цехам и объектам?'],
  ['Солнечная станция', 'Подойдёт ли нам солнечная станция?'],
  ['Найти потери', 'Как найти коммерческие потери электроэнергии?'],
];

const chatWidget = $('#chatWidget');
const chatFab = $('#chatFab');
const messages = $('#chatMessages');
const form = $('#chatForm');
const input = $('#chatText');
const leadForm = $('#leadForm');
let sessionId = storageGet(SESSION_KEY);
let dialogState = 'open';
let adminTimer = null;
let activeTab = 'traces';

// --- хранилище и запросы ---

function storageGet(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch { /* приватный режим */ } }

class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function api(url, {method = 'GET', body} = {}) {
  const response = await fetch(url, {
    method,
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, formatDetail(data.detail) || `HTTP ${response.status}`);
  return data;
}

function formatDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  // ошибки валидации pydantic: [{loc, msg}]
  return detail.map((d) => `${(d.loc || []).slice(-1)[0] || ''}: ${d.msg}`).join('; ');
}

async function newSession() {
  const data = await api('/api/sessions', {method: 'POST'});
  setSession(data.session_id);
  return sessionId;
}

function setSession(id) { sessionId = id; storageSet(SESSION_KEY, id); }

// Сессия из localStorage могла исчезнуть вместе с базой: 404 → новая сессия и повтор
async function withSession(call) {
  if (!sessionId) await newSession();
  try { return await call(sessionId); }
  catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
    await newSession();
    return call(sessionId);
  }
}

// --- чат ---

function openChat(text = '') {
  chatWidget.classList.add('open');
  chatWidget.setAttribute('aria-hidden', 'false');
  chatFab.classList.add('hidden');
  chatFab.setAttribute('aria-expanded', 'true');
  if (text) sendMessage(text);
  else setTimeout(() => input.focus(), 150);
}

function closeChat() {
  chatWidget.classList.remove('open');
  chatWidget.setAttribute('aria-hidden', 'true');
  chatFab.classList.remove('hidden');
  chatFab.setAttribute('aria-expanded', 'false');
}

function formatClock(date = new Date()) {
  return new Intl.DateTimeFormat('ru-RU', {hour: '2-digit', minute: '2-digit'}).format(date);
}

function messageElement(text, role, withTime = true) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  const p = document.createElement('p');
  p.textContent = text;
  wrap.appendChild(p);
  if (withTime) {
    const time = document.createElement('time');
    time.dateTime = new Date().toISOString();
    time.textContent = formatClock();
    wrap.appendChild(time);
  }
  return wrap;
}

function addMessage(text, role) {
  messages.appendChild(messageElement(text, role));
  scrollChat();
}

function scrollChat() { messages.scrollTop = messages.scrollHeight; }

function renderGreeting() {
  messages.innerHTML = '';
  addMessage(GREETING, 'bot');
  const list = document.createElement('div');
  list.className = 'scenario-list';
  list.id = 'scenarioList';
  SCENARIOS.forEach(([label, question]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => sendMessage(question));
    list.appendChild(button);
  });
  messages.appendChild(list);
}

function showTyping() {
  const el = document.createElement('div');
  el.id = 'typing';
  el.className = 'message bot typing';
  el.innerHTML = '<p><i></i><i></i><i></i></p>';
  messages.appendChild(el);
  scrollChat();
}

function setDialogState(state) {
  dialogState = state || 'open';
  $('#dialogState').textContent = DIALOG_LABELS[dialogState] || dialogState;
  if (LEAD_STATES.has(dialogState)) openLeadForm();
  if (dialogState === 'submitted') closeLeadForm();
}

async function sendMessage(raw) {
  const text = (raw ?? input.value).trim();
  if (!text || form.dataset.busy === '1') return;
  form.dataset.busy = '1';
  input.value = '';
  input.style.height = 'auto';
  $('#scenarioList')?.remove();
  addMessage(text, 'user');
  showTyping();
  try {
    const turn = await withSession((id) => api(`/api/sessions/${id}/messages?stream=false`, {method: 'POST', body: {text}}));
    $('#typing')?.remove();
    addMessage(turn.reply, 'bot');
    setDialogState(turn.dialog);
    if (turn.lead_id) addLeadCard(turn.lead_id);
  } catch (error) {
    $('#typing')?.remove();
    addMessage(`Не удалось получить ответ: ${error.message}`, 'bot');
  } finally {
    form.dataset.busy = '0';
    input.focus();
    if (isAdminOpen()) refreshAdmin();
  }
}

async function resetChat() {
  if (sessionId) {
    try { setSession((await api(`/api/sessions/${sessionId}/reset`, {method: 'POST'})).session_id); }
    catch { await newSession().catch(() => setSession('')); }
  }
  leadForm.reset();
  closeLeadForm();
  setDialogState('open');
  renderGreeting();
}

// Восстанавливаем переписку текущей сессии после перезагрузки страницы
async function restoreChat() {
  if (!sessionId) { renderGreeting(); return; }
  try {
    const history = await api(`/api/sessions/${sessionId}/messages`);
    if (!history.length) { renderGreeting(); return; }
    messages.innerHTML = '';
    history.forEach((m) => messages.appendChild(messageElement(m.text, m.role === 'user' ? 'user' : 'bot', false)));
    scrollChat();
  } catch {
    setSession('');
    renderGreeting();
  }
}

// --- заявка ---

function openLeadForm() { leadForm.classList.add('open'); }
function closeLeadForm() { leadForm.classList.remove('open'); $('#leadError').textContent = ''; }

function leadFormBody() {
  const data = new FormData(leadForm);
  const optional = (name) => String(data.get(name) || '').trim() || null;
  return {
    name: String(data.get('name') || '').trim(),
    phone: String(data.get('phone') || '').trim(),
    company: String(data.get('company') || '').trim(),
    pd_consent: data.get('pd_consent') === 'on',
    email: optional('email'),
    position: optional('position'),
  };
}

function leadFormProblem(body) {
  if (!body.name || !body.phone || !body.company) return 'Заполните ФИО, телефон и компанию.';
  if (body.phone.replace(/\D/g, '').length < 10) return 'Телефон: нужно 10 цифр, например +7 912 345-67-89.';
  if (!body.pd_consent) return 'Нужно согласие на обработку персональных данных.';
  return '';
}

async function submitLead(event) {
  event.preventDefault();
  const body = leadFormBody();
  const problem = leadFormProblem(body);
  $('#leadError').textContent = problem;
  if (problem) return;
  const button = $('button[type=submit]', leadForm);
  button.disabled = true;
  try {
    const receipt = await withSession((id) => api(`/api/sessions/${id}/lead`, {method: 'POST', body}));
    addMessage(receipt.reply, 'bot');
    setDialogState(receipt.dialog);
    if (receipt.lead_id) addLeadCard(receipt.lead_id);
    leadForm.reset();
  } catch (error) {
    $('#leadError').textContent = error.status === 422
      ? `Проверьте данные: ${error.message}`
      : `Не удалось отправить заявку: ${error.message}`;
  } finally {
    button.disabled = false;
    if (isAdminOpen()) refreshAdmin();
  }
}

function addLeadCard(leadId) {
  const card = document.createElement('div');
  card.className = 'lead-card';
  card.innerHTML = `<strong>✓ Заявка #${escapeHtml(leadId)} передана менеджеру</strong>
    <div><button type="button">Открыть заявку</button><a href="${leadFileUrl(leadId)}" download>Скачать .txt</a></div>`;
  $('button', card).addEventListener('click', () => { openAdmin('leads'); openLeadViewer(leadId); });
  messages.appendChild(card);
  scrollChat();
}

function leadFileUrl(leadId) { return `/api/leads/${encodeURIComponent(leadId)}.txt`; }

// --- мониторинг ---

function formatMs(value) { return value ? `${(Number(value) / 1000).toFixed(2)} с` : '—'; }
function timingPart(value, total) { return total ? `${Math.max(1, value / total * 100)}%` : '0%'; }
function escapeHtml(value) { const d = document.createElement('div'); d.textContent = String(value ?? ''); return d.innerHTML; }
function formatTime(iso) { return iso ? new Date(iso).toLocaleString('ru-RU', {dateStyle: 'short', timeStyle: 'medium'}) : ''; }

function traceKey(item) { return `${item.session_id}:${item.turn}`; }

function renderTrace(item, isOpen) {
  const t = item.timings_ms || {};
  const total = Number(t.total || 0);
  const parts = ['extract', 'retrieve', 'respond'].filter((name) => t[name]);
  const documents = item.documents || [];
  const dialog = item.dialog || {};
  const card = document.createElement('article');
  card.className = 'trace-card';
  card.dataset.traceId = traceKey(item);
  const flow = dialog.to
    ? `<p class="trace-flow">Ход ${item.turn} · ${escapeHtml(dialog.from || '?')} → <b>${escapeHtml(dialog.to)}</b>${dialog.leader ? ` · лидер: ${escapeHtml(dialog.leader)}` : ''}${item.next_slot ? ` · следующий вопрос: ${escapeHtml(item.next_slot)}` : ''}${dialog.reason ? ` · ${escapeHtml(dialog.reason)}` : ''}</p>`
    : `<p class="trace-flow">Ход ${item.turn}</p>`;
  const docs = documents.length
    ? `<ul class="trace-docs">${documents.map((d) => `<li><span>${escapeHtml(d.title)}</span><span>${Number(d.score).toFixed(2)}</span></li>`).join('')}</ul>`
    : '';
  card.innerHTML = `
    <div class="trace-head"><div><strong>Диалог ${escapeHtml(item.session_id.slice(0, 8))}</strong><div class="trace-id">${formatTime(item.created_at)}</div></div><div class="trace-time">${formatMs(total)}</div></div>
    <div class="timing-bar">${parts.map((name) => `<i class="${name}" style="width:${timingPart(t[name], total)}" title="${name}: ${t[name]} ms"></i>`).join('')}</div>
    ${flow}
    <div class="trace-details"><span>LLM: ${(item.llm_calls || []).length}</span><span>источники: ${documents.length}</span><span>слов: ${item.reply_words || 0}</span>${parts.map((name) => `<span>${name}: ${formatMs(t[name])}</span>`).join('')}${item.degraded?.length ? `<span>⚠ ${escapeHtml(item.degraded.join(', '))}</span>` : '<span>✓ без деградации</span>'}</div>
    ${docs}
    <details${isOpen ? ' open' : ''}><summary>Подробный JSON трассы</summary><pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></details>`;
  return card;
}

async function refreshHealth() {
  const lamp = $('#statusLamp');
  try {
    const health = await api('/healthz');
    lamp.className = 'status-lamp ready';
    $('#healthTitle').textContent = 'Сервис готов';
    $('#healthMeta').textContent = [health.model || 'модель не задана', health.retrieval, `${health.chunks} разделов базы`, health.dense_ready ? 'векторный индекс готов' : 'только BM25'].filter(Boolean).join(' · ');
  } catch (error) {
    lamp.className = 'status-lamp error';
    $('#healthTitle').textContent = 'Сервис недоступен';
    $('#healthMeta').textContent = error.message;
  }
}

async function refreshTraces() {
  const since = storageGet(CLEARED_KEY);
  const feed = await api(`/api/admin/traces?limit=50${since ? `&since=${encodeURIComponent(since)}` : ''}`);
  $('#metricRequests').textContent = feed.stats.requests;
  $('#metricAvg').textContent = formatMs(feed.stats.avg_ms);
  $('#metricLast').textContent = formatMs(feed.stats.last_ms);
  $('#metricCalls').textContent = feed.stats.llm_calls;
  const list = $('#traceList');
  // перерисовка каждые 2,5 с не должна сворачивать открытый JSON и сбрасывать прокрутку
  const opened = new Set($$('details[open]', list).map((d) => d.closest('.trace-card')?.dataset.traceId));
  const scrolls = new Map($$('.trace-card pre', list).map((pre) => [pre.closest('.trace-card')?.dataset.traceId, pre.scrollTop]));
  const panel = $('#adminPanel');
  const panelScroll = panel.scrollTop;
  list.innerHTML = '';
  if (!feed.items.length) list.innerHTML = '<div class="empty-state">Пока нет запросов. Откройте чат и задайте вопрос.</div>';
  else feed.items.forEach((item) => list.appendChild(renderTrace(item, opened.has(traceKey(item)))));
  $$('.trace-card pre', list).forEach((pre) => { pre.scrollTop = scrolls.get(pre.closest('.trace-card')?.dataset.traceId) || 0; });
  panel.scrollTop = panelScroll;
}

function leadStatus(status) {
  if (status === 'sent' || status === 'mock_sent') return ['ok', status === 'sent' ? 'в CRM' : 'сохранена'];
  if (status === 'send_failed') return ['fail', 'ошибка CRM'];
  return ['', status];
}

async function refreshLeads() {
  const leads = await api('/api/leads?limit=100');
  $('#leadCount').textContent = leads.length;
  const list = $('#leadList');
  list.innerHTML = '';
  if (!leads.length) { list.innerHTML = '<div class="empty-state">Заявок пока нет. Пройдите диалог до контактов или нажмите «Заявка» в чате.</div>'; return; }
  leads.forEach((lead) => {
    const contact = lead.brief?.contact || {};
    const [cls, label] = leadStatus(lead.status);
    const row = document.createElement('article');
    row.className = 'lead-row';
    row.tabIndex = 0;
    row.innerHTML = `<div><strong>${escapeHtml(contact.company || 'Без компании')}</strong> · ${escapeHtml(contact.name || '')}<br><small>#${escapeHtml(lead.id)} · ${formatTime(lead.created_at)} · ${escapeHtml(contact.phone || '')}</small></div><span class="status ${cls}">${escapeHtml(label)}</span>`;
    const open = () => openLeadViewer(lead.id);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
    list.appendChild(row);
  });
}

async function refreshAdmin() {
  await refreshHealth();
  try { await Promise.all([refreshTraces(), refreshLeads()]); }
  catch (error) {
    $('#healthTitle').textContent = 'Панель недоступна';
    $('#healthMeta').textContent = error.message;
    $('#statusLamp').className = 'status-lamp error';
  }
}

function selectTab(tab) {
  activeTab = tab;
  $$('.admin-tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  $$('[data-pane]').forEach((pane) => { pane.hidden = pane.dataset.pane !== tab; });
}

function isAdminOpen() { return $('#adminPanel').classList.contains('open'); }

function openAdmin(tab = activeTab) {
  selectTab(tab);
  $('#adminPanel').classList.add('open');
  $('#adminPanel').setAttribute('aria-hidden', 'false');
  $('#openAdmin').setAttribute('aria-expanded', 'true');
  document.body.classList.add('admin-open');
  refreshAdmin();
  clearInterval(adminTimer);
  adminTimer = setInterval(refreshAdmin, 2500);
}

function closeAdmin() {
  $('#adminPanel').classList.remove('open');
  $('#adminPanel').setAttribute('aria-hidden', 'true');
  $('#openAdmin').setAttribute('aria-expanded', 'false');
  document.body.classList.remove('admin-open');
  clearInterval(adminTimer);
}

// --- просмотр заявки ---

async function openLeadViewer(leadId) {
  const viewer = $('#leadViewer');
  $('#leadViewerTitle').textContent = `Заявка #${leadId}`;
  $('#leadViewerMeta').textContent = 'Загрузка…';
  $('#leadBrief').textContent = '';
  $('#leadDialog').innerHTML = '';
  $('#leadDownload').href = leadFileUrl(leadId);
  viewer.classList.add('open');
  viewer.setAttribute('aria-hidden', 'false');
  try {
    const lead = await api(`/api/leads/${encodeURIComponent(leadId)}`);
    const contact = lead.brief?.contact || {};
    const [, label] = leadStatus(lead.status);
    $('#leadViewerTitle').textContent = `${contact.company || 'Заявка'} · #${lead.id}`;
    $('#leadViewerMeta').textContent = `${formatTime(lead.created_at)} · ${label} · ${contact.name || ''} ${contact.phone || ''}`;
    $('#leadBrief').textContent = lead.brief_text;
    const history = await api(`/api/sessions/${encodeURIComponent(lead.session_id)}/messages`);
    const dialog = $('#leadDialog');
    history.forEach((m) => dialog.appendChild(messageElement(m.text, m.role === 'user' ? 'user' : 'bot', false)));
  } catch (error) {
    $('#leadViewerMeta').textContent = `Не удалось загрузить заявку: ${error.message}`;
  }
}

function closeLeadViewer() {
  $('#leadViewer').classList.remove('open');
  $('#leadViewer').setAttribute('aria-hidden', 'true');
}

// --- события ---

chatFab.addEventListener('click', () => openChat());
$('#closeChat').addEventListener('click', closeChat);
$('#resetChat').addEventListener('click', resetChat);
$('#toggleLead').addEventListener('click', () => (leadForm.classList.contains('open') ? closeLeadForm() : openLeadForm()));
$('#closeLead').addEventListener('click', closeLeadForm);
leadForm.addEventListener('submit', submitLead);
$$('[data-open-chat]').forEach((el) => el.addEventListener('click', () => openChat()));
$$('[data-open-leads]').forEach((el) => el.addEventListener('click', () => openAdmin('leads')));
form.addEventListener('submit', (e) => { e.preventDefault(); sendMessage(); });
input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 90)}px`; });
$('#openAdmin').addEventListener('click', () => openAdmin());
$('#closeAdmin').addEventListener('click', closeAdmin);
$('#refreshAdmin').addEventListener('click', refreshAdmin);
$('#clearLogs').addEventListener('click', () => { storageSet(CLEARED_KEY, new Date().toISOString()); refreshAdmin(); });
$$('.admin-tabs button').forEach((b) => b.addEventListener('click', () => selectTab(b.dataset.tab)));
$('#closeViewer').addEventListener('click', closeLeadViewer);
$('#leadViewer').addEventListener('click', (e) => { if (e.target.id === 'leadViewer') closeLeadViewer(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLeadViewer(); });

restoreChat();
