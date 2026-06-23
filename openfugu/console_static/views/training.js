export async function viewTraining(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, 'Profile 与分类器训练'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('刷新', { icon: 'refresh', onClick: () => ui.refresh() }))));

  const [ds, pr, tasks] = await Promise.all([
    ui.GET('/api/datasets'), ui.GET('/api/profiles'), ui.GET('/api/tasks')]);
  if (ds.error) { main.appendChild(ui.el('div', { class: 'err-box' }, ds.error)); return; }

  const g = ui.el('div', { class: 'grid grid-3' });

  // datasets
  g.appendChild(ui.card('数据集', (ds.items || []).length ? ui.table(['文件', '大小'], (ds.items || []).map(d => [
    ui.el('span', { class: 'mono text-sm' }, d.path), fmtSize(d.size)])) : ui.el('div', { class: 'empty' }, '无数据集')));

  // profiles
  const prRows = (pr.items || []).map(p => [
    ui.el('a', { href: '#training', onClick: () => showProfile(ui, p.path), class: 'mono text-sm', style: 'color:var(--accent-2)' }, p.path),
    fmtSize(p.size),
    ui.btn('摘要', { sm: true, onClick: () => showProfile(ui, p.path) }),
  ]);
  g.appendChild(ui.card('Profile 文件', prRows.length ? ui.table(['文件', '大小', ''], prRows) : ui.el('div', { class: 'empty' }, '无 profile')));
  main.appendChild(g);

  // launch panel
  const launchBody = ui.el('div', { class: 'grid grid-3' });
  launchBody.appendChild(launchCard('Profile', '对 worker 池跑 profile', [
    sel('p_model', ['openfugu-flash', 'openfugu-pro']),
    selDs('p_ds', ds.items),
    inp('p_out', 'data/worker_profile_new.jsonl'),
    chk('p_fake', '离线 (FakeCloudWorkerPool)', true),
  ], () => launch('profile')));
  launchBody.appendChild(launchCard('训练 Flash Head', 'CMA-ES 优化 per-question 路由', [
    selDs('tf_ds', ds.items),
    selPr('tf_pr', pr.items),
    inp('tf_out', 'data/flash_head_new.npy'),
    numInp('tf_iters', 20, '迭代次数'),
    chk('tf_nb', '无 backbone (hash 特征)', true),
  ], () => launch('train-flash')));
  launchBody.appendChild(launchCard('训练 Pro Head', 'per-step 多轮路由 head', [
    selDs('tp_ds', ds.items),
    inp('tp_out', 'data/pro_head_new.npy'),
    numInp('tp_iters', 6, '迭代次数'),
    chk('tp_fake', '离线 (FakeCloudWorkerPool)', true),
  ], () => launch('train-pro')));
  main.appendChild(ui.el('div', { style: 'margin-top:16px' }, ui.card('启动任务', launchBody)));

  // task list
  const taskRows = (tasks.items || []).slice().reverse().map(t => [
    ui.el('div', {}, ui.el('strong', {}, t.type), ui.el('div', { class: 'text-faint text-sm' }, t.id)),
    taskBadge(t.status),
    fmtTime(t.started_at),
    t.finished_at ? fmtTime(t.finished_at) : '—',
    t.eval?.best_single != null ? String(t.eval.best_single) : '—',
    t.eval?.oracle != null ? String(t.eval.oracle) : '—',
    t.eval?.router_score != null ? String(t.eval.router_score) : '—',
    t.eval?.lift_pct != null
      ? ui.el('span', { class: t.eval.lift_pct >= 0 ? 'mono' : 'mono', style: `color:${t.eval.lift_pct >= 0 ? 'var(--green)' : 'var(--red)'}` },
          `${t.eval.lift_pct >= 0 ? '+' : ''}${t.eval.lift_pct}%`)
      : '—',
    ui.el('div', { class: 'btn-row' },
      ui.btn('日志', { sm: true, onClick: () => showLog(ui, t) }),
      t.status === 'running' ? ui.btn('停止', { sm: true, kind: 'danger', onClick: () => stopTask(t.id) }) : null),
  ]);
  main.appendChild(ui.el('div', { style: 'margin-top:16px' },
    ui.card('任务历史', taskRows.length ? ui.table(['任务', '状态', '开始', '结束', 'best single', 'oracle', 'router', 'lift', '操作'], taskRows)
      : ui.el('div', { class: 'empty' }, '暂无任务'))));

  async function launch(kind) {
    let body, url;
    if (kind === 'profile') {
      url = '/api/tasks/profile';
      body = { model: v('p_model'), dataset: v('p_ds'), out: v('p_out'), fake: chkVal('p_fake') };
    } else if (kind === 'train-flash') {
      url = '/api/tasks/train-flash';
      body = { dataset: v('tf_ds'), profile: v('tf_pr'), out: v('tf_out'), iters: +v('tf_iters'), no_backbone: chkVal('tf_nb') };
    } else {
      url = '/api/tasks/train-pro';
      body = { dataset: v('tp_ds'), out: v('tp_out'), iters: +v('tp_iters'), fake: chkVal('tp_fake') };
    }
    const r = await ui.POST(url, body);
    ui.toast(r.error || `任务已启动: ${r.id}`, r.error ? 'err' : 'ok');
    ui.refresh();
  }
  async function stopTask(id) {
    const r = await ui.POST(`/api/tasks/${id}/stop`); ui.toast(r.stopped ? '已发送停止' : '无法停止', r.stopped ? 'ok' : 'err'); ui.refresh();
  }
  function launchCard(title, sub, fields, onRun) {
    const body = ui.el('div');
    body.appendChild(ui.el('div', { class: 'text-dim text-sm mb' }, sub));
    fields.forEach(f => body.appendChild(f));
    body.appendChild(ui.el('div', { class: 'mt' }, ui.btn('运行', { icon: 'play', kind: 'primary', onClick: onRun })));
    return ui.el('div', { class: 'card' }, ui.el('div', { class: 'card-body' }, ui.el('h3', { style: 'font-size:13px;margin-bottom:8px' }, title), body));
  }
  function sel(id, opts) { const s = ui.el('select', { id, style: 'width:auto' }); opts.forEach(o => { s.appendChild(ui.el('option', { value: o }, o)); }); wrapField(id, s); return s; }
  function selDs(id, items) { const s = ui.el('select', { id }); (items || []).forEach(d => s.appendChild(ui.el('option', { value: d.path }, d.path))); return wrapField(id, s); }
  function selPr(id, items) { const s = ui.el('select', { id }); (items || []).forEach(p => s.appendChild(ui.el('option', { value: p.path }, p.path))); return wrapField(id, s); }
  function inp(id, val) { const i = ui.el('input', { id, value: val }); return wrapField(id, i); }
  function numInp(id, val, label) { const i = ui.el('input', { id, type: 'number', value: val, style: 'width:80px' }); return wrapField(label, i); }
  function chk(id, label, val) { const c = ui.el('input', { id, type: 'checkbox' }); c.checked = val; const w = ui.el('label', { style: 'display:flex;align-items:center;gap:6px;font-size:11.5px;margin-bottom:8px' }, c, label); return w; }
  function wrapField(label, input) { const w = ui.el('div', { class: 'form-row', style: 'margin-bottom:8px' }, ui.el('label', { style: 'font-size:10.5px' }, label), input); return w; }
  function v(id) { const e = document.getElementById(id); return e ? (e.type === 'checkbox' ? e.checked : e.value) : ''; }
  function chkVal(id) { return document.getElementById(id)?.checked ?? false; }
  function taskBadge(s) { return ui.badge(s, s === 'done' ? 'green' : s === 'failed' ? 'red' : s === 'running' ? 'blue' : 'amber'); }
  function fmtSize(n) { if (n > 1e6) return (n / 1e6).toFixed(1) + 'MB'; if (n > 1e3) return (n / 1e3).toFixed(0) + 'KB'; return n + 'B'; }
  function fmtTime(ts) { if (!ts) return '—'; return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }); }
}

async function showProfile(ui, path) {
  const r = await ui.GET(`/api/profiles/${path}/summary`);
  const ov = ui.el('div', { class: 'modal-overlay' });
  const m = ui.el('div', { class: 'modal' });
  const body = ui.el('div', { class: 'modal-body' });
  body.appendChild(ui.el('div', { class: 'text-dim text-sm mb' }, `path: ${path} · records: ${r.records ?? '—'} · overall: ${r.overall_mean ?? '—'}`));
  if (r.workers) {
    body.appendChild(ui.table(['Worker', 'n', '均分', '均延迟', '错误'], Object.entries(r.workers).map(([w, d]) => [
      ui.el('span', { class: 'mono' }, w), d.n, d.mean_score, d.mean_latency ?? '—', d.errors])));
  } else if (r.error) { body.appendChild(ui.el('div', { class: 'err-box' }, r.error)); }
  m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, 'Profile 摘要'), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
  m.appendChild(body); ov.appendChild(m); document.body.appendChild(ov);
}

async function showLog(ui, t) {
  const r = await ui.GET(`/api/tasks/${t.id}`);
  const ov = ui.el('div', { class: 'modal-overlay' });
  const m = ui.el('div', { class: 'modal', style: 'width:720px' });
  const body = ui.el('div', { class: 'modal-body' });
  if (r.eval && Object.keys(r.eval).length) {
    body.appendChild(ui.el('div', { class: 'ok-box' }, Object.entries(r.eval).map(([k, v]) => `${k}: ${v}`).join(' · ')));
  }
  body.appendChild(ui.el('div', { class: 'text-faint text-sm mono mb' }, r.command));
  body.appendChild(ui.el('div', { class: 'pre' }, r.log || '(无输出)'));
  m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, `任务日志 · ${t.type}`), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
  m.appendChild(body); ov.appendChild(m); document.body.appendChild(ov);
}
