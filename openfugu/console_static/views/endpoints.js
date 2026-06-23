export async function viewEndpoints(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '接入源管理'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('新增接入源', { icon: 'plus', kind: 'primary', onClick: () => showForm() }))));

  const [epData, cmData] = await Promise.all([ui.GET('/api/endpoints'), ui.GET('/api/canonical-models')]);
  if (epData.error) { main.appendChild(ui.el('div', { class: 'err-box' }, epData.error)); return; }
  const cms = cmData.items || [];

  // group endpoints by canonical model to determine the current (primary) one
  const byCm = {};
  for (const e of (epData.items || [])) (byCm[e.canonical_model] ||= []).push(e);
  const currentByCm = {};
  for (const [cm, eps] of Object.entries(byCm)) {
    const enabled = eps.filter(e => e.enabled).sort((a, b) => a.priority - b.priority);
    if (enabled[0]) currentByCm[cm] = enabled[0].id;
  }

  const rows = (epData.items || []).map(e => [
    ui.el('div', {}, ui.el('strong', {}, e.id), ui.el('div', { class: 'text-faint text-mono text-sm' }, e.canonical_display)),
    ui.el('span', { class: 'mono' }, e.provider_model),
    ui.el('span', { class: 'mono text-sm' }, e.api_base_env || '—'),
    ui.el('span', { class: 'mono text-sm' }, e.api_key_env ? `${e.api_key_env} ****` : '—'),
    String(e.priority),
    String(e.cost || 0),
    healthCell(e),
    ui.el('div', { class: 'flex aic gap' },
      ui.dot(e.enabled),
      currentByCm[e.canonical_model] === e.id ? ui.badge('当前', 'green') : null),
    ui.el('div', { class: 'btn-row' },
      currentByCm[e.canonical_model] !== e.id ? ui.btn('设为当前', { sm: true, kind: 'primary', onClick: () => setCurrent(e) }) : null,
      ui.btn(e.enabled ? '禁用' : '启用', { sm: true, onClick: () => toggle(e) }),
      ui.btn('测试', { sm: true, icon: 'bolt', onClick: () => smoke(e) }),
      ui.btn('编辑', { sm: true, onClick: () => showForm(e) }),
      ui.btn('', { sm: true, kind: 'danger', icon: 'trash', onClick: () => del(e) })),
  ]);
  main.appendChild(ui.card('Endpoint 列表',
    rows.length ? ui.table(['Endpoint', 'provider_model', 'api_base_env', 'api_key_env', '优先级', '成本', '健康', '状态', '操作'], rows)
      : ui.el('div', { class: 'empty' }, '暂无接入源。密钥只保存环境变量名，不保存明文。')));

  function healthCell(e) {
    const h = e.health || {};
    if (h.ok == null) return ui.el('span', { class: 'text-faint text-sm' }, '未测试');
    return ui.el('div', { class: 'flex aic gap' },
      ui.badge(h.ok ? 'ok' : 'fail', h.ok ? 'green' : 'red'),
      ui.el('span', { class: 'mono text-sm' }, h.latency_ms == null ? '—' : `${h.latency_ms}ms`));
  }

  async function setCurrent(e) {
    const r = await ui.POST(`/api/endpoints/${e.id}/set-current`);
    ui.toast(r.error || `${e.id} 已设为当前 endpoint`, r.error ? 'err' : 'ok');
    ui.refresh();
  }
  async function toggle(e) {
    const r = await ui.PUT(`/api/endpoints/${e.id}`, { enabled: !e.enabled });
    ui.toast(r.error || '已更新', r.error ? 'err' : 'ok'); ui.refresh();
  }
  async function del(e) {
    if (!confirm(`删除接入源 ${e.id}？`)) return;
    const r = await ui.DEL(`/api/endpoints/${e.id}`);
    ui.toast(r.error || '已删除', r.error ? 'err' : 'ok'); ui.refresh();
  }
  async function smoke(e) {
    ui.toast('正在测试...', 'ok');
    const r = await ui.POST('/api/endpoints/smoke-test', { endpoint_id: e.id });
    if (r.ok) ui.toast(`✓ ${e.id} 延迟 ${r.latency_ms}ms`, 'ok');
    else ui.toast(`✗ ${e.id}: ${r.error}`, 'err');
  }
  function showForm(e = {}) {
    const ov = ui.el('div', { class: 'modal-overlay' });
    const m = ui.el('div', { class: 'modal' });
    const body = ui.el('div', { class: 'modal-body' });
    body.appendChild(field('ID', ui.el('input', { id: 'ep_id', value: e.id || '', placeholder: 'gpt_5_5_official', ...(e.id ? { disabled: true } : {}) }), '唯一标识'));
    const cmSel = ui.el('select', { id: 'ep_cm' });
    cmSel.appendChild(ui.el('option', { value: '' }, '— 选择标准模型 —'));
    cms.forEach(c => { const o = ui.el('option', { value: c.id }, `${c.display_name} (${c.id})`); if (c.id === e.canonical_model) o.selected = true; cmSel.appendChild(o); });
    body.appendChild(field('所属标准模型', cmSel));
    body.appendChild(field('provider_model', ui.el('input', { id: 'ep_pm', value: e.provider_model || '', placeholder: 'openai/gpt-5.5' }), 'litellm 模型 ID'));
    body.appendChild(ui.el('div', { class: 'field-grid' },
      field('api_base_env', ui.el('input', { id: 'ep_base', value: e.api_base_env || '', placeholder: 'OPENAI_API_BASE' })),
      field('api_key_env', ui.el('input', { id: 'ep_key', value: e.api_key_env || '', placeholder: 'OPENAI_API_KEY' }))));
    body.appendChild(ui.el('div', { class: 'warn-box' }, '不保存明文密钥，只保存环境变量名。密钥继续走环境变量。'));
    body.appendChild(ui.el('div', { class: 'field-grid' },
      field('优先级', ui.el('input', { id: 'ep_pri', type: 'number', value: e.priority ?? 10 })),
      field('成本', ui.el('input', { id: 'ep_cost', type: 'number', step: '0.001', value: e.cost ?? 0 }))));
    m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, e.id ? '编辑接入源' : '新增接入源'), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
    m.appendChild(body);
    m.appendChild(ui.el('div', { class: 'modal-foot' }, ui.btn('取消', { onClick: () => ov.remove() }), ui.btn('保存', { kind: 'primary', onClick: save })));
    ov.appendChild(m); document.body.appendChild(ov);
    function save() {
      const p = { canonical_model: v('ep_cm'), provider_model: v('ep_pm'), api_base_env: v('ep_base'),
        api_key_env: v('ep_key'), priority: +v('ep_pri'), cost: +v('ep_cost'), enabled: true };
      if (!e.id) p.id = v('ep_id');
      (e.id ? ui.PUT(`/api/endpoints/${e.id}`, p) : ui.POST('/api/endpoints', p))
        .then(r => { ui.toast(r.error || '已保存', r.error ? 'err' : 'ok'); ov.remove(); ui.refresh(); });
    }
    function v(id) { return document.getElementById(id)?.value || ''; }
  }
  function field(label, input, hint) {
    return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input, hint ? ui.el('div', { class: 'hint' }, hint) : null);
  }
}
