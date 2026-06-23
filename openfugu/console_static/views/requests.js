export async function viewRequests(main, ui) {
  const filters = { model: '', worker: '', status: '', endpoint: '', since: '', until: '' };

  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '请求与路由观测'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('刷新', { icon: 'refresh', onClick: () => ui.refresh() }))));

  // filter bar
  const bar = ui.el('div', { class: 'card', style: 'margin-bottom:16px' });
  const barBody = ui.el('div', { class: 'card-body', style: 'display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end' });
  barBody.appendChild(field('模型', select('f_model', ['', 'openfugu-flash', 'openfugu-pro'])));
  barBody.appendChild(field('状态', select('f_status', ['', 'ok', 'error'])));
  barBody.appendChild(field('Worker', ui.el('input', { id: 'f_worker', placeholder: 'worker id' })));
  barBody.appendChild(field('Endpoint', ui.el('input', { id: 'f_endpoint', placeholder: 'endpoint id' })));
  barBody.appendChild(field('开始时间', ui.el('input', { id: 'f_since', type: 'datetime-local' })));
  barBody.appendChild(field('结束时间', ui.el('input', { id: 'f_until', type: 'datetime-local' })));
  barBody.appendChild(ui.el('div', { class: 'form-row', style: 'margin:0' },
    ui.btn('筛选', { kind: 'primary', onClick: () => load() }),
    ui.btn('清除', { onClick: () => {
      document.getElementById('f_model').value = ''; document.getElementById('f_status').value = '';
      document.getElementById('f_worker').value = ''; document.getElementById('f_endpoint').value = '';
      document.getElementById('f_since').value = ''; document.getElementById('f_until').value = ''; load(); } })));
  bar.appendChild(barBody);
  main.appendChild(bar);

  const holder = ui.el('div');
  main.appendChild(holder);
  load();

  async function load() {
    filters.model = document.getElementById('f_model')?.value || '';
    filters.status = document.getElementById('f_status')?.value || '';
    filters.worker = document.getElementById('f_worker')?.value || '';
    filters.endpoint = document.getElementById('f_endpoint')?.value || '';
    filters.since = ts('f_since');
    filters.until = ts('f_until');
    const q = {};
    if (filters.model) q.model = filters.model;
    if (filters.status) q.status = filters.status;
    if (filters.worker) q.worker = filters.worker;
    if (filters.endpoint) q.endpoint = filters.endpoint;
    if (filters.since) q.since = filters.since;
    if (filters.until) q.until = filters.until;
    holder.innerHTML = '';
    holder.appendChild(ui.el('div', { class: 'empty' }, ui.el('span', { class: 'spinner' }), ' 加载中…'));
    const data = await ui.GET('/api/requests', q);
    holder.innerHTML = '';
    if (data.error) { holder.appendChild(ui.el('div', { class: 'err-box' }, data.error)); return; }
    render(data.items || []);
  }

  function render(items) {
    if (!items.length) {
      holder.appendChild(ui.el('div', { class: 'empty' }, '暂无请求日志。启动 product_serve 并调用 /v1/chat/completions 后会在此记录。'));
      return;
    }
    const total = items.length;
    const errs = items.filter(r => r.status === 'error').length;
    const avgLat = (items.reduce((s, r) => s + (r.latency || 0), 0) / total).toFixed(2);
    const g = ui.el('div', { class: 'grid grid-4', style: 'margin-bottom:16px' });
    g.appendChild(stat('请求数', total, `最近 ${total} 条`));
    g.appendChild(stat('错误', errs, errs ? `${(errs / total * 100).toFixed(0)}%` : '0%', errs ? 'red' : 'gray'));
    g.appendChild(stat('平均耗时', avgLat + 's', 'latency'));
    g.appendChild(stat('Pro 占比', `${pctPro(items)}%`, 'openfugu-pro'));
    holder.appendChild(g);

    const rows = items.map(r => [
      fmtTime(r.ts),
      ui.badge(r.model || '—', (r.model || '').includes('pro') ? 'purple' : 'blue'),
      r.latency != null ? ui.el('span', { class: 'mono' }, r.latency + 's') : '—',
      String(r.turns ?? 1),
      ui.badge(r.status || 'ok', r.status === 'error' ? 'red' : 'green'),
      ui.el('span', { class: 'mono text-sm' }, r.worker || '—'),
      ui.el('span', { class: 'mono text-sm' }, r.endpoint || '—'),
      ui.el('span', { class: 'mono text-sm' }, r.route_reason || '—'),
      ui.el('span', { class: 'mono text-sm' }, r.terminated_by || '—'),
      ui.el('div', { class: 'btn-row' },
        r.trace?.length ? ui.btn('trace', { sm: true, onClick: () => showDetail(ui, r) }) : null,
        ui.btn('详情', { sm: true, onClick: () => showDetail(ui, r) })),
    ]);
    holder.appendChild(ui.card('请求列表',
      ui.table(['时间', '模型', '耗时', 'turns', '状态', 'worker', 'endpoint', 'route_reason', 'terminated_by', '操作'], rows)));
  }

  function ts(id) {
    const v = document.getElementById(id)?.value;
    return v ? Math.floor(new Date(v).getTime() / 1000) : '';
  }

  function field(label, input) {
    return ui.el('div', { class: 'form-row', style: 'margin:0;min-width:140px' },
      ui.el('label', {}, label), input);
  }
  function select(id, opts) {
    const s = ui.el('select', { id });
    opts.forEach(o => s.appendChild(ui.el('option', { value: o }, o || '全部')));
    return s;
  }
  function stat(label, value, sub, kind) {
    return ui.el('div', { class: 'stat' },
      ui.el('div', { class: 'stat-label' }, label),
      ui.el('div', { class: 'stat-value', style: kind === 'red' ? 'color:var(--red)' : '' }, String(value)),
      ui.el('div', { class: 'stat-sub' }, sub));
  }
  function pctPro(items) {
    if (!items.length) return 0;
    return Math.round(items.filter(r => (r.model || '').includes('pro')).length / items.length * 100);
  }
  function fmtTime(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
  }
}

function showDetail(ui, r) {
  const ov = ui.el('div', { class: 'modal-overlay' });
  const m = ui.el('div', { class: 'modal', style: 'width:560px' });
  const body = ui.el('div', { class: 'modal-body' });
  body.appendChild(...ui.kvRows({
    ts: r.ts ? new Date(r.ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '',
    model: r.model, latency: r.latency != null ? r.latency + 's' : '', turns: r.turns,
    status: r.status, worker: r.worker, route_reason: r.route_reason,
    terminated_by: r.terminated_by, endpoint: r.endpoint, error: r.error,
  }));
  if (r.query) body.appendChild(ui.el('div', { class: 'mt' },
    ui.el('div', { class: 'text-dim text-sm mb' }, 'query:'),
    ui.el('div', { class: 'pre' }, r.query)));
  if (r.trace?.length) {
    body.appendChild(ui.el('div', { class: 'mt' }, ui.el('div', { class: 'text-dim text-sm mb' }, 'per-turn trace:')));
    body.appendChild(traceTable(ui, r.trace));
  }
  m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, '请求详情'),
    ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
  m.appendChild(body); ov.appendChild(m); document.body.appendChild(ov);
}

function traceTable(ui, trace) {
  return ui.table(['turn', 'agent', 'role', 'worker', 'reply 摘要'],
    trace.map(t => [
      String(t.turn ?? ''), String(t.agent_id ?? ''), ui.tag(t.role || ''),
      ui.el('span', { class: 'mono text-sm' }, t.worker || '—'),
      ui.el('span', { class: 'text-sm' }, t.reply || ''),
    ]));
}
