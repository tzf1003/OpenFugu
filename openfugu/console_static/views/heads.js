export async function viewHeads(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, 'Head 版本管理'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('回滚 flash', { icon: 'refresh', onClick: () => rollback('flash') }),
      ui.btn('回滚 pro', { icon: 'refresh', onClick: () => rollback('pro') }),
      ui.btn('注册 Head', { icon: 'plus', kind: 'primary', onClick: () => showForm() }),
      ui.btn('刷新', { icon: 'refresh', onClick: () => ui.refresh() }))));

  const data = await ui.GET('/api/heads');
  if (data.error) { main.appendChild(ui.el('div', { class: 'err-box' }, data.error)); return; }

  const rows = (data.items || []).map(h => {
    const activeFlash = data.active_flash === h.id;
    const activePro = data.active_pro === h.id;
    return [
      ui.el('div', {}, ui.el('strong', {}, h.id), ui.el('div', { class: 'text-faint text-sm' }, h.path || '—')),
      ui.badge(h.type, h.type === 'flash' ? 'blue' : 'purple'),
      h.shape ? ui.el('span', { class: 'mono text-sm' }, `[${h.shape.join(', ')}]`) : '—',
      (h.training_workers || []).length ? ui.el('span', { class: 'mono text-sm' }, `${h.training_workers.length} workers`) : '—',
      h.eval?.router_score != null ? String(h.eval.router_score) : '—',
      h.eval?.lift_pct != null ? `${h.eval.lift_pct > 0 ? '+' : ''}${h.eval.lift_pct}%` : '—',
      ui.el('div', { class: 'flex aic gap' },
        activeFlash ? ui.badge('flash active', 'green') : null,
        activePro ? ui.badge('pro active', 'green') : null,
        h.status === 'deprecated' ? ui.badge('deprecated', 'gray') : null),
      ui.el('div', { class: 'btn-row' },
        h.type === 'flash' && !activeFlash ? ui.btn('设为 flash', { sm: true, kind: 'primary', onClick: () => activate(h, 'flash') }) : null,
        h.type === 'pro' && !activePro ? ui.btn('设为 pro', { sm: true, kind: 'primary', onClick: () => activate(h, 'pro') }) : null,
        (activeFlash || activePro) ? ui.btn('下线', { sm: true, kind: 'danger', onClick: () => deactivate(h) }) : null,
        ui.btn('详情', { sm: true, onClick: () => showDetail(ui, h) }),
        ui.btn('', { sm: true, kind: 'danger', icon: 'trash', onClick: () => del(h) })),
    ];
  });
  main.appendChild(ui.card('Head 版本列表',
    rows.length ? ui.table(['版本', '类型', 'shape', '训练 workers', 'router 分数', 'lift', '状态', '操作'], rows)
      : ui.el('div', { class: 'empty' }, '暂无 head 版本')));

  main.appendChild(ui.el('div', { class: 'warn-box', style: 'margin-top:16px' },
    '上线规则：head 维度必须匹配当前 worker 池；worker 顺序必须一致或执行过映射迁移；评估分数不能低于 best single worker（除非人工强制）。'));

  async function activate(h, type) {
    const r = await ui.POST(`/api/heads/${h.id}/activate`, { type });
    ui.toast(r.error || `已设为 active ${type} head`, r.error ? 'err' : 'ok'); ui.refresh();
  }
  async function deactivate(h) {
    if (!confirm(`下线 ${h.id}？`)) return;
    const r = await ui.POST(`/api/heads/${h.id}/deactivate`);
    ui.toast(r.error || '已下线', r.error ? 'err' : 'ok'); ui.refresh();
  }
  async function rollback(type) {
    const r = await ui.POST('/api/heads/rollback', { type });
    ui.toast(r.error || `已回滚到 ${r.id}`, r.error ? 'err' : 'ok'); ui.refresh();
  }
  async function del(h) {
    if (!confirm(`删除 head 版本 ${h.id}？`)) return;
    const r = await ui.DEL(`/api/heads/${h.id}`);
    ui.toast(r.error || '已删除', r.error ? 'err' : 'ok'); ui.refresh();
  }
  function showForm() {
    const ov = ui.el('div', { class: 'modal-overlay' });
    const m = ui.el('div', { class: 'modal' });
    const body = ui.el('div', { class: 'modal-body' });
    body.appendChild(field('类型', sel('h_type', ['flash', 'pro'])));
    body.appendChild(field('文件路径 (项目相对)', ui.el('input', { id: 'h_path', placeholder: 'data/flash_head.npy' })));
    body.appendChild(field('训练 workers (逗号分隔)', ui.el('input', { id: 'h_tw', placeholder: 'gpt_5_5,gemini_3_5_flash' })));
    body.appendChild(field('数据集', ui.el('input', { id: 'h_ds', placeholder: 'data/router_train.jsonl' })));
    m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, '注册 Head'), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
    m.appendChild(body);
    m.appendChild(ui.el('div', { class: 'modal-foot' }, ui.btn('取消', { onClick: () => ov.remove() }),
      ui.btn('注册', { kind: 'primary', onClick: () => {
        ui.POST('/api/heads', { type: v('h_type'), path: v('h_path'),
          training_workers: v('h_tw').split(',').map(s => s.trim()).filter(Boolean), dataset: v('h_ds') })
          .then(r => { ui.toast(r.error || '已注册', r.error ? 'err' : 'ok'); ov.remove(); ui.refresh(); });
      } })));
    ov.appendChild(m); document.body.appendChild(ov);
  }
  function field(label, input) { return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input); }
  function sel(id, opts) { const s = ui.el('select', { id }); opts.forEach(o => s.appendChild(ui.el('option', { value: o }, o))); return s; }
  function v(id) { return document.getElementById(id)?.value || ''; }
}

async function showDetail(ui, h) {
  const ov = ui.el('div', { class: 'modal-overlay' });
  const m = ui.el('div', { class: 'modal', style: 'width:600px' });
  const body = ui.el('div', { class: 'modal-body' });
  body.appendChild(...ui.kvRows({ id: h.id, type: h.type, path: h.path, shape: h.shape ? `[${h.shape.join(', ')}]` : '', n_workers: h.n_workers, dataset: h.dataset, profile: h.profile, status: h.status, note: h.note }));
  if (h.training_workers?.length) {
    body.appendChild(ui.el('div', { class: 'mt' }, ui.el('div', { class: 'text-dim text-sm mb' }, '训练 worker 顺序:'),
      ui.el('div', { class: 'mono text-sm' }, h.training_workers.map((w, i) => `${i}:${w}`).join('  →  '))));
  }
  if (h.eval && Object.keys(h.eval).length) {
    body.appendChild(ui.el('div', { class: 'mt' }, ui.el('div', { class: 'text-dim text-sm mb' }, '评估结果:'),
      ...ui.kvRows(h.eval)));
  }
  if (h.params && Object.keys(h.params).length) {
    body.appendChild(ui.el('div', { class: 'mt' }, ui.el('div', { class: 'text-dim text-sm mb' }, '训练参数:'),
      ui.el('div', { class: 'pre' }, JSON.stringify(h.params, null, 2))));
  }
  m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, h.id), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
  m.appendChild(body); ov.appendChild(m); document.body.appendChild(ov);
}
