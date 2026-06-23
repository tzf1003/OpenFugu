export async function viewValidation(main, ui) {
  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '配置校验'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('重新校验', { icon: 'refresh', onClick: () => ui.refresh() }),
      ui.btn('下载快照', { icon: 'download', onClick: () => snapshot() }),
      ui.btn('重新加载', { icon: 'upload', onClick: () => reload() }),
      ui.btn('保存配置', { kind: 'primary', icon: 'bolt', onClick: () => save() }))));

  const holder = ui.el('div');
  main.appendChild(holder);
  holder.appendChild(ui.el('div', { class: 'empty' }, ui.el('span', { class: 'spinner' }), ' 校验中…'));
  load();

  async function load() {
    const [val, settings] = await Promise.all([ui.GET('/api/config/validate'), ui.GET('/api/settings')]);
    holder.innerHTML = '';
    if (val.error) { holder.appendChild(ui.el('div', { class: 'err-box' }, val.error)); return; }

    // verdict
    const verdict = ui.el('div', { class: val.valid ? 'ok-box' : 'err-box' },
      val.valid ? '✓ 配置校验通过，可以保存上线。' : `✗ 校验未通过：${val.errors.length} 个错误。`);
    holder.appendChild(verdict);

    // errors
    if (val.errors?.length) {
      const eb = ui.el('div', { class: 'card', style: 'margin-top:16px' });
      eb.appendChild(ui.el('div', { class: 'card-head' }, ui.el('h3', {}, `错误 (${val.errors.length})`)));
      const bb = ui.el('div', { class: 'card-body' });
      val.errors.forEach(e => bb.appendChild(ui.el('div', { class: 'err-box' }, e)));
      eb.appendChild(bb); holder.appendChild(eb);
    }
    // warnings
    if (val.warnings?.length) {
      const wb = ui.el('div', { class: 'card', style: 'margin-top:16px' });
      wb.appendChild(ui.el('div', { class: 'card-head' }, ui.el('h3', {}, `警告 (${val.warnings.length})`)));
      const bb = ui.el('div', { class: 'card-body' });
      val.warnings.forEach(w => bb.appendChild(ui.el('div', { class: 'warn-box' }, w)));
      wb.appendChild(bb); holder.appendChild(wb);
    }
    if (!val.errors?.length && !val.warnings?.length) {
      holder.appendChild(ui.el('div', { class: 'card', style: 'margin-top:16px' },
        ui.el('div', { class: 'card-body empty' }, '没有错误或警告。')));
    }

    // config path line
    const cfg = await ui.GET('/api/config');
    if (cfg && !cfg.error) {
      holder.appendChild(ui.el('div', { class: 'text-faint text-sm', style: 'margin-top:12px' },
        `配置文件：${cfg.config_path || '—'}`));
    }

    // settings editor
    if (!settings.error) renderSettings(settings);
  }

  function renderSettings(s) {
    const card = ui.el('div', { class: 'card', style: 'margin-top:16px' });
    card.appendChild(ui.el('div', { class: 'card-head' }, ui.el('h3', {}, '控制台设置')));
    const body = ui.el('div', { class: 'card-body' });
    body.appendChild(field('serve_url', ui.el('input', { id: 's_serve_url', value: s.serve_url || '' }), 'product_serve 地址，如 http://localhost:8090'));
    body.appendChild(field('config_path', ui.el('input', { id: 's_config_path', value: s.config_path || '' }), 'fugu.yaml 路径'));
    const dbg = ui.el('label', { class: 'flex aic gap', style: 'cursor:pointer;font-size:12px' },
      ui.el('input', { id: 's_debug', type: 'checkbox', ...(s.debug ? { checked: true } : {}) }), 'debug（默认暴露路由信息）');
    body.appendChild(dbg);
    body.appendChild(ui.el('div', { class: 'flex gap', style: 'margin-top:12px' },
      ui.btn('保存设置', { kind: 'primary', onClick: () => saveSettings() })));
    card.appendChild(body); holder.appendChild(card);
  }

  function field(label, input, hint) {
    return ui.el('div', { class: 'form-row' }, ui.el('label', {}, label), input,
      hint ? ui.el('div', { class: 'hint' }, hint) : null);
  }
  function v(id) { return document.getElementById(id)?.value ?? ''; }
  function chk(id) { return document.getElementById(id)?.checked ?? false; }

  async function save() {
    const r = await ui.POST('/api/config/save');
    ui.toast(r.error || `已保存到 ${r.saved}`, r.error ? 'err' : 'ok');
    if (!r.error) ui.refresh();
  }
  async function reload() {
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
  async function saveSettings() {
    const r = await ui.PUT('/api/settings', {
      serve_url: v('s_serve_url'), config_path: v('s_config_path'), debug: chk('s_debug'),
    });
    ui.toast(r.error || '设置已保存', r.error ? 'err' : 'ok');
  }
}
