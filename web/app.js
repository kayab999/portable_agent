// ── SESSION TOKEN (Bearer) ───────────────────────────────────────────────────
let sessionToken = sessionStorage.getItem('bonsai_token') || '';

function authHeaders(extra = {}) {
  const h = { ...extra };
  if (sessionToken) {
    h['Authorization'] = `Bearer ${sessionToken}`;
    h['X-Bonsai-Token'] = sessionToken;
  }
  return h;
}

async function askPassword() {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:9999';
    const box = document.createElement('div');
    box.style.cssText = 'background:#161616;border:1px solid #3a3a3a;border-radius:12px;padding:20px;width:min(360px,90vw);display:flex;flex-direction:column;gap:10px;color:#eee;font-family:inherit';
    const title = document.createElement('strong');
    title.textContent = 'Servidor bloqueado';
    const hint = document.createElement('span');
    hint.textContent = 'Introduce la contraseña del vault';
    hint.style.cssText = 'font-size:12px;opacity:.75';
    const input = document.createElement('input');
    input.type = 'password';
    input.autocomplete = 'current-password';
    input.placeholder = 'Contraseña';
    input.style.cssText = 'padding:9px;border-radius:8px;border:1px solid #444;background:#1f1f1f;color:#eee;font:inherit';
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:4px';
    const cancel = document.createElement('button');
    cancel.textContent = 'Cancelar';
    cancel.style.cssText = 'padding:8px 12px;border-radius:8px;border:1px solid #444;background:transparent;color:#ccc;cursor:pointer';
    const submit = document.createElement('button');
    submit.textContent = 'Desbloquear';
    submit.style.cssText = 'padding:8px 12px;border-radius:8px;border:0;background:#2f7d4f;color:#fff;cursor:pointer';
    row.append(cancel, submit);
    box.append(title, hint, input, row);
    overlay.append(box);
    document.body.append(overlay);
    input.focus();

    const close = (value) => {
      overlay.remove();
      resolve(value);
    };
    cancel.addEventListener('click', () => close(null));
    submit.addEventListener('click', () => close(input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') close(input.value);
      if (e.key === 'Escape') close(null);
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close(null);
    });
  });
}

let sessionUnlockPromise = null;
let unlockCancelledAt = 0;

async function ensureSession() {
  if (sessionToken) return sessionToken;
  if (sessionUnlockPromise) return sessionUnlockPromise;
  sessionUnlockPromise = (async () => {
    const post = (body) => fetch(`${API_BASE}/api/session/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
    });
    try {
      let r = await post({});
      let d = await r.json().catch(() => ({}));
      if (r.ok && d.token) {
        sessionToken = d.token;
        sessionStorage.setItem('bonsai_token', sessionToken);
        return sessionToken;
      }
      if (d.error === 'not_provisioned') {
        showToast('Sin vault. Ejecuta: python launcher.py --set-password', 7000);
        return '';
      }
      if (r.status === 403) {
        // Avoid an immediate second prompt when a retry follows a cancel.
        if (Date.now() - unlockCancelledAt < 3000) return '';
        const pw = await askPassword();
        if (!pw) {
          unlockCancelledAt = Date.now();
          return '';
        }
        r = await post({ password: pw });
        d = await r.json().catch(() => ({}));
        if (r.ok && d.token) {
          sessionToken = d.token;
          sessionStorage.setItem('bonsai_token', sessionToken);
          showUnlockIndicator(true);
          return sessionToken;
        }
        showToast(
          d.error === 'invalid_credentials'
            ? 'Contraseña incorrecta.'
            : (d.message || 'No se pudo desbloquear.'),
          5000
        );
        return '';
      }
    } catch (e) {
      console.warn('session unlock failed', e);
    }
    return sessionToken;
  })();
  try {
    return await sessionUnlockPromise;
  } finally {
    sessionUnlockPromise = null;
  }
}

// ── RETRY HELPER ─────────────────────────────────────────────────────────────
async function fetchWithRetry(url, options, maxRetries = 3) {
  options = options || {};
  options.headers = authHeaders(options.headers || {});
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const resp = await fetch(url, options);
      if (resp.status === 401 && attempt === 0) {
        sessionToken = '';
        sessionStorage.removeItem('bonsai_token');
        await ensureSession();
        options.headers = authHeaders(options.headers || {});
        continue;
      }
      if (resp.status === 429) {
        const retryAfter = resp.headers.get('Retry-After');
        const wait = retryAfter ? parseInt(retryAfter) * 1000 : (1000 * Math.pow(2, attempt));
        if (attempt < maxRetries - 1) {
          await new Promise(r => setTimeout(r, wait));
          continue;
        }
      }
      return resp;
    } catch (e) {
      if (attempt < maxRetries - 1) {
        await new Promise(r => setTimeout(r, 1000 * Math.pow(2, attempt)));
        continue;
      }
      throw e;
    }
  }
}

// ── CONFIG (same-origin via gateway; fallback for file:// open) ─────────────
const API_BASE = (typeof location !== 'undefined' && location.protocol.startsWith('http'))
  ? `${location.protocol}//${location.host}`
  : 'http://127.0.0.1:8080';
const HEALTH   = `${API_BASE}/api/health`;
const HEALTH_LLM = `${API_BASE}/health`;
const RAG_API  = `${API_BASE}/api/rag`;
const IDENTITY_API = `${API_BASE}/api/identity`;
const PERSONAS_API = `${API_BASE}/api/personas`;

let messages    = [];
let sources     = [];
let loading     = false;
let modelName  = 'Bonsai';
let ragEnabled  = false;
let ragContext  = '';
let debugMode   = false;
let unlockStatus = 'locked';

// Estado de identidad
let personasList  = [];
let activePersona = null;
let ownerName     = '';
let selectedPersona = null;  // para el wizard OOBE

const TONES = {
  'Formal / Filosófico': '📚',
  'Técnico / Directo': '📊',
  'Práctico / Técnico': '🔧',
  'Creativo / Estructurado': '💡',
};

// ── IDENTITY & OOBE INIT ────────────────────────────────────────────────────
async function initIdentity() {
  try {
    await ensureSession();
    const r = await fetchWithRetry(IDENTITY_API, { method: 'GET' }, 2);
    if (!r.ok) return;
    const data = await r.json();

    debugMode = data.debug_mode === true;
    if (debugMode) showDebugBanner();

    if (data.needs_setup) {
      // Primer arranque sin identidad — mostrar OOBE (también en producción)
      await loadPersonas();
      showOOBE();
    } else if (data.active_persona) {
      activePersona = data.active_persona;
      ownerName = data.identity?.owner || '';
      showMainApp();
      updatePersonaBadge();
    } else {
      showMainApp();
    }
  } catch {
    showMainApp();
  }
}

async function loadPersonas() {
  try {
    await ensureSession();
    const r = await fetch(PERSONAS_API, { headers: authHeaders() });
    if (r.ok) {
      const data = await r.json();
      personasList = data.personas || [];
      renderPersonaCardsStep1();
    }
  } catch { personasList = []; }
}

function showOOBE() {
  document.getElementById('oobe-screen').classList.add('visible');
  document.getElementById('main-app')?.classList.remove('visible');
}

function showMainApp() {
  const oobe = document.getElementById('oobe-screen');
  if (oobe) oobe.classList.remove('visible');
}

function renderPersonaCardsStep1() {
  const container = document.getElementById('persona-cards-step1');
  if (!container) return;
  container.innerHTML = personasList.map(p => `
    <div class="persona-card" data-key="${p.key}" data-action="select-persona-step1"
         style="border-color:${selectedPersona === p.key ? p.color : 'var(--border)'}">
      <div class="persona-card-icon">${p.icon}</div>
      <div class="persona-card-name">${p.nombre}</div>
      <div class="persona-card-desc">${p.descripcion}</div>
      <div class="persona-card-tone">${p.tone}</div>
    </div>
  `).join('');
}

function selectPersonaStep1(key) {
  selectedPersona = key;
  personasList.forEach(p => {
    const card = document.querySelector(`.persona-card[data-key="${p.key}"]`);
    if (card) {
      card.classList.toggle('selected', p.key === key);
      card.style.borderColor = p.key === key ? p.color : 'var(--border)';
    }
  });
  document.getElementById('oobe-next-btn').disabled = false;
}

function oobeNext() {
  if (!selectedPersona) return;
  const p = personasList.find(x => x.key === selectedPersona);
  if (!p) return;

  document.getElementById('oobe-step-welcome').classList.remove('active');
  document.getElementById('oobe-owner-icon').textContent = p.icon;
  document.getElementById('oobe-owner-title').textContent = p.nombre;
  document.getElementById('oobe-step-owner').classList.add('active');
  document.getElementById('owner-name-input').focus();
}

function oobeBack() {
  document.getElementById('oobe-step-owner').classList.remove('active');
  document.getElementById('oobe-step-welcome').classList.add('active');
}

function oobeOwnerInput(val) {
  ownerName = val.trim();
  document.getElementById('oobe-save-btn').disabled = false;
}

async function oobeSave() {
  if (!selectedPersona) return;
  try {
    await ensureSession();
    const r = await fetchWithRetry(IDENTITY_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona: selectedPersona, owner: ownerName }),
    }, 2);
    if (r.ok) {
      const data = await r.json();
      activePersona = data.active_persona;
      ownerName = data.identity?.owner || '';
      showMainApp();
      updatePersonaBadge();
      renderMainApp();
    }
  } catch (e) {
    alert('Error guardando identidad: ' + e.message);
  }
}

function oobeSkip() {
  selectedPersona = 'investigador';
  ownerName = 'Usuario';
  oobeSave();
}

async function resetPersona() {
  if (!confirm('¿Resetear identidad? Volverás a la pantalla de configuración.')) return;
  try {
    await ensureSession();
    await fetch(IDENTITY_API + '/reset', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
    });
    activePersona = null;
    ownerName = '';
    selectedPersona = null;
    messages = [];
    await loadPersonas();
    showOOBE();
  } catch {}
}

// ── PERSONA BADGE (reemplaza tabs) ──────────────────────────────────────────
function updatePersonaBadge() {
  const badge = document.getElementById('persona-badge');
  const popLabel = document.getElementById('persona-badge-label');
  if (!badge || !activePersona) return;
  badge.querySelector('.p-icon').textContent = activePersona.icon;
  badge.querySelector('.p-name').textContent = activePersona.nombre;
  if (popLabel) popLabel.textContent = activePersona.nombre;
}

function togglePersonaSelector() {
  const pop = document.getElementById('persona-selector-popup');
  pop?.classList.toggle('open');
  if (pop?.classList.contains('open')) {
    renderPersonaSelector();
  }
}

function renderPersonaSelector() {
  const pop = document.getElementById('persona-selector-list');
  if (!pop) return;
  pop.innerHTML = personasList.map(p => `
    <div class="popt-item ${activePersona?.key === p.key ? 'active' : ''}"
         data-action="switch-persona" data-key="${p.key}">
      <span>${p.icon}</span><span>${p.nombre}</span>
    </div>
  `).join('') + `
    <div class="popt-divider"></div>
    <div class="popt-item" data-action="reset-persona">
      <span>🔄</span><span class="popt-reset">Resetear identidad...</span>
    </div>
  `;
}

async function switchPersona(key) {
  try {
    const r = await fetchWithRetry(IDENTITY_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona: key, owner: ownerName }),
    }, 1);
    if (r.ok) {
      const data = await r.json();
      activePersona = data.active_persona;
      messages = [];
      updatePersonaBadge();
      togglePersonaSelector();
      document.getElementById('messages').innerHTML = '';
      appendEmptyState();
    }
  } catch {}
}

// ── RENDER MAIN APP (reemplaza tabs copiloto/debate/citas/general) ──────────
function renderMainApp() {
  // El main-app div ya está en el HTML, poblamos según necesidad
}

// ── STATUS CHECK ─────────────────────────────────────────────────────────────
async function checkStatus() {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  try {
    const r = await fetch(HEALTH, { signal: AbortSignal.timeout(2000) });
    if (r.ok) {
      const d = await r.json().catch(() => ({}));
      modelName = d.model || d.model_alias || 'Bonsai';
      const llmOk = d.llm !== false;
      dot.className  = llmOk ? 'status-dot ok' : 'status-dot err';
      text.textContent = llmOk ? `${modelName} · activo` : `${modelName} · LLM caído`;
      document.getElementById('footer-model').textContent = `🌿 ${modelName} · local`;

      if (d.unlocked === true || r.headers.get('X-Unlock-Status') === 'unlocked') {
        unlockStatus = 'unlocked';
        showUnlockIndicator(true);
      } else {
        unlockStatus = 'locked';
      }
      if (d.debug_mode === true || r.headers.get('X-Debug-Mode') === 'true') {
        debugMode = true;
        showDebugBanner();
      }
      // Obtain session when server is unlocked or DEBUG (auth may still be off)
      if (d.debug_mode || d.unlocked || !d.auth_required) {
        await ensureSession();
      }
      return llmOk;
    }
  } catch (e) {
    console.warn('health check failed', e);
  }
  dot.className  = 'status-dot err';
  text.textContent = 'Servidor no disponible';
  return false;
}

function showUnlockIndicator(unlocked) {
  const ind = document.getElementById('unlock-indicator');
  if (ind) ind.style.display = unlocked ? 'flex' : 'none';
}

function showDebugBanner() {
  let banner = document.getElementById('debug-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'debug-banner';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#fbbf24;color:#000;padding:6px 16px;font-size:11px;font-weight:600;text-align:center;display:flex;align-items:center;justify-content:center;gap:8px';
    banner.innerHTML = '⚠ MODO DESARROLLO — seguridad deshabilitada &nbsp;|&nbsp; Quitar BONSAI_DEBUG=1 y usar owner.lock para producción';
    document.body.insertBefore(banner, document.body.firstChild);
    document.body.style.marginTop = '28px';
  }
  banner.style.display = 'flex';
}

// ── RAG HELPERS ──────────────────────────────────────────────────────────
async function fetchRagContext(query) {
  if (!ragEnabled) return '';
  try {
    const r = await fetchWithRetry(`${RAG_API}/query`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ query, top_k: 3 }),
      signal: AbortSignal.timeout(3000),
    });
    if (r.ok) {
      const d = await r.json();
      if (d.context) {
        const count = d.results ? d.results.length : 0;
        showRagPreview(count, d.results || []);
        return d.context;
      }
      showRagPreview(0, []);
    } else {
      const ind = document.getElementById('rag-context-indicator');
      if (ind) { ind.style.display = 'inline-flex'; ind.textContent = '📎 RAG error'; }
    }
  } catch (e) {
    console.warn('RAG query failed', e);
    const ind = document.getElementById('rag-context-indicator');
    if (ind) { ind.style.display = 'inline-flex'; ind.textContent = '📎 RAG offline'; }
  }
  return '';
}

function showRagPreview(count, results) {
  const ind = document.getElementById('rag-context-indicator');
  const prev = document.getElementById('rag-context-preview');
  if (!ind || !prev) return;
  if (count > 0) {
    ind.style.display = 'inline-flex';
    ind.textContent = `📎 ${count} docs encontrados`;
    prev.style.display = 'block';
    prev.innerHTML = results.map(r =>
      `<span style="color:var(--green-text)">${esc(r.path || r.file || '')}</span> — ${esc((r.text || '').substring(0, 80))}...`
    ).join('<br>');
  } else {
    ind.style.display = 'none';
    prev.style.display = 'none';
  }
}

function updateRagIndicator(docs, chunks) {
  const ind = document.getElementById('rag-indicator');
  const statusEl = document.getElementById('rag-status');
  if (ind) ind.style.display = 'flex';
  if (statusEl) statusEl.textContent = `${docs || 0} docs · ${chunks || 0} fragmentos`;
}

async function reindexDocs() {
  try {
    const r = await fetchWithRetry(`${RAG_API}/index`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      showToast(d.message || d.error || `RAG error ${r.status}`);
      return;
    }
    const st = d.status || {};
    updateRagIndicator(st.documents, st.chunks);
    showToast(`RAG: ${d.indexed || 0} fragmentos nuevos · ${d.skipped || 0} sin cambios`);
  } catch (e) {
    showToast('RAG offline: ' + e.message);
  }
}

async function initRag() {
  try {
    await ensureSession();
    const r = await fetch(`${RAG_API}/status`, {
      headers: authHeaders(),
      signal: AbortSignal.timeout(2000),
    });
    if (r.status === 503) {
      ragEnabled = false;
      const ind = document.getElementById('rag-indicator');
      if (ind) ind.style.display = 'none';
      return;
    }
    if (!r.ok) return;
    const d = await r.json();
    ragEnabled = true;
    updateRagIndicator(d.documents, d.chunks);
  } catch {}
}

// ── SIDEBAR ──────────────────────────────────────────────────────────────────
function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  if (sb) sb.classList.toggle('collapsed');
}

// ── FUENTES ──────────────────────────────────────────────────────────────────
function addSource() {
  const author = document.getElementById('s-author')?.value.trim() || '';
  const text   = document.getElementById('s-text')?.value.trim() || '';
  if (!text) return;
  sources.push({ id: Date.now(), author: author || 'Sin autor', text });
  renderSources();
  saveLocalState();
  const aEl = document.getElementById('s-author');
  const tEl = document.getElementById('s-text');
  if (aEl) aEl.value = '';
  if (tEl) tEl.value = '';
}

function renderSources() {
  const el = document.getElementById('src-list');
  if (!el) return;
  if (!sources.length) {
    el.innerHTML = '<div style="font-size:11px;color:var(--hint);text-align:center;padding:16px 0">Agrega fragmentos de textos filosóficos</div>';
    return;
  }
  el.innerHTML = sources.map(s => `
    <div class="src-item">
      <button class="src-del" data-action="delete-source" data-id="${s.id}">×</button>
      <div class="src-author">${esc(s.author)}</div>
      <div class="src-snippet">${esc(s.text.substring(0,100))}${s.text.length>100?'...':''}</div>
    </div>
  `).join('');
}

function delSource(id) {
  sources = sources.filter(s => s.id !== id);
  renderSources();
  saveLocalState();
}

// ── SYSTEM PROMPT (basado en persona) ────────────────────────────────────────
function buildSystem() {
  if (!activePersona) {
    return 'Eres un asistente de IA. Responde en español de forma útil y clara.';
  }

  const persona = activePersona;
  let system = persona.system_base || '';

  // Contexto de tesis (sidebar)
  const title   = document.getElementById('t-title')?.value.trim() || '';
  const arg     = document.getElementById('t-arg')?.value.trim() || '';
  const authors = document.getElementById('t-authors')?.value.trim() || '';

  if (title || arg || authors) {
    let ctx = '\n\n--- CONTEXTO DEL USUARIO ---\n';
    if (title)   ctx += `Título del proyecto: "${title}"\n`;
    if (arg)     ctx += `Objetivo: ${arg}\n`;
    if (authors) ctx += `Autores/Referencias clave: ${authors}\n`;
    system += ctx;
  }

  // Biblioteca de fuentes (contenido no confiable)
  if (sources.length) {
    const body = sources.map((src, i) =>
      `[${i+1}] ${src.author}\n"${src.text}"`).join('\n\n');
    system += '\n\n' + wrapUntrusted('SOURCES', body);
  }

  // Contexto RAG (contenido no confiable)
  if (ragContext) {
    system += '\n\n' + wrapUntrusted('RAG_CONTEXT', ragContext);
  }

  return system;
}

function wrapUntrusted(tag, body) {
  const safe = String(body).replace(/<<<BONSAI_/g, '<<<_BONSAI_');
  return `<<<BONSAI_${tag}>>>\n${safe}\n<<<END_BONSAI_${tag}>>>`;
}

// ── MARKDOWN (safe subset) ───────────────────────────────────────────────────
function renderMarkdown(src) {
  let s = esc(String(src || ''));
  // fenced code
  s = s.replace(/```([\s\S]*?)```/g, (_, code) =>
    `<pre><code>${code.replace(/<br>/g, '\n')}</code></pre>`);
  // inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // bold / italic
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // headings
  s = s.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  s = s.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^# (.+)$/gm, '<h3>$1</h3>');
  // unordered lists
  s = s.replace(/(?:^|\n)((?:- .+(?:\n|$))+)/g, (block) => {
    const items = block.trim().split('\n').map(l => l.replace(/^- /, '')).filter(Boolean);
    return '<ul>' + items.map(i => `<li>${i}</li>`).join('') + '</ul>';
  });
  // paragraphs: double newlines
  s = s.replace(/\n{2,}/g, '</p><p>');
  s = '<p>' + s + '</p>';
  s = s.replace(/<p><\/p>/g, '');
  s = s.replace(/\n/g, '<br>');
  return s;
}

function showToast(msg, ms = 3500) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// ── PERSIST SIDEBAR ──────────────────────────────────────────────────────────
function loadLocalState() {
  try {
    const raw = localStorage.getItem('bonsai_sidebar_v1');
    if (!raw) return;
    const data = JSON.parse(raw);
    if (Array.isArray(data.sources)) sources = data.sources;
    ['t-title','t-arg','t-authors'].forEach(id => {
      if (data[id] != null) {
        const el = document.getElementById(id);
        if (el) el.value = data[id];
      }
    });
    renderSources();
  } catch {}
}
function saveLocalState() {
  try {
    localStorage.setItem('bonsai_sidebar_v1', JSON.stringify({
      sources,
      't-title': document.getElementById('t-title')?.value || '',
      't-arg': document.getElementById('t-arg')?.value || '',
      't-authors': document.getElementById('t-authors')?.value || '',
    }));
  } catch {}
}

// ── CHAT ─────────────────────────────────────────────────────────────────────
let abortController = null;

function setLoading(on) {
  loading = on;
  const sendBtn = document.getElementById('send-btn');
  const cancelBtn = document.getElementById('cancel-btn');
  if (sendBtn) sendBtn.disabled = on;
  if (cancelBtn) cancelBtn.style.display = on ? 'inline-block' : 'none';
}

function cancelSend() {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  setLoading(false);
  showToast('Generación cancelada');
}

async function send(override) {
  if (loading) return;
  const input = document.getElementById('user-input');
  const text  = override || input?.value?.trim() || '';
  if (!text) return;

  document.getElementById('empty-state')?.remove();
  setLoading(true);
  if (input) { input.value = ''; input.style.height = '40px'; }

  appendMsg('user', text, null, false);
  // History for agent: only user/assistant content (server rebuilds system)
  const hist = messages.filter(m => m.role === 'user' || m.role === 'assistant');
  messages.push({ role: 'user', content: text });

  const thinkEl = appendThinking();
  const t0 = Date.now();
  abortController = new AbortController();

  const extra = buildSystem().includes('CONTEXTO DEL USUARIO')
    ? buildSystem().split('--- CONTEXTO DEL USUARIO ---')[1]?.split('---')[0] || ''
    : '';

  try {
    // Prefer server-side agent loop (tools + RAG + mask)
    await ensureSession();
    let resp = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      signal: abortController.signal,
      body: JSON.stringify({
        message: text,
        messages: hist,
        rag: true,
        extra_context: (extra || '').trim(),
        persona: activePersona?.key || undefined,
      }),
    });

    // Fallback to raw completions if agent route missing
    if (resp.status === 404) {
      ragContext = await fetchRagContext(text);
      resp = await fetchWithRetry(`${API_BASE}/v1/chat/completions`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          model: 'bonsai',
          messages: [{ role: 'system', content: buildSystem() }, ...messages],
          temperature: 0.7,
          stream: false,
        }),
      });
      if (!resp.ok) {
        if (resp.status === 429) throw new Error('Rate limit — espera un momento');
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      thinkEl.remove();
      const reply = data.choices?.[0]?.message?.content || '(sin respuesta)';
      messages.push({ role: 'assistant', content: reply });
      const label = activePersona ? `${activePersona.icon} ${activePersona.nombre}` : '🌿 Bonsai';
      appendMsg('bot', reply, label, true);
    } else {
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.message || err.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      thinkEl.remove();
      const reply = data.reply || '(sin respuesta)';
      if (Array.isArray(data.tools) && data.tools.length) {
        appendToolCards(data.tools);
      }
      messages.push({ role: 'assistant', content: reply });
      const label = activePersona ? `${activePersona.icon} ${activePersona.nombre}` : '🌿 Bonsai';
      appendMsg('bot', reply, label, true);
      if (data.rag_used) {
        const ind = document.getElementById('rag-context-indicator');
        if (ind) { ind.style.display = 'inline-flex'; ind.textContent = '📎 RAG activo'; }
      }
      if (Array.isArray(data.citations) && data.citations.length) {
        appendCitations(data.citations);
      }
      const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
      document.getElementById('footer-tokens').textContent =
        `${data.tools?.length || 0} tools · ${elapsed}s`;
    }
  } catch (e) {
    thinkEl.remove();
    if (e.name === 'AbortError') {
      appendMsg('bot', '_Cancelado._', '🌿 Bonsai', true);
    } else {
      appendMsg('bot', `Error: ${e.message}\n\n¿El servidor está activo en ${API_BASE}?`, '⚠️', true);
      showToast('Error de chat: ' + e.message);
    }
  }

  abortController = null;
  setLoading(false);
  scrollBottom();
  saveLocalState();
}

function resetSession() {
  messages = [];
  const el = document.getElementById('messages');
  if (el) el.innerHTML = '';
  appendEmptyState();
}

function appendEmptyState() {
  const el = document.getElementById('messages');
  if (!el) return;
  const personaLabel = activePersona ? `${activePersona.icon} ${activePersona.nombre}` : '🌿';
  const personaDesc = activePersona?.descripcion || 'IA 1-bit corriendo completamente local.';
  el.innerHTML = `
    <div class="empty" id="empty-state">
      <div class="empty-leaf">${personaLabel.split(' ')[0]}</div>
      <div class="empty-title">Este nodo está listo</div>
      <div class="empty-sub">${personaDesc}</div>
    </div>`;
}

function appendMsg(role, text, label, asMarkdown) {
  const el = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = `msg ${role === 'user' ? 'user' : 'bot'}`;
  const roleLabel = role === 'user' ? 'Tú' : (label || '🌿 Bonsai');
  const body = (asMarkdown && role !== 'user') ? renderMarkdown(text) : esc(text);
  div.innerHTML = `
    <div class="msg-role">${roleLabel}</div>
    <div class="msg-bubble">${body}</div>`;
  el.appendChild(div);
  scrollBottom();
}

function appendCitations(citations) {
  const el = document.getElementById('messages');
  if (!el || !citations.length) return;
  const div = document.createElement('div');
  div.className = 'msg bot';
  const items = citations.map(c => {
    const path = esc(c.path || '');
    const chunk = Number(c.chunk || 0);
    const score = Number(c.score || 0).toFixed(2);
    return `<li><code>${path}#${chunk}</code> <span class="src-score">${score}</span></li>`;
  }).join('');
  div.innerHTML = `<div class="msg-role">📎 Fuentes</div><div class="msg-bubble"><ul class="citations">${items}</ul></div>`;
  el.appendChild(div);
  scrollBottom();
}

function appendToolCards(tools) {  const el = document.getElementById('messages');
  tools.forEach(t => {
    const ok = !!t.ok;
    const details = document.createElement('details');
    details.className = 'tool-card';
    const msg = t.message || t.error || (ok ? 'ok' : 'error');
    details.innerHTML = `
      <summary>
        <span class="${ok ? 'tool-ok' : 'tool-fail'}">${ok ? '✓' : '✕'}</span>
        <span>tool: ${esc(t.tool || 'unknown')}</span>
      </summary>
      <div class="tool-body">${esc(String(msg))}</div>`;
    el.appendChild(details);
  });
  scrollBottom();
}

function appendThinking() {
  const el  = document.getElementById('messages');
  const personaIcon = activePersona ? `${activePersona.icon} ${activePersona.nombre}` : '🌿';
  const wrap = document.createElement('div');
  wrap.className = 'msg bot';
  wrap.innerHTML = `<div class="msg-role">${personaIcon}</div>
    <div class="thinking"><div class="dot"></div><div class="dot"></div><div class="dot"></div>
    <span style="font-size:11px;color:var(--hint);margin-left:4px">pensando...</span></div>`;
  el.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function scrollBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

function handleKey(e) {
  if (e.key === 'Escape') { cancelSend(); return; }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
}

function autoResize(el) {
  el.style.height = '40px';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function esc(t) {
  return String(t)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\n/g,'<br>');
}

// ── INIT ──────────────────────────────────────────────────────────────────
setInterval(checkStatus, 5000);
loadLocalState();
initIdentity();
setTimeout(initRag, 1500);
['t-title','t-arg','t-authors'].forEach(id => {
  document.getElementById(id)?.addEventListener('change', saveLocalState);
});


// ── EVENT DELEGATION (CSP: no inline handlers) ────────────────────────────
const CLICK_ACTIONS = {
  'toggle-persona': () => togglePersonaSelector(),
  'reindex-docs': () => reindexDocs(),
  'toggle-sidebar': () => toggleSidebar(),
  'reset-session': () => resetSession(),
  'save-engine': () => saveEngineConfig(),
  'toggle-engine': () => toggleEnginePanel(),
  'oobe-next': () => oobeNext(),
  'oobe-skip': () => oobeSkip(),
  'oobe-back': () => oobeBack(),
  'oobe-save': () => oobeSave(),
  'add-source': () => addSource(),
  'cancel-send': () => cancelSend(),
  'send': () => send(),
  'select-persona-step1': (el) => selectPersonaStep1(el.dataset.key),
  'switch-persona': (el) => switchPersona(el.dataset.key),
  'reset-persona': () => resetPersona(),
  'delete-source': (el) => delSource(Number(el.dataset.id)),
};

document.addEventListener('click', (event) => {
  const el = event.target.closest('[data-action]');
  if (!el) return;
  const handler = CLICK_ACTIONS[el.dataset.action];
  if (!handler) return;
  event.preventDefault();
  handler(el);
});

document.addEventListener('input', (event) => {
  const el = event.target.closest('[data-oninput]');
  if (!el) return;
  if (el.dataset.oninput === 'auto-resize') autoResize(el);
  if (el.dataset.oninput === 'oobe-owner') oobeOwnerInput(el.value);
});

document.addEventListener('keydown', (event) => {
  const el = event.target.closest('[data-onkeydown]');
  if (el && el.dataset.onkeydown === 'handle-key') handleKey(event);
});
