const state = { tasks: [], current: null, loading: false };
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
}
function showToast(message) { const toast = $('toast'); toast.textContent = message; toast.classList.remove('hidden'); setTimeout(() => toast.classList.add('hidden'), 3200); }
function statusClass(status) { return String(status || '').replaceAll('_', '-'); }
function taskLabel(task) { return task.user_request.length > 42 ? `${task.user_request.slice(0, 42)}…` : task.user_request; }

async function refreshTasks() {
  try {
    const payload = await api('/api/tasks');
    state.tasks = payload.tasks || [];
    renderTaskList();
    if (state.current && state.tasks.some((task) => task.task_id === state.current.task.task_id)) await selectTask(state.current.task.task_id, false);
    $('health-label').textContent = 'online';
  } catch (error) { $('health-label').textContent = 'offline'; }
}
async function selectTask(taskId, updateUrl = true) {
  try { state.current = await api(`/api/tasks/${encodeURIComponent(taskId)}`); renderCurrent(); if (updateUrl) history.replaceState({}, '', `#${taskId}`); }
  catch (error) { showToast(error.message); }
}
function renderTaskList() {
  $('task-list').innerHTML = state.tasks.length ? state.tasks.map((task) => `
    <button class="task-row ${state.current?.task?.task_id === task.task_id ? 'selected' : ''}" data-task="${escapeHtml(task.task_id)}">
      <span class="task-row-dot ${statusClass(task.status)}"></span><span class="task-row-copy"><b>${escapeHtml(taskLabel(task))}</b><small>${escapeHtml(task.status.replaceAll('_', ' '))}</small></span>
    </button>`).join('') : '<div class="empty">No tasks yet.</div>';
  document.querySelectorAll('[data-task]').forEach((button) => button.addEventListener('click', () => selectTask(button.dataset.task)));
}
function renderCurrent() {
  if (!state.current) return;
  const { task, messages = [], events = [], activity = [], attachments = [], artifacts = [] } = state.current;
  $('empty-state').classList.add('hidden'); $('workspace').classList.remove('hidden'); $('page-title').textContent = taskLabel(task);
  $('task-status').textContent = task.status.replaceAll('_', ' '); $('task-status').className = `status-pill ${statusClass(task.status)}`;
  $('task-summary').innerHTML = `<div class="summary-row"><span>Task ID</span><code>${escapeHtml(task.task_id)}</code></div><div class="summary-row"><span>Created</span><span>${new Date(task.created_at * 1000).toLocaleString()}</span></div><div class="summary-row"><span>Commands</span><span>${task.command_history.length}</span></div>`;
  const context = state.current.context;
  $('context-view').innerHTML = `<div>workspace&nbsp; ${escapeHtml(context?.workspace_root || './workspace')}</div><div>state-db&nbsp; local SQLite</div><div>controller&nbsp; DeepSeek</div>`;
  renderConversation(messages); renderAction(task); renderActivity(activity); renderFiles(attachments, artifacts); renderTaskList();
}
function renderConversation(messages) {
  const items = messages.map((message) => {
    let content = message.content; let role = message.role;
    if (role === 'assistant') { try { const parsed = JSON.parse(content); content = parsed.assistant_message || content; } catch (_) {} }
    if (role === 'user' && content.startsWith('{"execution_result"')) { try { const parsed = JSON.parse(content).execution_result; content = `Execution returned <b>${escapeHtml(parsed.task_status)}</b>${parsed.output != null ? `<pre>${escapeHtml(JSON.stringify(parsed.output, null, 2))}</pre>` : ''}`; } catch (_) {} }
    const label = role === 'assistant' ? 'DEEPSEEK' : 'YOU';
    return `<article class="message ${role}"><div class="message-label">${label}</div><div class="message-body">${role === 'user' && !content.includes('<b>') ? escapeHtml(content).replaceAll('\n', '<br>') : content}</div></article>`;
  }).join('');
  $('conversation').innerHTML = items || '<div class="empty">Conversation is ready.</div>';
  $('conversation').scrollTop = $('conversation').scrollHeight;
}
function renderAction(task) {
  const card = $('action-card');
  if (task.status === 'waiting_for_user') { card.classList.remove('hidden'); card.innerHTML = `<div class="action-kicker">DEEPSEEK NEEDS INPUT</div><strong>${escapeHtml(task.pending_input || 'Please provide the requested information.')}</strong><div class="action-line"><input id="pending-input" placeholder="Your answer…"><button id="submit-input" class="primary">Continue →</button></div>`; $('submit-input').onclick = submitInput; return; }
  if (task.status === 'waiting_for_approval') { const approval = task.pending_approval || {}; card.classList.remove('hidden'); card.innerHTML = `<div class="action-kicker">APPROVAL REQUIRED</div><strong>${escapeHtml(approval.summary || 'DeepSeek requested approval')}</strong><p>${escapeHtml(approval.impact || '')}</p><div class="action-line"><button id="approve-action" class="primary">Approve action</button><button id="reject-action" class="ghost">Cancel task</button></div>`; $('approve-action').onclick = () => approveAction(approval.action); $('reject-action').onclick = () => cancelTask('Approval rejected'); return; }
  card.classList.add('hidden'); card.innerHTML = '';
}
function renderActivity(activity) { $('activity-view').innerHTML = activity.length ? activity.slice(-8).reverse().map((item) => `<div class="activity-item"><span>${escapeHtml(item.type.replaceAll('_', ' '))}</span><small>${new Date(item.created_at * 1000).toLocaleTimeString()}</small></div>`).join('') : '<span class="muted">No activity yet.</span>'; }
function renderFiles(attachments, artifacts) { const all = [...attachments.map((item) => ({ ...item, kind: 'attachment', type: item.media_type || 'file' })), ...artifacts.map((item) => ({ ...item, kind: 'artifact', type: item.artifact_type }))]; $('files-view').innerHTML = all.length ? all.map((file) => `<div class="file-row"><span class="file-icon">${file.kind === 'artifact' ? '▣' : '⌁'}</span><span><b>${escapeHtml(file.kind)}</b><small>${escapeHtml(file.path)}</small></span></div>`).join('') : '<span class="muted">No local files registered.</span>'; }

async function createTask(message) { state.loading = true; try { const result = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ message }) }); await selectTask(result.task_id); await refreshTasks(); } catch (error) { showToast(error.message); } finally { state.loading = false; } }
async function submitInput() { const value = $('pending-input')?.value.trim(); if (!value) return; try { const result = await api(`/api/tasks/${state.current.task.task_id}/input`, { method: 'POST', body: JSON.stringify({ value }) }); state.current = { ...state.current, ...result }; await selectTask(state.current.task_id || state.current.task.task_id, false); } catch (error) { showToast(error.message); } }
async function approveAction(action) { try { await api(`/api/tasks/${state.current.task.task_id}/approve`, { method: 'POST', body: JSON.stringify({ action }) }); await selectTask(state.current.task.task_id, false); } catch (error) { showToast(error.message); } }
async function cancelTask(reason) { try { await api(`/api/tasks/${state.current.task.task_id}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) }); await selectTask(state.current.task.task_id, false); } catch (error) { showToast(error.message); } }

$('composer').addEventListener('submit', (event) => { event.preventDefault(); const input = $('message-input'); const value = input.value.trim(); if (!value || state.loading) return; if (state.current && state.current.task.status === 'waiting_for_user') { $('pending-input').value = value; submitInput(); } else { createTask(value); } input.value = ''; });
$('message-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('composer').requestSubmit(); } });
$('new-task').onclick = () => { state.current = null; $('workspace').classList.add('hidden'); $('empty-state').classList.remove('hidden'); $('message-input').focus(); };
$('empty-new-task').onclick = () => $('message-input').focus();
$('add-file').onclick = async () => { if (!state.current) return; const path = prompt('Local file path (already accessible to Open-Manus):'); if (!path) return; try { await api(`/api/tasks/${state.current.task.task_id}/attachments`, { method: 'POST', body: JSON.stringify({ attachment_id: `att-${Date.now()}`, path }) }); await selectTask(state.current.task.task_id, false); } catch (error) { showToast(error.message); } };

(async function init() { await refreshTasks(); const hash = location.hash.slice(1); if (hash) await selectTask(hash); setInterval(refreshTasks, 5000); })();
