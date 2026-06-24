// OpenFugu console — vanilla ES-module SPA. No build step.
import { viewOverview } from './views/overview.js';
import { viewModels } from './views/models.js';
import { viewEndpoints } from './views/endpoints.js';
import { viewWorkers } from './views/workers.js';
import { viewTraining } from './views/training.js';
import { viewHeads } from './views/heads.js';
import { viewRequests } from './views/requests.js';
import { viewPlayground } from './views/playground.js';
import { viewValidation } from './views/validation.js';

// ---- API client ----
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  let data; try { data = await r.json(); } catch { data = { error: 'invalid response' }; }
  if (!r.ok && !data.error) data.error = `HTTP ${r.status}`;
  return data;
}
const GET = (p, q) => api('GET', p + (q ? '?' + new URLSearchParams(q) : ''));
const POST = (p, b) => api('POST', p, b);
const PUT = (p, b) => api('PUT', p, b);
const DEL = (p) => api('DELETE', p);

// ---- shared UI helpers (exported to views) ----
export const ui = {
  api, GET, POST, PUT, DEL,
  el(tag, props = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      if (typeof c === 'string' || typeof c === 'number') e.appendChild(document.createTextNode(String(c)));
      else e.appendChild(c);
    }
    return e;
  },
  badge(text, kind = 'gray') { return this.el('span', { class: `badge badge-${kind}` }, text); },
  tag(text) { return this.el('span', { class: 'tag' }, text); },
  dot(on) { return this.el('span', { class: `dot ${on ? 'dot-on' : 'dot-off'}` }); },
  btn(label, opts = {}) {
    const cls = ['btn']; if (opts.kind) cls.push(`btn-${opts.kind}`); if (opts.sm) cls.push('btn-sm');
    const b = this.el('button', { class: cls.join(' ') }, opts.icon ? this.icon(opts.icon) : null, label);
    if (opts.onClick) b.addEventListener('click', opts.onClick);
    return b;
  },
  icon(name) {
    const paths = {
      overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
      models: '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
      endpoints: '<path d="M5 12h14"/><path d="M12 5v14"/><circle cx="12" cy="12" r="9"/>',
      workers: '<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><circle cx="7" cy="7" r="1"/><circle cx="7" cy="17" r="1"/>',
      training: '<path d="M12 2v4"/><path d="m6.3 6.3 2.9 2.9"/><path d="M2 12h4"/><path d="m6.3 17.7 2.9-2.9"/><path d="M12 18v4"/><path d="m14.8 14.8 2.9 2.9"/><path d="M18 12h4"/><path d="m14.8 9.2 2.9-2.9"/>',
      heads: '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
      requests: '<path d="M3 12h4l3-9 4 18 3-9h4"/>',
      playground: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"/>',
      validation: '<path d="M9 12l2 2 4-4"/><path d="M21 12c0 5-3.5 7.5-8.5 9.5C7.5 19.5 4 17 4 12V6l8-3 8 3v6Z"/>',
      settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
      refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/>',
      play: '<polygon points="5 3 19 12 5 21 5 3"/>',
      upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
      x: '<path d="M18 6 6 18M6 6l12 12"/>',
      download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
      arrowUp: '<path d="m18 15-6-6-6 6"/>', arrowDown: '<path d="m6 9 6 6 6-6"/>',
      bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    };
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
    svg.innerHTML = paths[name] || '';
    return svg;
  },
  card(title, bodyEl, headExtra) {
    const c = this.el('div', { class: 'card' });
    const h = this.el('div', { class: 'card-head' },
      this.el('h3', {}, title), headExtra || this.el('div'));
    c.appendChild(h);
    const b = this.el('div', { class: 'card-body' });
    if (Array.isArray(bodyEl)) bodyEl.forEach(x => x && b.appendChild(x));
    else if (bodyEl) b.appendChild(bodyEl);
    c.appendChild(b);
    return c;
  },
  table(headers, rows) {
    const t = this.el('table');
    const thead = this.el('thead', {}, this.el('tr', {}, ...headers.map(h => this.el('th', {}, h))));
    const tbody = this.el('tbody');
    for (const row of rows) {
      const tr = this.el('tr', {}, ...row.map(c => {
        if (c instanceof Node) return this.el('td', {}, c);
        return this.el('td', {}, c == null ? '' : String(c));
      }));
      tbody.appendChild(tr);
    }
    t.appendChild(thead); t.appendChild(tbody);
    return t;
  },
  kvRows(obj) {
    return Object.entries(obj).filter(([, v]) => v != null && v !== '').map(([k, v]) =>
      this.el('div', { class: 'kv' }, this.el('span', { class: 'kv-k' }, k), this.el('span', { class: 'kv-v mono' }, String(v))));
  },
  toast(msg, kind = 'ok') {
    const t = this.el('div', { class: `toast toast-${kind}` }, msg);
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  },
  async refresh() { router.render(); },
};

// ---- router ----
const routes = [
  ['overview', '总览', 'overview', viewOverview],
  ['models', '标准模型', 'models', viewModels],
  ['endpoints', '接入源', 'endpoints', viewEndpoints],
  ['workers', 'Worker 池', 'workers', viewWorkers],
  ['training', 'Profile 与训练', 'training', viewTraining],
  ['heads', 'Head 版本', 'heads', viewHeads],
  ['requests', '请求与路由', 'requests', viewRequests],
  ['playground', '调用台', 'playground', viewPlayground],
  ['validation', '配置校验', 'validation', viewValidation],
];

const router = {
  current: 'overview',
  init() {
    window.addEventListener('hashchange', () => this.render());
    this.render();
  },
  render() {
    const hash = location.hash.slice(1) || 'overview';
    this.current = routes.find(r => r[0] === hash) ? hash : 'overview';
    const app = document.getElementById('app');
    app.innerHTML = '';
    app.appendChild(this.sidebar());
    const main = this.el('div', { class: 'main' });
    const route = routes.find(r => r[0] === this.current);
    if (route) route[3](main, ui);
    app.appendChild(main);
  },
  sidebar() {
    const s = this.el('div', { class: 'sidebar' });
    s.appendChild(this.el('div', { class: 'brand' },
      this.el('span', { class: 'brand-icon' }, '🐡'),
      this.el('div', {}, this.el('div', { class: 'brand-name' }, 'OpenFugu'),
        this.el('div', { class: 'brand-sub' }, 'Admin Console'))));
    const nav = this.el('div', { class: 'nav' });
    nav.appendChild(this.el('div', { class: 'nav-section' }, '运维'));
    routes.slice(0, 4).forEach(r => nav.appendChild(this.navItem(r)));
    nav.appendChild(this.el('div', { class: 'nav-section' }, '模型接入'));
    routes.slice(4, 6).forEach(r => nav.appendChild(this.navItem(r)));
    nav.appendChild(this.el('div', { class: 'nav-section' }, '观测与工具'));
    routes.slice(6).forEach(r => nav.appendChild(this.navItem(r)));
    s.appendChild(nav);
    s.appendChild(this.el('div', { class: 'sidebar-foot' }, 'OpenFugu · Apache-2.0'));
    return s;
  },
  navItem([key, label, icon,]) {
    return this.el('button', {
      class: `nav-item ${this.current === key ? 'active' : ''}`,
      onClick: () => { location.hash = key; }
    }, this.icon(icon), label);
  },
  el: ui.el.bind(ui), icon: ui.icon.bind(ui),
};

router.init();
