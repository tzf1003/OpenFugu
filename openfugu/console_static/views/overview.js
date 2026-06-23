export async function viewOverview(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '总览'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('刷新', { icon: 'refresh', onClick: () => ui.refresh() }),
      ui.btn('重新加载配置', { icon: 'upload', onClick: () => reloadCfg() }),
      ui.btn('下载快照', { icon: 'download', onClick: () => snapshot() }),
      ui.btn('Debug: 关', { id: 'btn-debug', icon: 'bolt', onClick: () => toggleDebug() }))));

  const data = await ui.GET('/api/overview');
  if (data.error) { main.appendChild(ui.el('div', { class: 'err-box' }, data.error)); return; }
  updateDebugBtn(data.debug);

  const online = data.health?.online;
  const grid = ui.el('div', { class: 'grid grid-4' });
  grid.appendChild(stat('服务状态', online ? '在线' : '离线',
    data.serve_url, online ? 'green' : 'red'));
  grid.appendChild(stat('标准模型', data.canonical_count, 'canonical models'));
  grid.appendChild(stat('接入源', data.endpoint_count, 'endpoints'));
  grid.appendChild(stat('Workers', data.worker_count, 'workers'));
  main.appendChild(grid);

  const g2 = ui.el('div', { class: 'grid grid-2', style: 'margin-top:16px' });

  // models + heads
  const body1 = ui.el('div');
  for (const [name, m] of Object.entries(data.models || {})) {
    const headType = name.includes('flash') ? 'flash' : 'pro';
    const head = headType === 'flash' ? data.flash_head : data.pro_head;
    const match = headType === 'flash' ? data.flash_match : data.pro_match;
    const rows = [
      ...ui.kvRows({ mode: m.mode, workers: (m.workers || []).join(', ') }),
      ui.el('div', { class: 'kv' }, ui.el('span', { class: 'kv-k' }, 'active head'),
        ui.el('span', { class: 'kv-v' }, head ? `${head.id} (${head.type})` : '— 未设置')),
    ];
    if (match) {
      rows.push(ui.el('div', { class: 'kv' }, ui.el('span', { class: 'kv-k' }, 'head 匹配'),
        ui.el('span', { class: 'kv-v' }, match.match === null ? '—' : (match.match ? '✓ 匹配' : '✗ 不匹配'))));
      if (match.issues?.length) {
        rows.push(ui.el('div', { class: 'kv' }, ui.el('span', { class: 'kv-k' }, '问题'),
          ui.el('span', { class: 'kv-v text-sm', style: 'max-width:260px' }, match.issues.join('; '))));
      }
    }
    body1.appendChild(ui.el('div', { class: 'mb' },
      ui.el('div', { class: 'flex aic gap mb' }, ui.el('strong', {}, name), ui.badge(m.mode, 'blue'))));
    body1.appendChild(...rows);
    body1.appendChild(ui.el('hr', { style: 'border:0;border-top:1px solid var(--border);margin:10px 0' }));
  }
  g2.appendChild(ui.card('对外模型与 Head', body1));

  // recent requests
  const reqs = data.recent_requests || [];
  const body2 = reqs.length ? ui.table(
    ['时间', '模型', '耗时', '状态'],
    reqs.slice(0, 10).map(r => [
      fmtTime(r.ts), r.model, (r.latency || 0) + 's',
      ui.badge(r.status || 'ok', r.status === 'error' ? 'red' : 'green'),
    ])) : ui.el('div', { class: 'empty' }, '暂无请求日志（启动 product_serve 后会记录）');
  g2.appendChild(ui.card('最近请求', body2));
  main.appendChild(g2);

  async function toggleDebug() {
    const cur = await ui.GET('/api/settings');
    const r = await ui.PUT('/api/settings', { debug: !cur.debug });
    ui.toast(r.error || `debug 已${r.debug ? '开启' : '关闭'}`, r.error ? 'err' : 'ok');
    updateDebugBtn(r.debug);
  }
  function updateDebugBtn(on) {
    const b = document.getElementById('btn-debug');
    if (b) { b.textContent = ''; b.appendChild(ui.icon('bolt')); b.appendChild(document.createTextNode(`Debug: ${on ? '开' : '关'}`)); }
  }
  async function reloadCfg() {
    const r = await ui.POST('/api/config/reload');
    ui.toast(r.error || `已重新加载 ${r.reloaded}`, r.error ? 'err' : 'ok');
    if (!r.error) ui.refresh();
  }
  async function snapshot() {
    const r = await ui.GET('/api/config/snapshot');
    if (r.error) { ui.toast(r.error, 'err'); return; }
    const text = r.yaml || JSON.stringify(r.raw, null, 2);
    const blob = new Blob([text], { type: r.yaml ? 'text/yaml' : 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = r.path ? r.path.split(/[/\\]/).pop() : 'fugu-snapshot.yaml';
    a.click(); URL.revokeObjectURL(a.href);
    ui.toast('已下载配置快照', 'ok');
  }
  function stat(label, value, sub, kind) {
    return ui.el('div', { class: 'stat' },
      ui.el('div', { class: 'stat-label' }, label),
      ui.el('div', { class: 'stat-value', style: kind === 'green' ? 'color:var(--green)' : kind === 'red' ? 'color:var(--red)' : '' }, String(value)),
      ui.el('div', { class: 'stat-sub' }, sub));
  }
  function fmtTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  }
}
