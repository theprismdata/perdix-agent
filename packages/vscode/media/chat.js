/* 서버·키·파일 접근은 확장 호스트에서만 처리한다. 콘텐츠는 HTML로 해석하지 않는다. */
(() => {
  const vscode = acquireVsCodeApi();
  const byId = (id) => document.getElementById(id);
  const send = (type, extra = {}) => vscode.postMessage({ type, ...extra });
  const messages = byId('messages');
  const tools = new Map();
  const approvals = new Map();
  let chunk = null;
  let chunkKind = null;
  let busy = false;
  let connected = false;
  const draft = vscode.getState();
  byId('prompt').value = draft?.draft || '';
  byId('prompt').addEventListener('input', () => vscode.setState({ draft: byId('prompt').value }));
  const welcome = byId('welcome');
  function scroll() { messages.scrollTop = messages.scrollHeight; }
  function row(label, text, kind = 'agent') {
    welcome.hidden = true;
    const box = document.createElement('article'); box.className = 'message ' + kind;
    const title = document.createElement('div'); title.className = 'label'; title.textContent = label;
    const body = document.createElement('div'); body.className = 'text'; body.textContent = text;
    box.append(title, body); messages.append(box); return body;
  }
  function event(e) {
    if (e.type === 'agent_text' || e.type === 'agent_thought' || e.type === 'user_text') {
      if (!chunk || chunkKind !== e.type) {
        chunk = row(e.type === 'user_text' ? '나' : e.type === 'agent_thought' ? '생각 중' : 'Perdix', '', e.type === 'user_text' ? 'user' : 'agent');
        chunkKind = e.type;
      }
      chunk.textContent += e.text; scroll(); return;
    }
    if (!['usage', 'model_changed', 'mode_changed'].includes(e.type)) { chunk = null; chunkKind = null; }
    if (e.type === 'user_prompt') row('나', e.text, 'user');
    if (e.type === 'error') row('문제가 발생했습니다', e.message, 'error');
    if (e.type === 'server_exit') row('연결 종료', '서버 연결이 종료되었습니다. 새 대화를 시작하거나 서버 설정을 확인하세요.', 'error');
    if (e.type === 'tool_call' || e.type === 'tool_call_update') {
      welcome.hidden = true;
      let card = tools.get(e.toolCallId);
      if (!card) {
        const details = document.createElement('details'); const title = document.createElement('summary'); const content = document.createElement('pre');
        details.append(title, content); messages.append(details);
        card = { title, content, name: e.title || e.toolCallId, status: e.status || 'pending' }; tools.set(e.toolCallId, card);
      }
      if (e.title) card.name = e.title;
      if (e.status) card.status = e.status;
      const labels = { pending: '대기', in_progress: '실행 중', completed: '완료', failed: '실패' };
      card.title.textContent = (labels[card.status] || card.status) + ' · ' + card.name;
      if (e.rawInput) card.content.textContent = JSON.stringify(e.rawInput, null, 2);
      if (e.content) card.content.textContent = e.content.map((c) => c.type === 'diff' ? c.path + '\n이전:\n' + (c.oldText || '') + '\n변경:\n' + c.newText : c.text).join('\n');
    }
    if (e.type === 'plan') row('작업 계획', e.entries.map((p) => (p.status === 'completed' ? '✓ ' : '○ ') + p.content).join('\n'));
    if (e.type === 'usage') byId('usage').textContent = e.used.toLocaleString() + ' / ' + e.size.toLocaleString();
    scroll();
  }
  function options(id, values, selected, connected) {
    const select = byId(id); select.replaceChildren();
    for (const value of values) { const option = document.createElement('option'); option.value = value.value || value.id; option.textContent = value.name; select.append(option); }
    select.value = selected || ''; select.disabled = busy || !values.length || !connected;
  }
  function state(message) {
    busy = message.busy;
    connected = !!message.connected;
    byId('send').disabled = busy || !message.connected; byId('cancel').hidden = !busy || message.connecting;
    byId('reconnect').hidden = busy || !!message.connected;
    byId('new').disabled = busy; byId('history').disabled = busy;
    byId('status').textContent = message.connecting ? '서버 연결 중…' : busy ? '작업 중…' : message.connected ? '준비됨 · ' + message.info.cwd : message.connectionError ? '연결 실패 · ' + message.connectionError : '서버 연결 대기';
    if (message.info) {
      options('mode', message.info.modes, message.info.currentMode, message.connected);
      options('model', message.info.models, message.info.currentModel, message.connected);
    } else { byId('mode').disabled = true; byId('model').disabled = true; }
  }
  function permission(request) {
    if (approvals.has(request.requestId)) return;
    const box = document.createElement('section'); box.className = 'permission';
    const title = document.createElement('strong'); title.textContent = '작업을 허용할까요?';
    const text = document.createElement('p'); text.textContent = request.title; box.append(title, text);
    if (request.rawInput) { const detail = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = '작업 내용'; const pre = document.createElement('pre'); pre.textContent = JSON.stringify(request.rawInput, null, 2); detail.append(summary, pre); box.append(detail); }
    for (const option of request.options) {
      const button = document.createElement('button'); button.textContent = option.name;
      button.addEventListener('click', () => send('permission', { requestId: request.requestId, optionId: option.optionId })); box.append(button);
    }
    approvals.set(request.requestId, box); byId('permissions').append(box);
  }
  window.addEventListener('message', ({ data }) => {
    if (data.type === 'snapshot') {
      messages.replaceChildren(welcome); welcome.hidden = false; tools.clear(); approvals.clear(); byId('permissions').replaceChildren(); chunk = null; chunkKind = null;
      for (const entry of data.entries) event(entry);
      for (const request of data.permissions) permission(request);
      state(data);
    } else if (data.type === 'event') event(data.event);
    else if (data.type === 'state') state(data);
    else if (data.type === 'permission') permission(data.request);
    else if (data.type === 'permission_done') { approvals.get(data.requestId)?.remove(); approvals.delete(data.requestId); }
  });
  byId('composer').addEventListener('submit', (e) => {
    e.preventDefault(); const text = byId('prompt').value.trim(); if (!text || busy || !connected) return;
    send('send', { text }); byId('prompt').value = ''; vscode.setState({ draft: '' });
  });
  byId('prompt').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); byId('composer').requestSubmit(); } });
  for (const id of ['cancel', 'new', 'history', 'keys', 'folder', 'settings']) byId(id).addEventListener('click', () => send(id));
  byId('reconnect').addEventListener('click', () => send('ready'));
  for (const id of ['mode', 'model']) byId(id).addEventListener('change', () => send(id, { value: byId(id).value }));
  for (const button of document.querySelectorAll('[data-prompt]')) button.addEventListener('click', () => { byId('prompt').value = button.dataset.prompt; byId('prompt').focus(); });
  send('ready');
})();
