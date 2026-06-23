export async function viewPlayground(main, ui) {
  const state = { model: 'openfugu-flash', debug: false, sending: false };

  main.appendChild(ui.el('div', { class: 'topbar' },
    ui.el('h1', {}, '调用台'),
    ui.el('div', { class: 'topbar-actions' },
      ui.btn('清空', { icon: 'trash', onClick: () => { state.log = []; renderLog(); } }))));

  // control bar
  const bar = ui.el('div', { class: 'card', style: 'margin-bottom:16px' });
  const barBody = ui.el('div', { class: 'card-body', style: 'display:flex;gap:12px;flex-wrap:wrap;align-items:center' });
  const modelSel = ui.el('select', { id: 'pg_model' });
  ['openfugu-flash', 'openfugu-pro'].forEach(m => {
    const o = ui.el('option', { value: m }, m); modelSel.appendChild(o);
  });
  modelSel.addEventListener('change', () => { state.model = modelSel.value; });
  barBody.appendChild(ui.el('div', {}, ui.el('div', { class: 'text-dim text-sm', style: 'margin-bottom:4px' }, '模型'), modelSel));
  const dbg = ui.el('label', { class: 'flex aic gap', style: 'cursor:pointer;font-size:12px' },
    ui.el('input', { id: 'pg_debug', type: 'checkbox' }), 'debug（暴露路由与 trace）');
  dbg.querySelector('input').addEventListener('change', e => { state.debug = e.target.checked; });
  barBody.appendChild(dbg);
  barBody.appendChild(ui.el('div', { class: 'text-faint text-sm', style: 'margin-left:auto' },
    '调用经 product_serve 的 /v1/chat/completions 转发。'));
  bar.appendChild(barBody);
  main.appendChild(bar);

  // chat area
  const chatWrap = ui.el('div', { class: 'card' });
  const chatHead = ui.el('div', { class: 'card-head' }, ui.el('h3', {}, '对话'));
  chatWrap.appendChild(chatHead);
  const chatBody = ui.el('div', { class: 'card-body', id: 'pg_chat', style: 'min-height:200px;max-height:46vh;overflow-y:auto' });
  chatWrap.appendChild(chatBody);
  main.appendChild(chatWrap);

  // composer
  const comp = ui.el('div', { class: 'card', style: 'margin-top:16px' });
  const compBody = ui.el('div', { class: 'card-body' });
  const ta = ui.el('textarea', { id: 'pg_input', rows: 3, placeholder: '输入消息…（Ctrl+Enter 发送）' });
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  compBody.appendChild(ta);
  compBody.appendChild(ui.el('div', { class: 'flex jcb aic', style: 'margin-top:10px' },
    ui.el('div', { class: 'text-faint text-sm', id: 'pg_status' }, '就绪'),
    ui.btn('发送', { kind: 'primary', icon: 'play', onClick: () => send() })));
  comp.appendChild(compBody);
  main.appendChild(comp);

  state.log = [];
  state.chatBody = chatBody;

  function renderLog() {
    chatBody.innerHTML = '';
    if (!state.log.length) {
      chatBody.appendChild(ui.el('div', { class: 'empty' }, '发送一条消息开始对话。'));
      return;
    }
    for (const e of state.log) chatBody.appendChild(e);
    chatBody.scrollTop = chatBody.scrollHeight;
  }
  renderLog();

  async function send() {
    if (state.sending) return;
    const text = ta.value.trim();
    if (!text) return;
    state.sending = true;
    const sendBtn = comp.querySelector('button.btn-primary');
    if (sendBtn) sendBtn.disabled = true;
    document.getElementById('pg_status').textContent = '发送中…';
    ta.value = '';
    const userMsg = ui.el('div', { class: 'chat-msg chat-user' }, text);
    state.log.push(userMsg);
    renderLog();
    const t0 = performance.now();
    try {
      const resp = await ui.POST('/api/playground/chat', {
        model: state.model, debug: state.debug,
        messages: [{ role: 'user', content: text }],
      });
      const lat = resp._latency_ms != null ? resp._latency_ms : Math.round(performance.now() - t0);
      if (resp.error) {
        state.log.push(ui.el('div', { class: 'chat-msg chat-bot' }, '⚠ ' + resp.error));
      } else {
        const answer = resp.choices?.[0]?.message?.content || '(空回复)';
        const usage = resp.usage || {};
        const bot = ui.el('div', { class: 'chat-msg chat-bot' }, answer);
        const meta = ui.el('div', { class: 'chat-trace' },
          `模型 ${resp.model || state.model} · ${lat}ms · turns ${usage.fugu_turns ?? '—'}` +
          (usage.fugu_route_reason ? ` · ${usage.fugu_route_reason}` : '') +
          (usage.fugu_selected_worker ? ` · worker=${usage.fugu_selected_worker}` : '') +
          (usage.fugu_terminated_by ? ` · ${usage.fugu_terminated_by}` : ''));
        bot.appendChild(meta);
        if (state.debug && usage.fugu_trace?.length) {
          bot.appendChild(traceBlock(ui, usage.fugu_trace));
        }
        state.log.push(bot);
      }
      document.getElementById('pg_status').textContent = `完成 · ${lat}ms`;
    } catch (e) {
      state.log.push(ui.el('div', { class: 'chat-msg chat-bot' }, '⚠ 请求失败: ' + e));
      document.getElementById('pg_status').textContent = '失败';
    } finally {
      state.sending = false;
      if (sendBtn) sendBtn.disabled = false;
      renderLog();
    }
  }
}

function traceBlock(ui, trace) {
  const wrap = ui.el('div', { class: 'mt' });
  wrap.appendChild(ui.el('div', { class: 'text-dim text-sm', style: 'margin-bottom:4px' }, `per-step trace (${trace.length} turns)`));
  wrap.appendChild(ui.table(['turn', 'agent', 'role', 'worker', 'reply'],
    trace.map(t => [
      String(t.turn ?? ''), String(t.agent_id ?? ''), ui.tag(t.role || ''),
      ui.el('span', { class: 'mono text-sm' }, t.worker || '—'),
      ui.el('span', { class: 'text-sm' }, t.reply || ''),
    ])));
  return wrap;
}
