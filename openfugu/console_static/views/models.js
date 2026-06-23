export async function viewModels(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '标准模型'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('接入向导', { icon: 'bolt', onClick: () => showWizard() }),
      ui.btn('新增标准模型', { icon: 'plus', kind: 'primary', onClick: () => showForm() }))));

  const data = await ui.GET('/api/canonical-models');
  if (data.error) { main.appendChild(ui.el('div', { class: 'err-box' }, data.error)); return; }

  const rows = (data.items || []).map(m => [
    ui.el('div', {}, ui.el('strong', {}, m.display_name), ui.el('div', { class: 'text-faint text-mono text-sm' }, m.id)),
    m.family || '—',
    (m.capabilities || []).map(c => ui.tag(c)),
    ui.el('div', { class: 'flex aic gap' },
      ui.badge(m.status, m.status === 'active' ? 'green' : m.status === 'draft' ? 'amber' : 'gray')),
    String(m.endpoint_count),
    String(m.worker_count),
    ui.el('div', { class: 'btn-row' },
      m.in_flash ? ui.badge('flash', 'blue') : null,
      m.in_pro ? ui.badge('pro', 'purple') : null,
      !m.in_flash ? ui.btn('+ flash', { sm: true, onClick: () => addToPool(m.id, 'flash') }) : ui.btn('- flash', { sm: true, kind: 'danger', onClick: () => removeFromPool(m.id, 'flash') }),
      !m.in_pro ? ui.btn('+ pro', { sm: true, onClick: () => addToPool(m.id, 'pro') }) : ui.btn('- pro', { sm: true, kind: 'danger', onClick: () => removeFromPool(m.id, 'pro') }),
      ui.btn('编辑', { sm: true, onClick: () => showForm(m) }),
      ui.btn('', { sm: true, kind: 'danger', icon: 'trash', onClick: () => del(m) })),
  ]);
  main.appendChild(ui.card('标准模型列表',
    rows.length ? ui.table(['模型', 'family', '能力', '状态', 'endpoints', 'workers', '操作'], rows)
      : ui.el('div', { class: 'empty' }, '暂无标准模型')));

  function showWizard() {
    const steps = [
      { title: '填写模型信息', desc: '设置 id、名称、family 和能力标签' },
      { title: '添加 Endpoint', desc: '至少添加一个 API 接入源' },
      { title: 'Smoke Test', desc: '验证 endpoint 可连通' },
      { title: 'Profile', desc: '选择数据集运行 profile' },
      { title: '训练 Head', desc: '训练或刷新分类器 head' },
      { title: '评估并启用', desc: '评估通过后启用上线' },
    ];
    let stepIdx = 0;
    const wiz = { cmId: '', model: {}, endpointId: '', profileTask: null, trainTask: null };
    const ov = ui.el('div', { class: 'modal-overlay' });
    const m = ui.el('div', { class: 'modal', style: 'width:600px' });
    ov.appendChild(m); document.body.appendChild(ov);
    renderStep();

    function renderStep() {
      m.innerHTML = '';
      // stepper header
      const head = ui.el('div', { class: 'modal-head' });
      head.appendChild(ui.el('h3', {}, '接入向导'));
      head.appendChild(ui.btn('', { icon: 'x', onClick: () => ov.remove() }));
      m.appendChild(head);
      const bar = ui.el('div', { class: 'wizard-bar' });
      steps.forEach((s, i) => {
        const dot = ui.el('div', { class: `wizard-dot ${i < stepIdx ? 'done' : ''} ${i === stepIdx ? 'active' : ''}` }, i < stepIdx ? '✓' : String(i + 1));
        bar.appendChild(dot);
        if (i < steps.length - 1) bar.appendChild(ui.el('div', { class: `wizard-line ${i < stepIdx ? 'done' : ''}` }));
      });
      const body = ui.el('div', { class: 'modal-body' });
      body.appendChild(bar);
      body.appendChild(ui.el('div', { class: 'wizard-title' }, steps[stepIdx].title));
      body.appendChild(ui.el('div', { class: 'text-dim text-sm mb' }, steps[stepIdx].desc));
      const content = ui.el('div', { id: 'wiz-content' });
      body.appendChild(content);
      m.appendChild(body);
      const foot = ui.el('div', { class: 'modal-foot' });
      if (stepIdx > 0) foot.appendChild(ui.btn('上一步', { onClick: () => { stepIdx--; renderStep(); } }));
      foot.appendChild(ui.el('div', { style: 'flex:1' }));
      if (stepIdx < steps.length - 1) foot.appendChild(ui.btn('下一步', { kind: 'primary', id: 'wiz-next', onClick: () => next() }));
      else foot.appendChild(ui.btn('完成', { kind: 'primary', onClick: () => ov.remove() }));
      m.appendChild(foot);
      renderContent(content);
    }

    async function renderContent(c) {
      c.innerHTML = '';
      if (stepIdx === 0) {
        c.appendChild(wField('ID', ui.el('input', { id: 'w_id', placeholder: 'gpt_5_5', value: wiz.model.id || '' })));
        c.appendChild(wField('显示名', ui.el('input', { id: 'w_dn', placeholder: 'GPT-5.5', value: wiz.model.display_name || '' })));
        c.appendChild(ui.el('div', { class: 'field-grid' },
          wField('family', ui.el('input', { id: 'w_fam', placeholder: 'openai', value: wiz.model.family || '' })),
          wField('vendor', ui.el('input', { id: 'w_vend', value: wiz.model.vendor || '' }))));
        c.appendChild(wField('能力标签 (逗号分隔)', ui.el('input', { id: 'w_cap', placeholder: 'reasoning, code', value: (wiz.model.capabilities || []).join(', ') })));
      } else if (stepIdx === 1) {
        if (!wiz.cmId) { c.appendChild(ui.el('div', { class: 'warn-box' }, '请先完成第一步创建模型')); return; }
        c.appendChild(wField('Endpoint ID', ui.el('input', { id: 'w_epid', placeholder: 'gpt_5_5_official' })));
        c.appendChild(wField('provider_model', ui.el('input', { id: 'w_eppm', placeholder: 'openai/gpt-5.5' })));
        c.appendChild(ui.el('div', { class: 'field-grid' },
          wField('api_base_env', ui.el('input', { id: 'w_epbase', placeholder: 'OPENAI_API_BASE' })),
          wField('api_key_env', ui.el('input', { id: 'w_epkey', placeholder: 'OPENAI_API_KEY' }))));
        c.appendChild(ui.el('div', { class: 'warn-box' }, '不保存明文密钥，只保存环境变量名。'));
        if (wiz.endpointId) c.appendChild(ui.el('div', { class: 'ok-box' }, `已创建 endpoint: ${wiz.endpointId}`));
      } else if (stepIdx === 2) {
        if (!wiz.endpointId) { c.appendChild(ui.el('div', { class: 'warn-box' }, '请先添加 endpoint')); return; }
        c.appendChild(ui.el('div', { class: 'text-dim text-sm' }, `点击测试 endpoint ${wiz.endpointId} 的连通性`));
        c.appendChild(ui.el('div', { class: 'mt' }, ui.btn('运行 Smoke Test', { icon: 'bolt', kind: 'primary', onClick: async () => {
          c.appendChild(ui.el('div', { class: 'text-sm mt', id: 'smoke-result' }, '测试中...'));
          const r = await ui.POST('/api/endpoints/smoke-test', { endpoint_id: wiz.endpointId });
          const el = document.getElementById('smoke-result');
          if (el) { el.textContent = ''; el.className = r.ok ? 'ok-box mt' : 'err-box mt'; el.textContent = r.ok ? `✓ 连通 · ${r.latency_ms}ms · ${r.reply}` : `✗ ${r.error}`; }
        } })));
      } else if (stepIdx === 3) {
        if (!wiz.cmId) { c.appendChild(ui.el('div', { class: 'warn-box' }, '请先完成前面的步骤')); return; }
        const ds = await ui.GET('/api/datasets');
        c.appendChild(wField('数据集', wSelect('w_pds', (ds.items || []).map(d => [d.path, d.path]), 'data/router_train.jsonl')));
        c.appendChild(wField('输出文件', ui.el('input', { id: 'w_pout', value: `data/worker_profile_${Date.now()}.jsonl` })));
        const lbl = ui.el('label', { class: 'flex aic gap', style: 'font-size:12px;cursor:pointer' },
          ui.el('input', { id: 'w_pfake', type: 'checkbox', checked: true }), '离线 (FakeCloudWorkerPool)');
        c.appendChild(lbl);
        c.appendChild(ui.el('div', { class: 'mt' }, ui.btn('启动 Profile', { icon: 'play', kind: 'primary', onClick: async () => {
          const r = await ui.POST('/api/tasks/profile', { model: 'openfugu-flash', dataset: wVal('w_pds'), out: wVal('w_pout'), fake: document.getElementById('w_pfake').checked });
          if (r.error) { ui.toast(r.error, 'err'); return; }
          wiz.profileTask = r; ui.toast(`profile 任务已启动: ${r.id}`, 'ok');
          c.appendChild(ui.el('div', { class: 'ok-box mt' }, `任务 ${r.id} 已启动，可在「Profile 与训练」页查看进度`));
        } })));
      } else if (stepIdx === 4) {
        const [ds, pr] = await Promise.all([ui.GET('/api/datasets'), ui.GET('/api/profiles')]);
        c.appendChild(wField('数据集', wSelect('w_tfds', (ds.items || []).map(d => [d.path, d.path]))));
        c.appendChild(wField('Profile 文件', wSelect('w_tfpr', (pr.items || []).map(p => [p.path, p.path]))));
        c.appendChild(wField('输出文件', ui.el('input', { id: 'w_tfout', value: `data/flash_head_${Date.now()}.npy` })));
        c.appendChild(ui.el('div', { class: 'mt' }, ui.btn('训练 Flash Head', { icon: 'play', kind: 'primary', onClick: async () => {
          const r = await ui.POST('/api/tasks/train-flash', { dataset: wVal('w_tfds'), profile: wVal('w_tfpr'), out: wVal('w_tfout'), no_backbone: true, iters: 20 });
          if (r.error) { ui.toast(r.error, 'err'); return; }
          wiz.trainTask = r; ui.toast(`训练任务已启动: ${r.id}`, 'ok');
          c.appendChild(ui.el('div', { class: 'ok-box mt' }, `任务 ${r.id} 已启动，完成后可在「Head 版本」页激活`));
        } })));
      } else if (stepIdx === 5) {
        const heads = await ui.GET('/api/heads');
        const flashHeads = (heads.items || []).filter(h => h.type === 'flash');
        c.appendChild(ui.el('div', { class: 'text-dim text-sm mb' }, '训练完成后，在 Head 版本管理页激活并启用模型。' + (wiz.cmId ? ` 记得将 ${wiz.cmId} 加入 openfugu-flash 候选池。` : '')));
        if (flashHeads.length) {
          c.appendChild(ui.table(['版本', 'shape', '分数', ''], flashHeads.map(h => [
            ui.el('span', { class: 'mono text-sm' }, h.id),
            h.shape ? `[${h.shape.join(', ')}]` : '—',
            h.eval?.router_score ?? '—',
            h.id === heads.active_flash ? ui.badge('已激活', 'green') : ui.btn('激活', { sm: true, kind: 'primary', onClick: async () => {
              const r = await ui.POST(`/api/heads/${h.id}/activate`, { type: 'flash' });
              ui.toast(r.error || '已激活', r.error ? 'err' : 'ok'); renderContent(c);
            } }),
          ])));
        } else {
          c.appendChild(ui.el('div', { class: 'empty' }, '暂无 flash head，请先训练'));
        }
        if (wiz.cmId) c.appendChild(ui.el('div', { class: 'mt' }, ui.btn('加入 flash 池', { kind: 'primary', onClick: async () => {
          const r = await ui.POST(`/api/canonical-models/${wiz.cmId}/add-to-pool`, { pool: 'flash' });
          ui.toast(r.error || '已加入', r.error ? 'err' : 'ok');
        } })));
      }
    }

    async function next() {
      if (stepIdx === 0) {
        const payload = {
          id: wVal('w_id'), display_name: wVal('w_dn'), family: wVal('w_fam'),
          vendor: wVal('w_vend'), capabilities: wVal('w_cap').split(',').map(s => s.trim()).filter(Boolean),
          status: 'draft',
        };
        if (!payload.id) { ui.toast('请填写 ID', 'err'); return; }
        const r = await ui.POST('/api/canonical-models', payload);
        if (r.error) { ui.toast(r.error, 'err'); return; }
        wiz.cmId = payload.id; wiz.model = payload;
        ui.toast('模型已创建', 'ok');
      } else if (stepIdx === 1) {
        if (wiz.endpointId) { stepIdx++; renderStep(); return; }
        const p = { canonical_model: wiz.cmId, provider_model: wVal('w_eppm'),
          api_base_env: wVal('w_epbase'), api_key_env: wVal('w_epkey'), priority: 10, enabled: true };
        p.id = wVal('w_epid');
        if (!p.id || !p.provider_model) { ui.toast('请填写 endpoint ID 和 provider_model', 'err'); return; }
        const r = await ui.POST('/api/endpoints', p);
        if (r.error) { ui.toast(r.error, 'err'); return; }
        wiz.endpointId = p.id; ui.toast('endpoint 已创建', 'ok');
      }
      stepIdx++; renderStep();
    }
    function wField(label, input) { return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input); }
    function wVal(id) { return document.getElementById(id)?.value || ''; }
    function wSelect(id, opts, def) {
      const s = ui.el('select', { id });
      opts.forEach(([v, l]) => { const o = ui.el('option', { value: v }, l); if (v === def) o.selected = true; s.appendChild(o); });
      return s;
    }
  }

  async function addToPool(id, pool) {
    const r = await ui.POST(`/api/canonical-models/${id}/add-to-pool`, { pool });
    ui.toast(r.error || `已加入 openfugu-${pool}`, r.error ? 'err' : 'ok');
    ui.refresh();
  }
  async function removeFromPool(id, pool) {
    const r = await ui.POST(`/api/canonical-models/${id}/remove-from-pool`, { pool });
    ui.toast(r.error || `已从 openfugu-${pool} 移除`, r.error ? 'err' : 'ok');
    ui.refresh();
  }
  async function del(m) {
    if (!confirm(`删除标准模型 ${m.display_name}？`)) return;
    const r = await ui.DEL(`/api/canonical-models/${m.id}`);
    ui.toast(r.error || '已删除', r.error ? 'err' : 'ok');
    ui.refresh();
  }
  function showForm(m = {}) {
    const overlay = ui.el('div', { class: 'modal-overlay' });
    const f = ui.el('div', { class: 'modal' });
    const body = ui.el('div', { class: 'modal-body' });
    body.appendChild(field('ID', ui.el('input', { value: m.id || '', placeholder: 'gpt_5_5', ...(m.id ? { disabled: true } : {}) }), '唯一标识，创建后不可改'));
    body.appendChild(field('显示名', ui.el('input', { id: 'f_dn', value: m.display_name || '', placeholder: 'GPT-5.5' })));
    body.appendChild(ui.el('div', { class: 'field-grid' },
      field('family', ui.el('input', { id: 'f_fam', value: m.family || '', placeholder: 'openai' })),
      field('vendor', ui.el('input', { id: 'f_vend', value: m.vendor || '' }))));
    body.appendChild(field('能力标签 (逗号分隔)', ui.el('input', { id: 'f_cap', value: (m.capabilities || []).join(', '), placeholder: 'reasoning, code' })));
    body.appendChild(ui.el('div', { class: 'field-grid' },
      field('状态', sel('f_st', ['draft', 'active', 'inactive'], m.status || 'draft')),
      field('说明', ui.el('input', { id: 'f_desc', value: m.description || '' }))));
    f.appendChild(ui.el('div', { class: 'modal-head' }, ui.el('h3', {}, m.id ? '编辑标准模型' : '新增标准模型'), closeBtn()));
    f.appendChild(body);
    f.appendChild(ui.el('div', { class: 'modal-foot' },
      ui.btn('取消', { onClick: () => overlay.remove() }),
      ui.btn('保存', { kind: 'primary', onClick: save })));
    overlay.appendChild(f);
    document.body.appendChild(overlay);

    function save() {
      const payload = {
        display_name: val('f_dn'), family: val('f_fam'), vendor: val('f_vend'),
        capabilities: val('f_cap').split(',').map(s => s.trim()).filter(Boolean),
        status: val('f_st'), description: val('f_desc'),
      };
      if (!m.id) payload.id = overlay.querySelector('input[placeholder="gpt_5_5"]')?.value?.trim() || '';
      (m.id ? ui.PUT(`/api/canonical-models/${m.id}`, payload) : ui.POST('/api/canonical-models', payload))
        .then(r => { ui.toast(r.error || '已保存', r.error ? 'err' : 'ok'); overlay.remove(); ui.refresh(); });
    }
    function closeBtn() { return ui.btn('', { icon: 'x', onClick: () => overlay.remove() }); }
  }
  function field(label, input, hint) {
    return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input, hint ? ui.el('div', { class: 'hint' }, hint) : null);
  }
  function sel(id, opts, val) {
    const s = ui.el('select', { id });
    opts.forEach(o => { const op = ui.el('option', { value: o }, o); if (o === val) op.selected = true; s.appendChild(op); });
    return s;
  }
  function val(id) { return document.getElementById(id)?.value || ''; }
}
