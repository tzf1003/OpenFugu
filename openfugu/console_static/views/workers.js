export async function viewWorkers(main, ui) {
  let activePool = 'openfugu-flash';
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, 'Worker 池管理'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('刷新', { icon: 'refresh', onClick: () => render(activePool) }))));

  const tabs = ui.el('div', { class: 'tabs' });
  const tabFlash = ui.el('div', { class: 'tab active' }, 'openfugu-flash');
  const tabPro = ui.el('div', { class: 'tab' }, 'openfugu-pro');
  tabs.appendChild(tabFlash); tabs.appendChild(tabPro);
  main.appendChild(tabs);
  const container = ui.el('div');
  main.appendChild(container);

  tabFlash.addEventListener('click', () => { activePool = 'openfugu-flash'; setActive(); render(activePool); });
  tabPro.addEventListener('click', () => { activePool = 'openfugu-pro'; setActive(); render(activePool); });
  function setActive() {
    tabFlash.classList.toggle('active', activePool === 'openfugu-flash');
    tabPro.classList.toggle('active', activePool === 'openfugu-pro');
  }

  await render(activePool);

  async function render(pool) {
    container.innerHTML = '';
    const [wData, cmp] = await Promise.all([
      ui.GET('/api/workers', { model: pool }), ui.GET('/api/workers/head-compare', { model: pool })]);
    if (wData.error) { container.appendChild(ui.el('div', { class: 'err-box' }, wData.error)); return; }

    // head comparison warning
    if (cmp && cmp.issues?.length) {
      const box = ui.el('div', { class: 'warn-box' });
      box.appendChild(ui.el('strong', {}, 'Head 匹配警告： '));
      box.appendChild(document.createTextNode(cmp.issues.join('； ')));
      container.appendChild(box);
    } else if (cmp && cmp.head && cmp.match) {
      container.appendChild(ui.el('div', { class: 'ok-box' }, `当前 worker 池与 ${cmp.head.id} 训练时一致`));
    } else if (cmp && !cmp.head) {
      container.appendChild(ui.el('div', { class: 'warn-box' }, '未设置 active head，无法比对 worker 顺序'));
    }

    // worker list with drag-to-reorder
    const list = ui.el('div', { style: 'margin-top:12px' });
    const order = [...(wData.order || [])];
    const items = wData.items || [];
    const byId = Object.fromEntries(items.map(w => [w.id, w]));
    order.forEach((wid, idx) => {
      const w = byId[wid] || { id: wid };
      const row = ui.el('div', { class: 'worker-row', draggable: 'true', 'data-idx': idx });
      row.appendChild(ui.el('span', { class: 'drag-handle' }, '⣿'));
      row.appendChild(ui.el('span', { class: 'text-faint mono', style: 'width:24px' }, String(idx)));
      row.appendChild(ui.el('div', { style: 'flex:1' },
        ui.el('div', { class: 'flex aic gap' }, ui.el('strong', {}, w.display_name || w.id), ui.dot(w.enabled),
          ...(w.tags || []).map(t => ui.tag(t))),
        ui.el('div', { class: 'text-faint text-sm mono' }, `${w.canonical_display || w.canonical_model || '—'} · ${w.primary_provider_model || w.provider_model || '—'}`)));
      row.appendChild(ui.el('div', { class: 'flex aic gap', style: 'font-size:11px' },
        ui.el('span', { class: 'text-faint' }, 'policy'),
        policySel(w, () => save(w.id, row)),
        ui.el('span', { class: 'text-faint' }, 'tok'),
        numInput(w.max_tokens, v => { w.max_tokens = v; save(w.id, row); }),
        ui.el('span', { class: 'text-faint' }, 'temp'),
        numInput(w.temperature, v => { w.temperature = v; save(w.id, row); }, 0.1)));
      row.appendChild(ui.el('div', { class: 'btn-row' },
        ui.btn(w.enabled ? '禁用' : '启用', { sm: true, onClick: () => toggleW(w) }),
        ui.btn('编辑', { sm: true, onClick: () => editForm(w) })));
      list.appendChild(row);
      dragSetup(row, list, order, () => saveOrder(order, pool));
    });
    const card = ui.card(`${pool} · ${order.length} workers`, list,
      ui.el('div', { class: 'flex aic gap' },
        ui.btn('保存顺序', { icon: 'upload', onClick: () => saveOrder(order, pool) }),
        ui.el('span', { class: 'text-faint text-sm' }, '拖拽行可调整顺序，head 依赖此顺序')));
    container.appendChild(card);
    container.appendChild(ui.el('div', { class: 'warn-box', style: 'margin-top:12px' },
      '危险：改 worker 顺序会让旧 head 的行语义错位；新增/删除 worker 后旧 flash head 维度会不匹配；pro head 可取前 n 个 agent rows，但语义仍依赖训练时的 worker 排列。'));

    function policySel(w, after) {
      const s = ui.el('select', { class: 'text-sm', style: 'width:auto' });
      ['fixed', 'cheapest_healthy', 'fastest_healthy', 'priority', 'weighted', ''].forEach(p => {
        const o = ui.el('option', { value: p }, p || '—'); if (p === w.endpoint_policy) o.selected = true; s.appendChild(o);
      });
      s.addEventListener('change', () => { w.endpoint_policy = s.value; after(); });
      return s;
    }
    function numInput(val, cb, step = 1) {
      const i = ui.el('input', { type: 'number', step: step, value: val, style: 'width:64px' });
      i.addEventListener('change', () => cb(+i.value)); return i;
    }
    async function save(wid, row) {
      const w = byId[wid]; if (!w) return;
      const r = await ui.PUT(`/api/workers/${wid}`, {
        endpoint_policy: w.endpoint_policy, max_tokens: w.max_tokens, temperature: w.temperature });
      ui.toast(r.error || '已保存', r.error ? 'err' : 'ok');
    }
    async function toggleW(w) {
      const r = await ui.PUT(`/api/workers/${w.id}`, { enabled: !w.enabled });
      ui.toast(r.error || '已更新', r.error ? 'err' : 'ok'); render(pool);
    }
  }

  function dragSetup(row, list, order, after) {
    let dragIdx = null;
    row.addEventListener('dragstart', () => { dragIdx = +row.dataset.idx; row.classList.add('dragging'); });
    row.addEventListener('dragend', () => { row.classList.remove('dragging'); });
    row.addEventListener('dragover', e => { e.preventDefault(); });
    row.addEventListener('drop', e => {
      e.preventDefault();
      const dropIdx = +row.dataset.idx;
      if (dragIdx === null || dragIdx === dropIdx) return;
      const [m] = order.splice(dragIdx, 1);
      order.splice(dropIdx, 0, m);
      after();
    });
  }
  async function saveOrder(order, pool) {
    const r = await ui.PUT('/api/workers/order', { model: pool, order });
    ui.toast(r.error || '顺序已保存', r.error ? 'err' : 'ok'); render(pool);
  }
  function editForm(w) {
    const ov = ui.el('div', { class: 'modal-overlay' });
    const m = ui.el('div', { class: 'modal' });
    const body = ui.el('div', { class: 'modal-body' });
    body.appendChild(field('显示名', ui.el('input', { id: 'w_dn', value: w.display_name || '' })));
    body.appendChild(field('canonical_model', ui.el('input', { id: 'w_cm', value: w.canonical_model || '' })));
    body.appendChild(field('endpoint_policy', sel('w_ep', ['fixed', 'cheapest_healthy', 'fastest_healthy', 'priority', 'weighted'], w.endpoint_policy)));
    body.appendChild(field('fixed_endpoint', ui.el('input', { id: 'w_fe', value: w.fixed_endpoint || '', placeholder: 'policy=fixed 时指定 endpoint id' })));
    body.appendChild(field('tags (逗号分隔)', ui.el('input', { id: 'w_tags', value: (w.tags || []).join(', ') })));
    m.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, `编辑 ${w.id}`), ui.btn('', { icon: 'x', onClick: () => ov.remove() })));
    m.appendChild(body);
    m.appendChild(ui.el('div', { class: 'modal-foot' }, ui.btn('取消', { onClick: () => ov.remove() }),
      ui.btn('保存', { kind: 'primary', onClick: () => {
        ui.PUT(`/api/workers/${w.id}`, {
          display_name: v('w_dn'), canonical_model: v('w_cm'), endpoint_policy: v('w_ep'),
          fixed_endpoint: v('w_fe'), tags: v('w_tags').split(',').map(s => s.trim()).filter(Boolean),
        }).then(r => { ui.toast(r.error || '已保存', r.error ? 'err' : 'ok'); ov.remove(); render(activePool); });
      } })));
    ov.appendChild(m); document.body.appendChild(ov);
  }
  function field(label, input) { return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input); }
  function sel(id, opts, val) { const s = ui.el('select', { id }); opts.forEach(o => { const op = ui.el('option', { value: o }, o); if (o === val) op.selected = true; s.appendChild(op); }); return s; }
  function v(id) { return document.getElementById(id)?.value || ''; }
}
