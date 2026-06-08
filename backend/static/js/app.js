/**
 * Estidafa - Micro-SaaS Hosting Platform
 * Main frontend application logic
 */

const API = window.location.origin;
const WS_BASE = API.replace(/^http/, 'ws');

// ========== State Management ==========
class AppState {
  constructor() {
    this.token = localStorage.getItem('token');
    this.user = JSON.parse(localStorage.getItem('user') || '{}');
    this.currentBotId = null;
    this.ws = null;
    this.resourceInterval = null;
    this.statusInterval = null;
  }

  isAuthenticated() { return !!this.token; }

  getHeaders() {
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${this.token}`,
    };
  }

  setUser(user) {
    this.user = user;
    localStorage.setItem('user', JSON.stringify(user));
  }

  setToken(token) {
    this.token = token;
    localStorage.setItem('token', token);
  }

  clear() {
    this.token = null;
    this.user = {};
    localStorage.removeItem('token');
    localStorage.removeItem('user');
  }

  cleanupIntervals() {
    if (this.resourceInterval) clearInterval(this.resourceInterval);
    if (this.statusInterval) clearInterval(this.statusInterval);
    this.resourceInterval = null;
    this.statusInterval = null;
  }

  cleanupWebSocket() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  cleanupAll() {
    this.cleanupIntervals();
    this.cleanupWebSocket();
  }
}

const state = new AppState();

// ========== Router / Section Management ==========
function showSection(section) {
  document.querySelectorAll('[id^="section-"]').forEach(s => s.classList.add('hidden'));
  const target = document.getElementById(`section-${section}`);
  if (target) target.classList.remove('hidden');
  if (section === 'bots') loadBots();
}

function switchDetailTab(tab) {
  document.querySelectorAll('[id^="panel"]').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('[id^="tab"]').forEach(t => {
    t.classList.remove('tab-active');
    t.classList.add('text-gray-500');
  });
  const panel = document.getElementById(`panel${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
  const tabEl = document.getElementById(`tab${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
  if (panel) panel.classList.remove('hidden');
  if (tabEl) {
    tabEl.classList.add('tab-active');
    tabEl.classList.remove('text-gray-500');
  }
}

// ========== Authentication ==========
async function login(email, password) {
  const res = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Login failed');
  state.setToken(data.access_token);
  state.setUser(data.user);
  return data;
}

async function register(username, email, password) {
  const res = await fetch(`${API}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Registration failed');
  state.setToken(data.access_token);
  state.setUser(data.user);
  return data;
}

function logout() {
  state.clear();
  state.cleanupAll();
  window.location.href = '/login';
}

// ========== Bots API ==========
async function loadBots() {
  try {
    const res = await fetch(`${API}/api/bots/`, { headers: state.getHeaders() });
    const bots = await res.json();
    renderBotsList(bots);
  } catch (err) {
    console.error('Failed to load bots:', err);
  }
}

function renderBotsList(bots) {
  const grid = document.getElementById('botsGrid');
  const noMsg = document.getElementById('noBotsMsg');
  grid.querySelectorAll('.bot-card').forEach(c => c.remove());

  if (!bots || bots.length === 0) {
    noMsg.classList.remove('hidden');
    return;
  }
  noMsg.classList.add('hidden');

  bots.forEach(bot => {
    const card = document.createElement('div');
    const statusClass = bot.status === 'running' ? 'border-emerald-500/30 bg-emerald-500/5'
      : bot.status === 'crashed' ? 'border-red-500/30 bg-red-500/5'
      : 'border-gray-700/50 bg-gray-800/30';

    card.className = `bot-card card p-5 cursor-pointer border ${statusClass} slide-up`;
    card.onclick = () => openBotDetail(bot.id);
    card.innerHTML = `
      <div class="flex items-start justify-between mb-3">
        <div class="w-10 h-10 rounded-xl ${bot.bot_type === 'python' ? 'bg-blue-500/10' : 'bg-purple-500/10'} flex items-center justify-center">
          <svg class="w-5 h-5 ${bot.bot_type === 'python' ? 'text-blue-400' : 'text-purple-400'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${bot.bot_type === 'python' ? 'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4' : 'M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01'}"/>
          </svg>
        </div>
        <span class="status-dot ${bot.status}"></span>
      </div>
      <h3 class="font-bold text-white mb-1">${escapeHtml(bot.name)}</h3>
      <p class="text-xs text-gray-500 mb-3">${bot.bot_type === 'python' ? 'بوت بايثون' : 'سكربت PHP'}</p>
      <div class="flex items-center gap-2 text-xs">
        <span class="${bot.status === 'running' ? 'text-emerald-400' : bot.status === 'crashed' ? 'text-red-400' : 'text-gray-500'}">
          ${bot.status === 'running' ? 'يعمل' : bot.status === 'crashed' ? 'معطل' : 'متوقف'}
        </span>
        <span class="text-gray-600">·</span>
        <span class="text-gray-600">${formatDate(bot.created_at)}</span>
      </div>
    `;
    grid.appendChild(card);
  });
}

async function createBot(name, botType, code, requirements) {
  const res = await fetch(`${API}/api/bots/`, {
    method: 'POST',
    headers: state.getHeaders(),
    body: JSON.stringify({ name, bot_type: botType, main_file: code, requirements }),
  });
  const data = await res.json();
  if (!res.ok) throw data;
  return data;
}

async function getBotDetail(botId) {
  const res = await fetch(`${API}/api/bots/${botId}`, { headers: state.getHeaders() });
  return await res.json();
}

async function controlBot(botId, action) {
  const res = await fetch(`${API}/api/bots/${botId}/${action}`, {
    method: 'POST',
    headers: state.getHeaders(),
  });
  return await res.json();
}

async function updateBotCode(botId, mainFile, requirements) {
  const res = await fetch(`${API}/api/bots/${botId}/code`, {
    method: 'PUT',
    headers: state.getHeaders(),
    body: JSON.stringify({ main_file: mainFile, requirements }),
  });
  const data = await res.json();
  if (!res.ok) throw data;
  return data;
}

async function deleteBot(botId) {
  const res = await fetch(`${API}/api/bots/${botId}`, {
    method: 'DELETE',
    headers: state.getHeaders(),
  });
  return await res.json();
}

// ========== Bot Detail View ==========
async function openBotDetail(botId) {
  state.cleanupAll();
  state.currentBotId = botId;

  try {
    const bot = await getBotDetail(botId);
    renderBotDetail(bot);

    state.statusInterval = setInterval(() => refreshBotStatus(botId), 3000);
    state.resourceInterval = setInterval(() => refreshBotResources(botId), 5000);

    connectLogs(botId);
    switchDetailTab('logs');
    showSection('detail');
  } catch (err) {
    console.error('Failed to load bot detail:', err);
  }
}

function renderBotDetail(bot) {
  document.getElementById('detailName').textContent = bot.name;
  document.getElementById('detailType').textContent = bot.bot_type === 'python' ? 'بوت بايثون' : 'سكربت PHP';
  document.getElementById('editCode').value = bot.main_file || '';
  document.getElementById('editRequirements').value = bot.requirements || '';
  updateBotStatusUI(bot.status, bot.resource_usage);
}

async function refreshBotStatus(botId) {
  try {
    const bot = await getBotDetail(botId);
    updateBotStatusUI(bot.status, bot.resource_usage);
  } catch (e) { /* ignore */ }
}

async function refreshBotResources(botId) {
  try {
    const bot = await getBotDetail(botId);
    const usage = bot.resource_usage || {};
    document.getElementById('detailCpu').textContent = (usage.cpu || 0) + '%';
    document.getElementById('detailRam').textContent = (usage.memory_mb || 0) + ' MB';
  } catch (e) { /* ignore */ }
}

function updateBotStatusUI(status, usage) {
  const dot = document.getElementById('detailStatusDot');
  const statusText = document.getElementById('detailStatus');
  const statusBig = document.getElementById('detailStatusText');
  dot.className = 'status-dot ' + (status || 'stopped');
  const labels = { running: 'يعمل', stopped: 'متوقف', crashed: 'معطل' };
  statusText.textContent = labels[status] || status;
  statusBig.textContent = labels[status] || status;
  if (usage) {
    document.getElementById('detailCpu').textContent = (usage.cpu || 0) + '%';
    document.getElementById('detailRam').textContent = (usage.memory_mb || 0) + ' MB';
  }
}

// ========== Bot Control Handlers ==========
async function handleControlBot(action) {
  if (!state.currentBotId) return;
  try {
    await controlBot(state.currentBotId, action);
    setTimeout(() => refreshBotStatus(state.currentBotId), 500);
    if (action === 'start') {
      setTimeout(() => connectLogs(state.currentBotId), 300);
    }
  } catch (e) { console.error(e); }
}

async function handleDeleteBot() {
  if (!state.currentBotId || !confirm('هل أنت متأكد من حذف هذا البوت؟')) return;
  try {
    await deleteBot(state.currentBotId);
    state.cleanupAll();
    state.currentBotId = null;
    showSection('bots');
  } catch (e) { console.error(e); }
}

async function handleSaveCode() {
  if (!state.currentBotId) return;
  const code = document.getElementById('editCode').value;
  const reqs = document.getElementById('editRequirements').value;
  const errEl = document.getElementById('editError');
  errEl.classList.add('hidden');
  try {
    await updateBotCode(state.currentBotId, code, reqs);
  } catch (data) {
    errEl.innerHTML = data.detail?.message || data.detail || 'فشل حفظ التعديلات';
    errEl.classList.remove('hidden');
  }
}

// ========== Live Logs via WebSocket ==========
function connectLogs(botId) {
  state.cleanupWebSocket();
  const consoleEl = document.getElementById('logConsole');
  const indicator = document.getElementById('liveIndicator');
  consoleEl.innerHTML = '<span class="text-gray-600">// جاري الاتصال بخادم السجلات...</span>';
  indicator.innerHTML = '<span class="w-2 h-2 rounded-full bg-yellow-400 animate-pulse"></span> متصل...';

  state.ws = new WebSocket(`${WS_BASE}/api/logs/ws/${botId}`);

  state.ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'log') {
        consoleEl.innerHTML += msg.data.replace(/\n/g, '<br>');
        consoleEl.scrollTop = consoleEl.scrollHeight;
      } else if (msg.type === 'status' && msg.data === 'stopped') {
        consoleEl.innerHTML += '\n<span class="text-red-400">// البوت متوقف</span>\n';
      }
    } catch (e) { /* ignore */ }
  };

  state.ws.onopen = () => {
    indicator.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse-slow"></span> مباشر';
  };

  state.ws.onclose = () => {
    indicator.innerHTML = '<span class="w-2 h-2 rounded-full bg-gray-500"></span> غير متصل';
  };

  state.ws.onerror = () => {
    indicator.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-400"></span> خطأ';
  };
}

// ========== Utility Functions ==========
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  return new Date(dateStr).toLocaleDateString('ar-SA', {
    year: 'numeric', month: 'short', day: 'numeric',
  });
}

// ========== Initialize on DOM Ready ==========
document.addEventListener('DOMContentLoaded', () => {
  if (!state.isAuthenticated()) {
    window.location.href = '/login';
    return;
  }
  if (state.user.username) {
    const display = document.getElementById('usernameDisplay');
    if (display) display.textContent = state.user.username;
  }
  showSection('bots');
});
