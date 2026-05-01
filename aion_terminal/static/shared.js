/* AION TERMINAL — shared.js
   Utilities, API helpers, navigation, math kit, color map. */

// ---------- API ----------
const API = (typeof window !== 'undefined' && window.__AION_API__) || 'http://localhost:8000';

async function api(path, opts = {}) {
  const url = path.startsWith('http') ? path : API + path;
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || j.error || msg; } catch (_) {}
    throw new Error(`HTTP ${res.status}: ${msg}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// ---------- Sectors ----------
const SECTORS = {
  ai_infra:   { name: 'AI INFRA',    etf: 'XLK',  tickers: ['APLD','IONQ','BE','NVDA','PLTR'] },
  semis:      { name: 'SEMIS',       etf: 'SOXX', tickers: ['NVDA','TSM','AMD','SNDK','LITE','AAOI'] },
  memory:     { name: 'MEMORY',      etf: 'XLK',  tickers: ['SNDK','LITE'] },
  biotech:    { name: 'BIOTECH',     etf: 'XLV',  tickers: ['ERAS','TERN','TNGX','INBX','CELC','AEHR'] },
  crypto:     { name: 'CRYPTO',      etf: 'XLF',  tickers: ['COIN'] },
  defense:    { name: 'DEFENSE',     etf: 'XLI',  tickers: ['PLTR','ASTS'] },
  macro:      { name: 'MACRO',       etf: 'SPY',  tickers: ['SPY','QQQ','GLD','TSM','AMD'] },
  b_universe: { name: 'B-UNIVERSE',  etf: 'IWM',  tickers: [] },
};

async function loadBUniverseTickers() {
  try {
    const data = await api('/rs/candidates?limit=50');
    const tickers = (data || []).map(r => (r.ticker || '').toUpperCase()).filter(Boolean);
    SECTORS.b_universe.tickers = tickers.slice(0, 30);
  } catch (err) {
    console.warn('B-universe load failed', err);
    SECTORS.b_universe.tickers = [];
  }
}

// ---------- Routing ----------
function goToSymbol(ticker) {
  if (!ticker) return;
  ticker = String(ticker).toUpperCase().trim();
  sessionStorage.setItem('aion_ticker', ticker);
  window.location.href = `/static/symbol.html?ticker=${encodeURIComponent(ticker)}`;
}

function getQueryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

// ---------- Random / Math ----------
function randn() {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.sqrt(2);
  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1)*t*Math.exp(-x*x);
  return 0.5 * (1 + sign * y);
}

function invNorm(p) {
  if (p <= 0) return -4;
  if (p >= 1) return 4;
  if (p === 0.5) return 0;
  const a = [0,-3.969683028665376,220.9460984245205,-275.9285104469687,138.357751867269,-30.66479806614716,2.506628277459239];
  const b = [0,-54.47609879822406,161.5858368580409,-155.6989798598866,66.80131188771972,-13.28068155288572];
  const c = [0,-0.007784894002430293,-0.3223964580411365,-2.400758277161838,-2.549732539343734,4.374664141464968,2.938163982698783];
  const d = [0,0.007784695709041462,0.3224671290700398,2.445134137142996,3.754408661907416];
  const pL = 0.02425, pHi = 1 - pL;
  let q, r;
  if (p < pL) {
    q = Math.sqrt(-2 * Math.log(p));
    return (((((c[1]*q + c[2])*q + c[3])*q + c[4])*q + c[5])*q + c[6]) /
           ((((d[1]*q + d[2])*q + d[3])*q + d[4])*q + 1);
  }
  if (p <= pHi) {
    q = p - 0.5; r = q * q;
    return (((((a[1]*r + a[2])*r + a[3])*r + a[4])*r + a[5])*r + a[6]) * q /
           (((((b[1]*r + b[2])*r + b[3])*r + b[4])*r + b[5])*r + 1);
  }
  q = Math.sqrt(-2 * Math.log(1 - p));
  return -(((((c[1]*q + c[2])*q + c[3])*q + c[4])*q + c[5])*q + c[6]) /
          ((((d[1]*q + d[2])*q + d[3])*q + d[4])*q + 1);
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ---------- Color map ----------
const AION_STOPS = [
  [0.00, [0, 61, 92]],
  [0.20, [0, 88, 140]],
  [0.40, [14, 100, 140]],
  [0.50, [20, 20, 35]],
  [0.60, [100, 20, 100]],
  [0.80, [180, 0, 180]],
  [1.00, [255, 0, 220]],
];

function cmap(t) {
  t = clamp(t, 0, 1);
  for (let i = 0; i < AION_STOPS.length - 1; i++) {
    const [s1, c1] = AION_STOPS[i];
    const [s2, c2] = AION_STOPS[i + 1];
    if (t >= s1 && t <= s2) {
      const f = (t - s1) / (s2 - s1);
      return c1.map((v, j) => Math.round(v + f * (c2[j] - v)));
    }
  }
  return AION_STOPS[AION_STOPS.length - 1][1];
}

function rgbStr(c) { return `rgb(${c[0]},${c[1]},${c[2]})`; }
function rgbLum(c) { return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255; }

// ---------- Formatting ----------
function fmtDollar(v) {
  if (v == null || isNaN(v)) return '$0';
  const a = Math.abs(v); const sign = v < 0 ? '-' : '';
  if (a < 1000) return `${sign}$${a.toFixed(0)}`;
  if (a < 1e6)  return `${sign}$${(a/1000).toFixed(1)}K`;
  if (a < 1e9)  return `${sign}$${(a/1e6).toFixed(2)}M`;
  return `${sign}$${(a/1e9).toFixed(2)}B`;
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(2);
}

function fmtPct(v, digits = 2) {
  if (v == null || isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${Number(v).toFixed(digits)}%`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-US', { timeZone: 'America/New_York', hour12: false });
  } catch (_) { return iso; }
}

// ---------- Regime helpers ----------
function regimeClass(r) {
  if (!r) return 'badge-neutral';
  const up = String(r).toUpperCase();
  if (up.includes('ACCEL')) return 'badge-acceleration';
  if (up.includes('TREND')) return 'badge-trend';
  if (up.includes('RANGE')) return 'badge-range';
  if (up.includes('DANGER') || up.includes('REVER')) return 'badge-danger';
  if (up.includes('SUPPORT')) return 'badge-support';
  if (up.includes('MAGNET')) return 'badge-magnet';
  return 'badge-neutral';
}

// ---------- Market hours ----------
function isMarketHours() {
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const mins = et.getHours() * 60 + et.getMinutes();
  return mins >= 570 && mins <= 960;
}

// ---------- ET clock ----------
function startClock() {
  const el = document.getElementById('et-clock');
  if (!el) return;
  function tick() {
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const h = String(et.getHours()).padStart(2, '0');
    const m = String(et.getMinutes()).padStart(2, '0');
    const s = String(et.getSeconds()).padStart(2, '0');
    el.textContent = `↻ ${h}:${m}:${s} ET`;
  }
  tick();
  setInterval(tick, 1000);
}

// ---------- Navigation render ----------
function renderNav(activePage) {
  const navHtml = `
    <div class="nav">
      <div class="nav-logo">
        <span class="infinity">∞</span>
        <span class="gradient-text">AION TERMINAL</span>
      </div>
      <div class="nav-links">
        <a class="nav-link ${activePage === 'dashboard' ? 'active' : ''}" href="/static/index.html">Dashboard</a>
        <a class="nav-link ${activePage === 'symbol' ? 'active' : ''}"    href="/static/symbol.html">Symbol</a>
        <a class="nav-link ${activePage === 'screener' ? 'active' : ''}"  href="/static/screener.html">Screener</a>
        <a class="nav-link ${activePage === 'positions' ? 'active' : ''}" href="/static/positions.html">Positions</a>
        <a class="nav-link ${activePage === 'agents' ? 'active' : ''}"    href="/static/agents.html">Agents</a>
      </div>
      <form class="nav-search" id="nav-search-form">
        <span class="icon">⚡</span>
        <input id="nav-ticker-input" placeholder="${(sessionStorage.getItem('aion_ticker') || 'NVDA')}" />
      </form>
      <span class="nav-clock" id="et-clock">↻ --:--:-- ET</span>
    </div>
  `;
  const target = document.getElementById('nav-mount');
  if (target) target.innerHTML = navHtml;
  const form = document.getElementById('nav-search-form');
  if (form) {
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const inp = document.getElementById('nav-ticker-input');
      if (inp && inp.value.trim()) {
        goToSymbol(inp.value.trim().toUpperCase());
      } else {
        goToSymbol(inp?.placeholder || 'NVDA');
      }
    });
  }
  startClock();
}

// ---------- Skeleton ----------
function skeleton(n = 3) {
  return Array.from({ length: n })
    .map(() => '<div class="skeleton" style="margin: 8px 0;"></div>')
    .join('');
}

// ---------- Error pane ----------
function errorPane(err, label = 'Error') {
  return `<div class="error-pane">${label}: ${String(err && err.message || err || 'unknown').slice(0, 200)}</div>`;
}

// ---------- Monte Carlo cone ----------
function runCone(lastPrice, drift, vol, days, sims = 1000) {
  const paths = [];
  for (let s = 0; s < sims; s++) {
    const path = [lastPrice];
    let p = lastPrice;
    for (let d = 0; d < days; d++) {
      const z = randn();
      const momentum = path.length >= 2
        ? Math.log(path[path.length - 1] / path[path.length - 2]) * 0.1
        : 0;
      const mr = -Math.log(p / (lastPrice * Math.exp(drift * (d + 1)))) * 0.05;
      const ret = (drift - 0.5 * vol * vol) + vol * z + momentum + mr;
      p = p * Math.exp(ret);
      path.push(p);
    }
    paths.push(path);
  }
  return paths;
}

function percentile(sortedArr, p) {
  if (!sortedArr.length) return null;
  const idx = clamp((sortedArr.length - 1) * p, 0, sortedArr.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sortedArr[lo];
  return sortedArr[lo] + (sortedArr[hi] - sortedArr[lo]) * (idx - lo);
}

// ---------- Decision Engine helpers ----------
function deriveS1S3(dealer) {
  const spot = Number(dealer.spot) || 0;
  const callWall = Number(dealer.call_wall);
  const putWall  = Number(dealer.put_wall);
  const king     = Number(dealer.king_node);

  const s1cand = [callWall, king].filter(v => Number.isFinite(v) && v > spot);
  const s3cand = [putWall, king].filter(v => Number.isFinite(v) && v < spot);

  const s1 = s1cand.length ? Math.min(...s1cand) : spot * 1.03;
  const s3 = s3cand.length ? Math.max(...s3cand) : spot * 0.97;
  return { s1, s3 };
}

function deriveState(spot, s1, s3) {
  if (spot > s1) return 'S1';
  if (spot < s3) return 'S3';
  return 'S2';
}

function touchProbability(spot, target, volAnnual, dteDays) {
  const T = Math.max(dteDays / 252, 1 / 252);
  const sigma = Math.max(volAnnual, 0.05);
  const d = Math.log(target / spot) / (sigma * Math.sqrt(T));
  return target > spot ? (1 - normalCDF(d)) : normalCDF(d);
}

function computeEVScore(position, dealer, probabilities) {
  let score = 0;
  const spot = Number(dealer.spot);
  const { s1, s3 } = deriveS1S3(dealer);
  const state = deriveState(spot, s1, s3);

  score += state === 'S1' ? 25 : state === 'S3' ? -40 : -5;
  if (state === 'S1' && spot > Number(dealer.king_node || 0)) score += 10;

  score += (probabilities.touch_s1 - probabilities.touch_s3) * 30;

  const delta = Math.abs(Number(position.delta) || 0);
  score += delta > 0.6 ? 10 : delta > 0.4 ? 5 : delta < 0.25 ? -8 : 0;

  const mark = Math.max(Number(position.mark) || 0.01, 0.01);
  const thetaBurden = Math.abs(Number(position.theta) || 0) / mark;
  score -= thetaBurden > 0.15 ? 20 : thetaBurden > 0.10 ? 10 : 0;

  const dte = Number(position.dte) || 0;
  score -= dte < 3 ? 15 : dte < 7 ? 8 : 0;

  if ((Number(position.iv) || 0) > 1.2 && state !== 'S1') score -= 10;

  const callDist = Number(dealer.call_wall_pct) || 0;
  if (callDist > 0 && callDist < 2 && state !== 'S1') score -= 12;

  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const hour = et.getHours(), min = et.getMinutes();
  if ((hour === 15 || (hour === 14 && min >= 45)) && dte <= 2) score -= 30;

  return clamp(Math.round(score), -100, 100);
}

function mapScoreToAction(score) {
  if (score >= 30)  return 'HOLD';
  if (score >= 15)  return 'HOLD (TRIM PARTIAL)';
  if (score >= 0)   return 'TRIM';
  if (score >= -20) return 'EXIT MAJORITY';
  return 'EXIT';
}

// ---------- Storage helpers ----------
const SS = {
  get(key, fallback) {
    try {
      const v = sessionStorage.getItem(key);
      if (v == null) return fallback;
      return JSON.parse(v);
    } catch (_) { return fallback; }
  },
  set(key, val) {
    try { sessionStorage.setItem(key, JSON.stringify(val)); } catch (_) {}
  },
};

// ---------- DOMContentLoaded helper ----------
function ready(fn) {
  if (document.readyState !== 'loading') fn();
  else document.addEventListener('DOMContentLoaded', fn);
}

// expose
window.AION = {
  API, api, apiPost, SECTORS, loadBUniverseTickers,
  goToSymbol, getQueryParam,
  randn, normalCDF, invNorm, clamp,
  cmap, rgbStr, rgbLum, AION_STOPS,
  fmtDollar, fmtPrice, fmtPct, fmtTime,
  regimeClass, isMarketHours,
  renderNav, skeleton, errorPane,
  runCone, percentile,
  deriveS1S3, deriveState, touchProbability, computeEVScore, mapScoreToAction,
  SS, ready,
};
