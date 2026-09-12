const DAY_START = 8 * 60;
const DAY_END = 24 * 60;
const HEADER_HEIGHT = 68;
const QUARTER_HEIGHT = 18;
const state = {
  apiBase: localStorage.getItem('planner-api') || 'http://127.0.0.1:8000/api/v1', workspace: null,
  weekStart: monday(new Date()), week: null, tasks: [], directions: [], labels: [], notifications: [], selected: new Set(),
  filter: 'all', taskKindFilter: 'all', catalogTaskKind: 'all', archive: null, archiveSection: 'tasks', collapsedTaskNodes: new Set(), search: '', plan: null, planPanelHidden: false, session: null,
  sessionBarHidden: localStorage.getItem('planner-session-hidden') === 'true', sessionReceivedAt: 0, timer: null,
  section: 'plan', report: null, dailyProgress: null, calendarDays: 21, templateSlots: [], goals: [], goalDetail: null, goalTaskIds: [], goalTaskFormOpen: false, goalTaskDraft: { title:'', parentTaskId:'', estimate:60, priority:3 }, planOverviewHidden: localStorage.getItem('planner-overview-hidden') === 'true', deletedTasks: [], sessionView: localStorage.getItem('planner-session-view') || 'timeline', taskSessionGroups: null, panelHidden: localStorage.getItem('planner-task-panel-hidden') === 'true', navPanelHidden: localStorage.getItem('planner-nav-panel-hidden') === 'true', taskSort: localStorage.getItem('planner-task-sort') || 'deadline', availabilitySelection: null, dragSource: null,
  planRevision: 0, pendingBlockMutations: new Map(), refreshTimer: null, loadToken: 0, loadController: null, nowTimer: null,
};
const $ = (selector) => document.querySelector(selector);
const pixelsPerMinute = QUARTER_HEIGHT / 15;
const priorityValues = [1, 2, 3, 5, 8, 13, 21];

function monday(value) { const date = new Date(value); date.setDate(date.getDate() - ((date.getDay() + 6) % 7)); date.setHours(0, 0, 0, 0); return date; }
function dateKey(value) { const date = new Date(value); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
function formatMinutes(value) { const hours = Math.floor(value / 60); return `${hours ? `${hours}ч ` : ''}${value % 60}м`; }
function formatWeek(date) { const end = new Date(date); end.setDate(end.getDate() + 6); const options = { day: 'numeric', month: 'long' }; return `${date.toLocaleDateString('ru-RU', options)} — ${end.toLocaleDateString('ru-RU', options)}`; }
function localMinute(iso) { const value = new Date(iso); return value.getHours() * 60 + value.getMinutes(); }
function snapMinute(value) { const start = state.workspace?.visible_day_start ?? DAY_START; const step = state.workspace?.grid_step_minutes ?? 15; return Math.max(start, Math.min(DAY_END - step, Math.round(value / step) * step)); }
function pixelForMinute(minute) { return HEADER_HEIGHT + (minute - DAY_START) * pixelsPerMinute; }
function heightForRange(start, end) { return Math.max(QUARTER_HEIGHT - 2, (end - start) * pixelsPerMinute - 2); }
function colorForTask(task) { return task.color || task.direction?.color || '#356AE6'; }
function rangeText(start, end) { return `${new Date(start).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}–${new Date(end).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`; }
function deadlineText(iso) { const value = new Date(iso); return value < new Date() ? 'просрочено' : `до ${value.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}`; }
function taskTitle(id) { return state.tasks.find((task) => task.id === id)?.title || 'Задача'; }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char])); }
function toMinutes(value) { const [hours, minutes] = String(value).split(':').map(Number); return hours * 60 + minutes; }
function timeValue(minutes) { return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`; }
function formatSeconds(value) { return formatMinutes(Math.round((value || 0) / 60)); }

async function api(path, options = {}) { const response = await fetch(`${state.apiBase}${path}`, { headers: { 'Content-Type':'application/json', ...(options.headers || {}) }, ...options }); if (response.status === 204) return null; const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.error?.message || `Ошибка сервера: ${response.status}`); return body; }
async function initialize() { try { const bootstrap = await api('/bootstrap'); state.workspace = bootstrap.workspace; await loadData(); showToast('Планировщик готов'); } catch (error) { showToast(`Не удалось подключиться к серверу. ${error.message}`, true); renderOffline(); } }
async function loadData() {
  const token = ++state.loadToken;
  state.loadController?.abort();
  state.loadController = new AbortController();
  const id = state.workspace.id;
  const signal = state.loadController.signal;
  try {
    const [startup, labels, notifications, progress, goals] = await Promise.all([api(`/workspaces/${id}/startup?week_start=${dateKey(state.weekStart)}&days=${state.calendarDays}`, { signal }), api(`/workspaces/${id}/labels`, { signal }), api(`/workspaces/${id}/notifications`, { signal }), api(`/workspaces/${id}/reports/daily/${dateKey(new Date())}`, { signal }), api(`/workspaces/${id}/goals?include_completed=true`, { signal })]);
    if (token !== state.loadToken) return;
    reconcilePendingBlockMutations(startup.week);
    state.week = startup.week; state.tasks = startup.tasks; state.directions = startup.directions; state.labels = labels; state.notifications = notifications; state.goals = goals; state.session = startup.active_session; state.templateSlots = startup.default_availability || []; state.dailyProgress = progress;
    render();
  } catch (error) {
    if (error.name !== 'AbortError') throw error;
  }
}
async function refreshPlanData() {
  const revisionAtRequest = state.planRevision;
  const id = state.workspace.id;
  const startup = await api(`/workspaces/${id}/startup?week_start=${dateKey(state.weekStart)}&days=${state.calendarDays}`);
  if (revisionAtRequest !== state.planRevision) return;
  reconcilePendingBlockMutations(startup.week);
  state.week = startup.week; state.tasks = startup.tasks; state.directions = startup.directions; state.session = startup.active_session; state.templateSlots = startup.default_availability || []; render();
  if (state.pendingBlockMutations.size) scheduleBackgroundRefresh(700);
}
function scheduleBackgroundRefresh(delay = 350) {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => refreshPlanData().catch((error) => showToast(`Не удалось синхронизировать план: ${error.message}`, true)), delay);
}
function mergeBlockIntoWeek(week, block) {
  const day = week?.days.find((item) => item.date === dateKey(block.start_at)); if (!day) return;
  const index = day.blocks.findIndex((item) => item.id === block.id); if (index >= 0) day.blocks[index] = block; else day.blocks.push(block);
  day.blocks.sort((a, b) => new Date(a.start_at) - new Date(b.start_at));
}
function mergeBlocks(blocks, keepUntilConfirmed = false) {
  for (const block of blocks) {
    mergeBlockIntoWeek(state.week, block);
    if (keepUntilConfirmed) state.pendingBlockMutations.set(block.id, { kind:'upsert', block });
  }
  if (keepUntilConfirmed) state.planRevision += 1;
}
function removeBlockLocally(blockId, keepUntilConfirmed = false) {
  state.week?.days.forEach((day) => { day.blocks = day.blocks.filter((block) => block.id !== blockId); });
  if (keepUntilConfirmed) { state.pendingBlockMutations.set(blockId, { kind:'remove' }); state.planRevision += 1; }
}
function removeArchivedTaskTreeLocally(taskId) {
  const archivedIds = new Set([taskId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const task of state.tasks) {
      if (task.parent_task_id && archivedIds.has(task.parent_task_id) && !archivedIds.has(task.id)) {
        archivedIds.add(task.id);
        changed = true;
      }
    }
  }
  state.week?.days.forEach((day) => { day.blocks = day.blocks.filter((block) => !archivedIds.has(block.task_id)); });
  state.tasks = state.tasks.filter((task) => !archivedIds.has(task.id));
  state.selected = new Set([...state.selected].filter((id) => !archivedIds.has(id)));
  renderTasks();
  renderWeek();
}
function reconcilePendingBlockMutations(week) {
  for (const [blockId, mutation] of state.pendingBlockMutations) {
    const serverBlock = week.days.flatMap((day) => day.blocks).find((block) => block.id === blockId);
    if (mutation.kind === 'upsert') {
      if (serverBlock && serverBlock.version >= mutation.block.version && serverBlock.start_at === mutation.block.start_at && serverBlock.end_at === mutation.block.end_at && serverBlock.status === mutation.block.status) state.pendingBlockMutations.delete(blockId);
      else mergeBlockIntoWeek(week, mutation.block);
    } else if (!serverBlock) state.pendingBlockMutations.delete(blockId);
    else week.days.forEach((day) => { day.blocks = day.blocks.filter((block) => block.id !== blockId); });
  }
}
function render() { renderHeader(); renderDailySuccess(); renderGoalStrip(); renderPlanOverview(); renderTaskPanelVisibility(); renderTasks(); renderWeek(); renderSession(); renderPlan(); renderSection(); }
function renderHeader() { const first = new Date(`${state.week.days[0].date}T12:00:00`); const last = new Date(`${state.week.days.at(-1).date}T12:00:00`); const options = { day:'numeric', month:'long' }; $('#week-range').textContent = `${first.toLocaleDateString('ru-RU', options)} — ${last.toLocaleDateString('ru-RU', options)}`; const total = state.week.days.reduce((sum, day) => sum + day.capacity_minutes, 0); const free = state.week.days.reduce((sum, day) => sum + day.free_minutes, 0); $('#week-capacity').textContent = `${formatMinutes(free)} / ${formatMinutes(total)}`; }
function renderDailySuccess() { const root = $('#daily-success'); const progress = state.dailyProgress; if (!progress) { root.hidden = true; return; } const goal = Math.round((progress.worked_percent + progress.planning_percent + (progress.planned_task_count ? Math.min(100, Math.round(progress.completed_count * 100 / progress.planned_task_count)) : 0)) / 3); const taskText = progress.planned_task_count ? `${progress.completed_count} из ${progress.planned_task_count}` : `${progress.completed_count}`; root.hidden = false; root.innerHTML = `<div class="success-head"><div><span class="success-emoji">${goal >= 75 ? '🔥' : goal >= 40 ? '🌱' : '✨'}</span><strong>Ритм дня: ${goal}%</strong><small>маленькие шаги складываются в результат</small></div><span class="success-score">${goal}/100</span></div><div class="success-metrics"><article><span>⏱️ В работе</span><strong>${progress.worked_percent}%</strong><small>${formatSeconds(progress.actual_task_seconds)} из ${formatSeconds(progress.planned_seconds)}</small><i><b style="width:${progress.worked_percent}%"></b></i></article><article><span>🧩 План заполнен</span><strong>${progress.planning_percent}%</strong><small>${formatSeconds(progress.planned_seconds)} из ${formatSeconds(progress.capacity_seconds)}</small><i><b style="width:${progress.planning_percent}%"></b></i></article><article><span>✅ Задачи</span><strong>${taskText}</strong><small>выполнено сегодня</small><i><b style="width:${progress.planned_task_count ? Math.min(100, Math.round(progress.completed_count * 100 / progress.planned_task_count)) : 0}%"></b></i></article></div>`; }
function renderGoalStrip() { const root = $('#goal-strip'); const goals = [...state.goals].sort((left, right) => (left.status === 'ACTIVE' ? 0 : 1) - (right.status === 'ACTIVE' ? 0 : 1)).slice(0, 4); const canShow = state.section === 'plan' && goals.length > 0; root.hidden = !canShow; if (root.hidden) return; root.innerHTML = `<div class="goal-strip-title"><span>🎯</span><strong>Цели в фокусе</strong><button class="button ghost" data-action="open-goals">Все цели →</button></div><div class="goal-strip-list">${goals.map((goal) => `<button class="goal-mini-card ${goal.status === 'COMPLETED' ? 'completed' : ''}" data-action="open-goal" data-id="${goal.id}" style="--goal-color:${goal.color || '#356AE6'}"><span>${escapeHtml(goal.title)}</span><strong>${goal.progress_percent || 0}%</strong></button>`).join('')}</div>`; }
function renderPlanOverview() { const root = $('#plan-overview'); const reveal = $('#show-overview'); const visible = state.section === 'plan' && !state.planOverviewHidden; root.hidden = !visible; reveal.hidden = state.section !== 'plan' || visible; }
function renderTaskPanelVisibility() {
  const taskVisible = state.section === 'plan' && !state.panelHidden;
  const navVisible = !state.navPanelHidden;
  $('.task-panel').hidden = !taskVisible;
  $('.navigation').hidden = !navVisible;
  $('.app-shell').classList.toggle('task-collapsed', !taskVisible);
  $('.app-shell').classList.toggle('nav-collapsed', !navVisible);
  $('#show-task-panel').hidden = taskVisible || state.section !== 'plan';
  $('#show-nav-panel').hidden = navVisible;
  $('#task-sort').value = state.taskSort;
}

function sortTasks(tasks) {
  return [...tasks].sort((left, right) => {
    if (state.taskSort === 'priority') return right.priority - left.priority || Number(Boolean(left.deadline_at)) - Number(Boolean(right.deadline_at)) || left.title.localeCompare(right.title, 'ru');
    const leftDeadline = left.deadline_at ? new Date(left.deadline_at).getTime() : Number.POSITIVE_INFINITY;
    const rightDeadline = right.deadline_at ? new Date(right.deadline_at).getTime() : Number.POSITIVE_INFINITY;
    return leftDeadline - rightDeadline || right.priority - left.priority || left.title.localeCompare(right.title, 'ru');
  });
}

function taskDepth(task) { let depth = 0; let current = task; const seen = new Set(); while (current?.parent_task_id && !seen.has(current.parent_task_id)) { seen.add(current.parent_task_id); current = state.tasks.find((item) => item.id === current.parent_task_id); depth += 1; } return depth; }
function parentTaskOptions(selectedId = '', editingId = '') { return state.tasks.filter((task) => task.status === 'ACTIVE' && task.id !== editingId).map((task) => `<option value="${task.id}" ${selectedId === task.id ? 'selected' : ''}>${'— '.repeat(taskDepth(task))}${escapeHtml(task.title)}</option>`).join(''); }
function parentTaskCandidates(editingId = '') { return state.tasks.filter((candidate) => { if (candidate.status !== 'ACTIVE' || candidate.id === editingId) return false; let current = candidate; const seen = new Set(); while (current?.parent_task_id && !seen.has(current.id)) { if (current.parent_task_id === editingId) return false; seen.add(current.id); current = state.tasks.find((item) => item.id === current.parent_task_id); } return true; }); }
function taskKind(task) { return task.is_checkpoint ? 'checkpoints' : task.is_leaf ? 'tasks' : 'groups'; }
function taskKindLabel(task) { return ({ tasks:'Задача', checkpoints:'Чекпоинт', groups:'Группа' })[taskKind(task)]; }
function taskMatchesKind(task, kind) { return kind === 'all' || taskKind(task) === kind; }
function canScheduleTask(task) { return Boolean(task.can_schedule ?? task.is_leaf); }

function renderTasks() {
  const tasks = sortTasks(state.tasks.filter((task) => task.status !== 'ARCHIVED' && taskMatchesKind(task, state.taskKindFilter) && task.title.toLowerCase().includes(state.search.toLowerCase()) && (state.filter === 'all' || state.filter === 'unplanned' && task.status === 'ACTIVE' && canScheduleTask(task) && task.planning_status === 'UNPLANNED' || state.filter === 'overdue' && task.is_overdue)));
  $('#task-count').textContent = state.tasks.filter((task) => task.status === 'ACTIVE').length;
  $('#task-list').innerHTML = tasks.length ? tasks.map((task) => {
    const completed = task.status === 'COMPLETED'; const canPlan = task.status === 'ACTIVE' && canScheduleTask(task) && task.planning_status !== 'PLANNED';
    return `<article class="task-card ${completed ? 'completed' : ''} ${state.selected.has(task.id) ? 'selected' : ''} ${task.is_overdue ? 'overdue' : ''} ${task.is_leaf ? '' : 'task-parent'}" ${canPlan ? `draggable="true" data-drag-task="${task.id}"` : ''} data-task-card="${task.id}">
      <input class="task-selector" type="checkbox" ${state.selected.has(task.id) ? 'checked' : ''} ${canPlan ? '' : 'disabled'} aria-label="Выбрать ${escapeHtml(task.title)}" />
      <span class="task-color" style="background:${colorForTask(task)}"></span>
      <div><div class="task-name">${completed ? '✓ ' : ''}${escapeHtml(task.title)}</div><div class="task-meta ${task.is_overdue ? 'danger' : ''}">${completed ? 'выполнено' : task.parent_task_title ? `${taskKindLabel(task).toLowerCase()} · ${escapeHtml(task.parent_task_title)} · ` : ''}${completed ? '' : canScheduleTask(task) ? `${escapeHtml(task.direction?.name || 'Без направления')} · ${task.deadline_at ? deadlineText(task.deadline_at) : 'без срока'}` : `${task.child_count} пункт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'}`}${task.repeat_rule === 'WEEKLY' && !completed ? ' · еженедельно' : ''}</div><div class="task-actions">${completed ? `<button class="task-icon" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные" aria-label="Вернуть в активные">✓</button>` : canScheduleTask(task) ? `<button class="task-icon" data-start-task="${task.id}" title="Начать" aria-label="Начать ${escapeHtml(task.title)}">▶</button><button class="task-icon" data-action="auto-plan-task" data-id="${task.id}" ${canPlan ? '' : 'disabled'} title="Распределить автоматически" aria-label="Распределить автоматически">◷</button>` : ''}${task.status === 'ACTIVE' ? `<button class="task-icon" data-action="new-child-task" data-id="${task.id}" title="Добавить чекпоинт" aria-label="Добавить чекпоинт к ${escapeHtml(task.title)}">＋</button>` : ''}<button class="task-icon" data-action="edit-task" data-id="${task.id}" title="Редактировать" aria-label="Редактировать">✎</button><button class="task-icon task-delete" data-action="delete-task" data-id="${task.id}" title="В архив" aria-label="В архив: ${escapeHtml(task.title)}">×</button></div></div>
      <span class="task-duration">${canScheduleTask(task) ? `${task.planned_minutes}/${task.estimate_minutes}м` : 'группа'}</span>
    </article>`;
  }).join('') : '<div class="empty"><strong>Задач пока нет</strong><p>Добавьте первую задачу — она появится здесь.</p></div>';
  $('#selection-bar').hidden = state.selected.size === 0; $('#selected-summary').textContent = `Выбрано: ${state.selected.size}`;
}

function positioned(className, start, end, content, color = '', attrs = '') { return `<div class="${className}" style="top:${pixelForMinute(start) + 1}px;height:${heightForRange(start, end)}px;${color ? `background:${color}` : ''}" ${attrs}>${content}</div>`; }
function renderWeek() {
  const timeLabels = [];
  for (let minute = DAY_START; minute <= DAY_END; minute += 30) {
    timeLabels.push(`<div class="time-label ${minute === DAY_END ? 'time-label-end' : ''}">${timeValue(minute)}</div>`);
  }
  $('#time-axis').innerHTML = timeLabels.join('');
  const today = dateKey(new Date());
  $('#week-columns').innerHTML = state.week.days.map((day) => {
    const availability = day.availability.map((slot) => positioned('availability', localMinute(slot.start_at), localMinute(slot.end_at), `<button type="button" class="availability-action" draggable="false" data-action="remove-availability-slot" data-date="${day.date}" data-start="${localMinute(slot.start_at)}" data-end="${localMinute(slot.end_at)}" aria-label="Удалить свободное время ${rangeText(slot.start_at, slot.end_at)}" title="Удалить свободное время">×</button>`, '', 'data-availability-slot="true"')).join('');
    const events = day.fixed_events.map((event) => positioned('fixed-event', localMinute(event.start_at), localMinute(event.end_at), `<div class="block-actions"><button type="button" class="block-action" draggable="false" data-action="edit-event" data-id="${event.id}" data-date="${day.date}" aria-label="Изменить событие ${escapeHtml(event.title)}" title="Изменить событие">✎</button><button type="button" class="block-action" draggable="false" data-action="delete-event" data-id="${event.id}" aria-label="Удалить событие ${escapeHtml(event.title)}" title="Удалить событие">×</button></div><strong>${escapeHtml(event.title)}</strong><span class="block-time">${rangeText(event.start_at, event.end_at)}</span>`, event.color, `data-fixed-event="${event.id}"`)).join('');
    const blocks = day.blocks.filter((block) => { const task = state.tasks.find((item) => item.id === block.task_id); return task && task.status !== 'ARCHIVED' && !task.deleted_at; }).map((block) => { const completed = block.status === 'COMPLETED' || block.task_status === 'COMPLETED'; const start = localMinute(block.start_at); const end = localMinute(block.end_at); const compact = end - start <= 30; const actions = completed ? `<button type="button" class="block-action" draggable="false" data-action="reopen-task" data-id="${block.task_id}" aria-label="Вернуть задачу в активные" title="Вернуть в активные">✓</button>` : `<button type="button" class="block-action" draggable="false" data-start-task="${block.task_id}" aria-label="Начать ${escapeHtml(block.task_title || 'задачу')}" title="Начать">▶</button><button type="button" class="block-action" draggable="false" data-action="complete-block" data-id="${block.id}" aria-label="Завершить блок" title="Завершить">✓</button>`; return positioned(`schedule-block ${compact ? 'compact-block' : ''} ${block.has_conflict ? 'conflict' : ''} ${block.is_pinned ? 'pinned' : ''} ${completed ? 'completed' : ''}`, start, end, `<div class="block-actions">${actions}<button type="button" class="block-action" draggable="false" data-action="edit-block" data-id="${block.id}" aria-label="Изменить время" title="Изменить время">✎</button><button type="button" class="block-action" draggable="false" data-action="cancel-block" data-id="${block.id}" aria-label="Убрать из плана" title="Убрать из плана">×</button></div><strong>${completed ? '✓ ' : ''}${escapeHtml(block.task_title || 'Задача')}</strong><span class="block-time">${rangeText(block.start_at, block.end_at)}${completed ? ' · готово' : ''}</span>`, block.task_color || block.direction_color || '#356AE6', `${completed ? '' : 'draggable="true"'} data-schedule-block="${block.id}"`); }).join('');
    const proposals = state.plan?.proposals.filter((item) => dateKey(item.start_at) === day.date).map((item) => positioned(`proposal-block ${item.has_conflict ? 'conflict' : ''}`, localMinute(item.start_at), localMinute(item.end_at), `<strong>${escapeHtml(taskTitle(item.task_id))}</strong><span class="block-time">предложено</span>`)).join('') || '';
    const nowLine = day.date === today ? `<span class="now-line" style="top:${pixelForMinute(new Date().getHours() * 60 + new Date().getMinutes())}px"></span>` : '';
    const label = new Date(`${day.date}T12:00:00`).toLocaleDateString('ru-RU', { weekday:'short', day:'numeric', month:'short' });
    const deadlineBadge = day.deadline_count ? `<b class="deadline-badge" title="${day.deadline_count} задач${day.deadline_count === 1 ? 'а заканчивается' : ' заканчиваются'} сегодня">${day.deadline_count}</b>` : '';
    const sunday = new Date(`${day.date}T12:00:00`).getDay() === 0;
    return `<section class="day-column ${sunday ? 'sunday' : ''}" data-date="${day.date}"><header class="day-header ${day.date === today ? 'today' : ''} ${sunday ? 'sunday' : ''}"><div class="day-heading"><strong>${label}</strong>${deadlineBadge}</div><span>${formatMinutes(day.free_minutes)} свободно</span></header>${availability}${events}${blocks}${proposals}${nowLine}</section>`;
  }).join('');
  clearInterval(state.nowTimer);
  state.nowTimer = setInterval(() => { const line = $('.now-line'); if (line) line.style.top = `${pixelForMinute(new Date().getHours() * 60 + new Date().getMinutes())}px`; }, 60_000);
}
function renderPlan() { const review = $('#planner-review'); const show = $('#show-plan'); if (!state.plan) { review.hidden = true; show.hidden = true; return; } review.hidden = state.planPanelHidden; show.hidden = !state.planPanelHidden; const conflicts = state.plan.proposals.filter((item) => item.has_conflict).length; const unplanned = Object.values(state.plan.explanation.unplanned_minutes || {}).reduce((sum, item) => sum + item, 0); $('#review-metrics').innerHTML = `<strong>${state.plan.proposals.length}</strong> блоков · <strong>${conflicts}</strong> конфликтов · <strong>${unplanned}м</strong> не размещено`; $('#review-explanations').textContent = Object.values(state.plan.explanation.explanations || {}).slice(0, 2).join(' '); }
function renderSession() { const bar = $('#session-bar'); const restore = $('#session-restore'); if (!state.session) { bar.hidden = true; restore.hidden = true; clearInterval(state.timer); return; } state.sessionReceivedAt = Date.now(); bar.hidden = state.sessionBarHidden; restore.hidden = !state.sessionBarHidden; $('#session-name').textContent = state.session.task_title || 'Рабочая сессия'; $('#session-pause').textContent = state.session.status === 'RUNNING' ? 'Пауза' : 'Продолжить'; updateTimer(); clearInterval(state.timer); state.timer = setInterval(updateTimer, 1000); }
function updateTimer() { if (!state.session) return; let seconds = state.session.elapsed_seconds; if (state.session.status === 'RUNNING') seconds += Math.max(0, Math.floor((Date.now() - state.sessionReceivedAt) / 1000)); $('#session-time').textContent = new Date(seconds * 1000).toISOString().slice(11, 19); }

async function openGoalPage(goalId = null) {
  try {
    state.goalDetail = goalId ? await api(`/workspaces/${state.workspace.id}/goals/${goalId}`) : { title:'', description:'', direction_id:null, color:'#356AE6', priority:3, deadline_at:null, tasks:[], progress_percent:0, task_count:0, completed_task_count:0 };
    state.goalTaskIds = state.goalDetail.tasks.filter((task) => !task.parent_task_id).map((task) => task.id);
    state.goalTaskFormOpen = false;
    state.goalTaskDraft = { title:'', parentTaskId:'', estimate:60, priority:3 };
    state.section = 'goal-detail';
    renderSection();
  } catch (error) { showToast(error.message, true); }
}

function goalTaskForDetail(taskId) { return state.goalDetail?.tasks.find((task) => task.id === taskId) || state.tasks.find((task) => task.id === taskId); }

function goalTaskParentOptions(tasks, rootIds, selectedId = '') {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const children = new Map();
  tasks.forEach((task) => { if (task.parent_task_id) children.set(task.parent_task_id, [...(children.get(task.parent_task_id) || []), task]); });
  const roots = [...rootIds.map((id) => byId.get(id)).filter(Boolean), ...tasks.filter((task) => !task.parent_task_id && !rootIds.includes(task.id))];
  const options = [];
  const visit = (task, depth) => { options.push(`<option value="${task.id}" ${selectedId === task.id ? 'selected' : ''}>${'— '.repeat(depth)}${escapeHtml(task.title)}</option>`); (children.get(task.id) || []).sort((left, right) => left.child_position - right.child_position).forEach((child) => visit(child, depth + 1)); };
  roots.forEach((task) => visit(task, 0));
  return options.join('');
}

function rememberGoalTaskDraft() {
  const form = $('#goal-new-task-form');
  if (!form) return;
  const fields = new FormData(form);
  state.goalTaskDraft = { title:String(fields.get('title') || ''), parentTaskId:String(fields.get('parent_task_id') || ''), estimate:Number(fields.get('estimate')) || 60, priority:Number(fields.get('priority')) || 3 };
}

function renderGoalDetail(view) {
  const goal = state.goalDetail;
  if (!goal) { state.section = 'goals'; renderSection(); return; }
  const tasks = goal.tasks || [];
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const childrenByParent = new Map();
  tasks.forEach((task) => { if (task.parent_task_id) childrenByParent.set(task.parent_task_id, [...(childrenByParent.get(task.parent_task_id) || []), task]); });
  const roots = state.goalTaskIds.map((id) => byId.get(id)).filter(Boolean);
  const goalRows = [];
  const renderNode = (task, depth, rootIndex = -1) => {
    const children = (childrenByParent.get(task.id) || []).sort((left, right) => left.child_position - right.child_position);
    const isRoot = depth === 0;
    const collapsed = state.collapsedTaskNodes.has(task.id);
    const toggle = children.length ? `<button class="tree-toggle" type="button" data-action="toggle-task-node" data-id="${task.id}" aria-label="${collapsed ? 'Развернуть' : 'Свернуть'} ${escapeHtml(task.title)}">${collapsed ? '▸' : '▾'}</button>` : '<span class="tree-toggle-placeholder"></span>';
    goalRows.push(`<article class="goal-task-row" style="--task-depth:${depth}"><span class="goal-order">${isRoot ? rootIndex + 1 : '↳'}</span><div><div class="goal-task-title">${toggle}<strong class="${task.status === 'COMPLETED' ? 'completed-title' : ''}">${task.status === 'COMPLETED' ? '✓ ' : ''}${escapeHtml(task.title)}</strong></div><small>${task.status === 'COMPLETED' ? 'выполнено' : task.is_leaf ? `${task.estimate_minutes} мин · важность ${task.priority}` : `${task.child_count} чекпоинт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'}`}</small></div><div class="row-actions">${isRoot ? `<button class="button ghost" type="button" data-action="goal-task-up" data-id="${task.id}" ${rootIndex === 0 ? 'disabled' : ''}>↑</button><button class="button ghost" type="button" data-action="goal-task-down" data-id="${task.id}" ${rootIndex === roots.length - 1 ? 'disabled' : ''}>↓</button>` : ''}${task.status === 'COMPLETED' ? `<button class="button ghost" type="button" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные">✓</button>` : ''}<button class="button ghost" type="button" data-action="new-child-task" data-id="${task.id}" title="Добавить чекпоинт">＋</button><button class="button ghost" type="button" data-action="edit-goal-task" data-id="${task.id}">✎</button><button class="button ghost task-delete" type="button" data-action="${isRoot ? 'goal-task-remove' : 'delete-goal-task'}" data-id="${task.id}" title="${isRoot ? 'Убрать из цели' : 'В архив'}" aria-label="${isRoot ? 'Убрать из цели' : 'В архив'}">×</button></div></article>`);
    if (!collapsed) children.forEach((child) => renderNode(child, depth + 1));
  };
  roots.forEach((task, index) => renderNode(task, 0, index));
  const deadline = goal.deadline_at ? goal.deadline_at.slice(0, 16) : '';
  const draft = state.goalTaskDraft;
  const goalParentChoices = tasks.filter((item) => item.status === 'ACTIVE').map((item) => `<button class="parent-choice" type="button" data-action="select-goal-task-parent" data-id="${item.id}">${escapeHtml(item.title)}<small>${taskKindLabel(item)}</small></button>`).join('');
  const newTaskPanel = state.goalTaskFormOpen ? `<form id="goal-new-task-form" class="goal-new-task"><div class="goal-new-task-heading"><p class="eyebrow">НОВАЯ ЗАДАЧА В ЭТОЙ ЦЕЛИ</p><button class="panel-toggle" type="button" data-action="hide-goal-task-form" aria-label="Скрыть форму">×</button></div><label class="field">Название<input name="title" required autofocus value="${escapeHtml(draft.title)}" placeholder="Например, разобрать главу" /></label><label class="checkpoint-switch"><input name="is_checkpoint" type="checkbox" ${draft.parentTaskId ? 'checked' : ''}/> Это чекпоинт</label><div class="checkpoint-parent" id="goal-checkpoint-parent-controls" ${draft.parentTaskId ? '' : 'hidden'}><span>Родитель: <strong id="goal-checkpoint-parent-name">${escapeHtml(tasks.find((item) => item.id === draft.parentTaskId)?.title || 'не выбран')}</strong></span><details><summary>Выбрать родителя</summary><div class="parent-choice-list">${goalParentChoices || '<small>Подходящих задач нет.</small>'}</div></details><input name="parent_task_id" type="hidden" value="${draft.parentTaskId}" /></div><div class="inline-fields"><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${draft.estimate}" required /></label><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${value === draft.priority ? 'selected' : ''}>${value}</option>`).join('')}</select></label></div><button class="button primary" type="submit">Добавить задачу</button></form>` : '';
  const goalActions = goal.id ? `<div class="goal-lifecycle-actions">${goal.status === 'COMPLETED' ? '<button class="button secondary" type="button" data-action="reopen-goal" data-id="' + goal.id + '">Вернуть в активные</button>' : '<button class="button ghost" type="button" data-action="complete-goal" data-id="' + goal.id + '">Отметить готовой</button>'}<button class="button danger" type="button" data-action="delete-goal" data-id="${goal.id}">Удалить цель</button></div>` : '';
  view.innerHTML = `<header class="page-header"><div><button class="button ghost" data-action="back-to-goals">← Все цели</button><p class="eyebrow">ЦЕЛЬ, ПРОЕКТЫ И ЧЕКПОИНТЫ</p><h2>${escapeHtml(goal.title || 'Новая цель')}</h2></div><div class="goal-progress"><strong>${goal.progress_percent || 0}%</strong><span>достигнуто</span><i><b style="width:${goal.progress_percent || 0}%"></b></i></div></header><div class="goal-detail-layout"><form id="goal-detail-form" class="settings-card form-grid"><h3>${goal.id ? 'Параметры цели' : 'Создать цель'}</h3><label class="field">Название<input name="title" required value="${escapeHtml(goal.title || '')}" /></label><label class="field">Направление<select name="direction_id"><option value="">Без направления</option>${state.directions.map((item) => `<option value="${item.id}" ${goal.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Описание<textarea name="description" rows="5">${escapeHtml(goal.description || '')}</textarea></label><div class="inline-fields"><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${goal.priority === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Цвет<input name="color" type="color" value="${goal.color || '#356AE6'}" /></label></div><label class="field">Срок<input name="deadline" type="datetime-local" value="${deadline}" /></label><button class="button primary" type="submit">${goal.id ? 'Сохранить цель и порядок' : 'Создать цель'}</button>${goalActions}</form><section class="settings-card goal-sequence"><div class="goal-sequence-head"><div><p class="eyebrow">ДЕРЕВО РАБОТЫ</p><h3>Задачи цели</h3><p class="muted">Группа объединяет чекпоинты. В расписание и учёт времени попадают только конечные задачи.</p></div><button class="button secondary" type="button" data-action="toggle-goal-task-form">+ Добавить</button></div><div class="goal-task-list">${goalRows.length ? goalRows.join('') : '<div class="empty"><strong>Задач пока нет</strong><p>Добавьте первый проект или конкретный чекпоинт.</p></div>'}</div>${newTaskPanel}</section></div>`;
  const goalCheckpointInput = $('#goal-new-task-form [name="is_checkpoint"]');
  goalCheckpointInput?.addEventListener('change', () => { $('#goal-checkpoint-parent-controls').hidden = !goalCheckpointInput.checked; });
}

async function saveGoalDetail(form) {
  const fields = new FormData(form);
  const deadline = fields.get('deadline');
  const payload = { title:fields.get('title'), direction_id:fields.get('direction_id') || null, description:fields.get('description') || null, color:fields.get('color'), priority:Number(fields.get('priority')), deadline_at:deadline ? new Date(deadline).toISOString() : null };
  try {
    let goalId = state.goalDetail.id;
    if (goalId) await api(`/workspaces/${state.workspace.id}/goals/${goalId}`, { method:'PATCH', body:JSON.stringify(payload) });
    else { const goal = await api(`/workspaces/${state.workspace.id}/goals`, { method:'POST', body:JSON.stringify(payload) }); goalId = goal.id; }
    state.goalDetail = await api(`/workspaces/${state.workspace.id}/goals/${goalId}/tasks`, { method:'PUT', body:JSON.stringify({ task_ids:state.goalTaskIds }) });
    await loadData();
    state.goalTaskIds = state.goalDetail.tasks.filter((task) => !task.parent_task_id).map((task) => task.id);
    state.section = 'goal-detail';
    renderSection();
    showToast('Цель и последовательность сохранены');
  } catch (error) { showToast(error.message, true); }
}

async function saveGoalTaskSequence() {
  if (!state.goalDetail?.id) { showToast('Сначала сохраните саму цель.', true); return; }
  try {
    state.goalDetail = await api(`/workspaces/${state.workspace.id}/goals/${state.goalDetail.id}/tasks`, { method:'PUT', body:JSON.stringify({ task_ids:state.goalTaskIds }) });
    state.goalTaskIds = state.goalDetail.tasks.filter((task) => !task.parent_task_id).map((task) => task.id);
    await loadData();
    state.section = 'goal-detail';
    renderSection();
    showToast('Последовательность задач сохранена');
  } catch (error) { showToast(error.message, true); }
}

async function createGoalTask(form) {
  if (!state.goalDetail?.id) { showToast('Сначала сохраните саму цель.', true); return; }
  const fields = new FormData(form);
  const estimate = Number(fields.get('estimate'));
  try {
    // Связь с родителем и тип работы независимы: снятие флажка не должно
    // выбрасывать задачу из уже выбранной ветки.
    const parentTaskId = fields.get('parent_task_id') || null;
    await api(`/workspaces/${state.workspace.id}/tasks`, { method:'POST', body:JSON.stringify({ title:fields.get('title'), goal_id:state.goalDetail.id, parent_task_id:parentTaskId, is_checkpoint:Boolean(fields.get('is_checkpoint')), estimate_minutes:estimate, min_block_minutes:Math.min(30, estimate), preferred_block_minutes:estimate, priority:Number(fields.get('priority')), color:state.goalDetail.color || null }) });
    state.goalTaskFormOpen = false;
    state.goalTaskDraft = { title:'', parentTaskId:'', estimate:60, priority:3 };
    await loadData();
    await openGoalPage(state.goalDetail.id);
    showToast('Задача добавлена в цель');
  } catch (error) { showToast(error.message, true); }
}

function sessionMoment(iso) { return new Date(iso).toLocaleString('ru-RU', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' }); }
function sessionIntervals(sessions) { return sessions.map((session) => `<details class="session-period"><summary><span>${sessionMoment(session.started_at)}${session.ended_at ? ` — ${new Date(session.ended_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}` : ' — сейчас'}</span><strong>${formatSeconds(session.elapsed_seconds)}</strong></summary><div class="session-segments">${session.segments.map((segment) => `<span>${new Date(segment.started_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}–${segment.ended_at ? new Date(segment.ended_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' }) : 'сейчас'} <b>${formatSeconds(segment.elapsed_seconds)}</b></span>`).join('') || '<span>Нет закрытых отрезков.</span>'}</div></details>`).join(''); }
async function setSessionView(mode) { state.sessionView = mode; localStorage.setItem('planner-session-view', mode); if (mode === 'tasks' && !state.taskSessionGroups) { try { state.taskSessionGroups = (await api(`/workspaces/${state.workspace.id}/work-sessions/by-task`)).groups; } catch (error) { showToast(error.message, true); return; } } renderSection(); }

function renderSection() {
  const plan = $('#plan-workspace'); const view = $('#page-view'); plan.hidden = state.section !== 'plan'; view.hidden = state.section === 'plan'; renderTaskPanelVisibility(); if (state.section === 'plan') return;
  const active = state.tasks.filter((item) => item.status === 'ACTIVE');
  if (state.section === 'goal-detail') { renderGoalDetail(view); return; }
  if (state.section === 'tasks') {
    const catalogTasks = sortTasks(state.tasks.filter((task) => taskMatchesKind(task, state.catalogTaskKind)));
    const catalogButton = (kind, title) => `<button class="button ${state.catalogTaskKind === kind ? 'secondary' : 'ghost'}" data-action="catalog-kind-filter" data-kind="${kind}">${title}</button>`;
    const taskActions = (task) => `<div class="catalog-task-actions">${task.status === 'ACTIVE' && canScheduleTask(task) ? `<button class="task-icon" data-action="start" data-id="${task.id}" title="Начать" aria-label="Начать">▶</button><button class="task-icon" data-action="auto-plan-task" data-id="${task.id}" title="Распределить" aria-label="Распределить">◷</button><button class="task-icon" data-action="complete" data-id="${task.id}" title="Готово" aria-label="Готово">✓</button>` : ''}${task.status === 'COMPLETED' ? `<button class="task-icon" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные" aria-label="Вернуть в активные">✓</button>` : task.status === 'ACTIVE' ? `<button class="task-icon" data-action="new-child-task" data-id="${task.id}" title="Добавить чекпоинт" aria-label="Добавить чекпоинт">＋</button>` : ''}<button class="task-icon" data-action="edit-task" data-id="${task.id}" title="Редактировать" aria-label="Редактировать">✎</button><button class="task-icon task-delete" data-action="delete-task" data-id="${task.id}" title="В архив" aria-label="В архив">×</button></div>`;
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ПОЛНЫЙ СПИСОК</p><h2>Задачи</h2><p class="muted">Конечные пункты можно планировать; группы объединяют чекпоинты.</p></div><button class="button primary" data-action="new-task">+ Задача</button></header><div class="catalog-toolbar">${catalogButton('all', 'Все')}${catalogButton('tasks', 'Задачи')}${catalogButton('checkpoints', 'Чекпоинты')}${catalogButton('groups', 'Группы')}</div><div class="task-catalog-grid">${catalogTasks.map((task) => `<article class="catalog-task ${task.status === 'COMPLETED' ? 'completed' : ''} ${task.is_overdue ? 'overdue' : ''} ${task.is_leaf ? '' : 'task-parent'}"><span class="task-color" style="background:${colorForTask(task)}"></span><div class="catalog-task-copy"><div><strong>${task.status === 'COMPLETED' ? '✓ ' : ''}${escapeHtml(task.title)}</strong><span class="status">${task.status === 'COMPLETED' ? 'Готово' : taskKindLabel(task)}</span></div><p>${task.status === 'COMPLETED' ? 'выполнено' : task.parent_task_title ? `внутри «${escapeHtml(task.parent_task_title)}» · ` : ''}${task.status === 'COMPLETED' ? '' : task.is_leaf ? `${task.direction?.name || 'Без направления'} · ${task.deadline_at ? deadlineText(task.deadline_at) : 'без срока'} · ${task.estimate_minutes}м` : `${task.child_count} чекпоинт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'}`}</p></div>${taskActions(task)}</article>`).join('') || '<div class="empty"><strong>Задач этого типа пока нет</strong></div>'}</div>`;
  } else if (state.section === 'directions') {
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ПРЕДМЕТЫ И ПРОЕКТЫ</p><h2>Направления и теги</h2></div><div><button class="button secondary" data-action="new-label">+ Тег</button><button class="button primary" data-action="new-direction">+ Направление</button></div></header><div class="card-grid">${state.directions.map((item) => { const count = active.filter((task) => task.direction?.id === item.id).length; const tags = state.labels.filter((label) => label.direction_id === item.id); return `<article class="direction-card"><span class="direction-dot" style="background:${item.color}"></span><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.kind)} · ${count} активных задач</p><p>По умолчанию: важность ${item.default_priority}${item.default_estimate_minutes ? `, ${formatMinutes(item.default_estimate_minutes)}` : ''}</p><div class="label-row">${tags.map((tag) => `<span style="background:${tag.color || 'var(--blue-soft)'};color:${tag.color ? '#fff' : 'var(--blue)'}">${escapeHtml(tag.name)}</span>`).join('') || '<small>Тегов нет</small>'}</div><div class="direction-actions"><button class="button ghost" data-action="edit-direction" data-id="${item.id}">✎ Изменить</button><button class="button ghost" data-action="delete-direction" data-id="${item.id}">Удалить</button></div></article>`; }).join('') || '<div class="empty"><strong>Направлений пока нет</strong></div>'}</div><section class="settings-card tag-management"><h3>Все теги</h3>${state.labels.length ? state.labels.map((tag) => `<div class="tag-row"><span class="tag-swatch" style="background:${tag.color || '#356AE6'}"></span><strong>${escapeHtml(tag.name)}</strong><span class="muted">${escapeHtml(state.directions.find((item) => item.id === tag.direction_id)?.name || 'Общий')}</span><div class="row-actions"><button class="button ghost" data-action="edit-label" data-id="${tag.id}">✎</button><button class="button ghost" data-action="delete-label" data-id="${tag.id}">Удалить</button></div></div>`).join('') : '<p class="muted">Тегов пока нет.</p>'}</section>`;
  } else if (state.section === 'goals') {
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ДОЛГОСРОЧНЫЙ ФОКУС</p><h2>Цели</h2></div><button class="button primary" data-action="new-goal">+ Цель</button></header><div class="card-grid">${state.goals.map((goal) => `<article class="direction-card goal-card ${goal.is_overdue ? 'overdue' : ''}"><span class="direction-dot" style="background:${goal.color || '#356AE6'}"></span><h3>${escapeHtml(goal.title)}</h3><p>${goal.leaf_task_count ? `${goal.completed_task_count} из ${goal.leaf_task_count} чекпоинтов завершено` : 'Чекпоинтов пока нет'}${goal.deadline_at ? ` · ${deadlineText(goal.deadline_at)}` : ''}</p><div class="goal-card-progress"><span>Достигнуто ${goal.progress_percent || 0}%</span><i><b style="width:${goal.progress_percent || 0}%"></b></i></div><p>${escapeHtml(goal.description || 'Без описания')}</p><div class="direction-actions goal-card-actions">${goal.status === 'ACTIVE' ? `<button class="button secondary" data-action="open-goal" data-id="${goal.id}">Открыть</button><button class="button ghost" data-action="complete-goal" data-id="${goal.id}">Готово</button>` : `<button class="button secondary" data-action="open-goal" data-id="${goal.id}">Открыть</button><button class="button ghost" data-action="reopen-goal" data-id="${goal.id}" title="Вернуть цель в активные">Вернуть</button>`}<button class="button danger" data-action="delete-goal" data-id="${goal.id}">Удалить</button></div></article>`).join('') || '<div class="empty"><strong>Целей пока нет</strong><p>Цель объединяет связанные задачи и помогает держать курс.</p></div>'}</div>`;
  } else if (state.section === 'results') {
    const report = state.report; const byTask = state.sessionView === 'tasks'; const sessionContent = byTask ? (state.taskSessionGroups ? state.taskSessionGroups.length ? `<div class="task-session-list">${state.taskSessionGroups.map((group) => `<details class="task-session-group"><summary><div><strong>${escapeHtml(group.task?.title || 'Общее время')}</strong><small>${group.session_count} сесс. · ${group.task?.goal_title ? escapeHtml(group.task.goal_title) : 'без задачи'}</small></div><b>${formatSeconds(group.actual_seconds)}</b></summary><div class="task-session-periods">${sessionIntervals(group.sessions)}</div></details>`).join('')}</div>` : '<p class="muted">Сессий пока нет.</p>' : '<p class="muted">Загружаю историю по задачам…</p>') : (report?.sessions.length ? report.sessions.map((item) => `<div class="session-row"><span>${escapeHtml(item.task_title || 'Общая работа')}</span><strong>${formatSeconds(item.elapsed_seconds)}</strong><small>${sessionMoment(item.started_at)}</small></div>`).join('') : '<p class="muted">За сегодня сессий пока нет.</p>'); view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ФАКТ И НАГРУЗКА</p><h2>Итоги дня</h2><p class="muted">${dateKey(new Date())}</p></div><div><button class="button secondary" data-action="add-manual">+ Внести время</button><button class="button primary" data-action="refresh-report">Обновить</button></div></header>${state.notifications.filter((item) => item.status === 'OPEN').length ? `<section class="notification-list">${state.notifications.filter((item) => item.status === 'OPEN').map((item) => `<div><strong>Срок пропущен</strong><span>${escapeHtml(item.title)}</span><small>${escapeHtml(item.body || '')}</small></div>`).join('')}</section>` : ''}${report ? `<div class="metrics"><article><span>План</span><strong>${formatSeconds(report.planned_seconds)}</strong></article><article><span>Факт</span><strong>${formatSeconds(report.actual_seconds)}</strong></article><article><span>Выполнено</span><strong>${report.completed_count}</strong></article><article><span>Перенос</span><strong>${report.carryover_count}</strong></article></div><section class="report-section"><div class="report-section-head"><h3>${byTask ? 'Работа по задачам' : 'Сессии сегодня'}</h3><div class="view-toggle"><button class="button ${byTask ? 'ghost' : 'secondary'}" data-action="show-session-timeline">По времени</button><button class="button ${byTask ? 'secondary' : 'ghost'}" data-action="show-session-tasks">По задачам</button></div></div><p class="muted">${byTask ? 'Раскройте задачу, чтобы увидеть все сессии и отдельные интервалы работы.' : 'Хронологический вид за текущий день.'}</p>${sessionContent}</section>` : '<div class="empty">Собираю итог дня…</div>'}`;
  } else if (state.section === 'archive') {
    const archive = state.archive || { tasks:[], goals:[], directions:[], labels:[], fixed_events:[] };
    const sections = [['tasks','Задачи'],['goals','Цели'],['directions','Направления'],['labels','Теги'],['fixed_events','События']];
    const items = archive[state.archiveSection] || [];
    const titleFor = (item) => item.title || item.name || 'Без названия';
    const metaFor = (item) => state.archiveSection === 'tasks' ? `${taskKindLabel(item)} · ${item.status === 'COMPLETED' ? 'была выполнена' : 'в архиве'}` : state.archiveSection === 'fixed_events' ? `${item.local_date || (item.weekday !== null ? 'повторяется еженедельно' : '')} · ${timeValue(item.start_minute)}–${timeValue(item.end_minute)}` : 'в архиве';
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">БЕЗВОЗВРАТНОЕ УДАЛЕНИЕ — ТОЛЬКО ОТСЮДА</p><h2>Архив</h2><p class="muted">Крестик в приложении перемещает сущность сюда. Восстановление возвращает её в работу.</p></div></header><div class="archive-tabs">${sections.map(([key, title]) => `<button class="button ${state.archiveSection === key ? 'secondary' : 'ghost'}" data-action="archive-section" data-archive-section="${key}">${title} <span>${(archive[key] || []).length}</span></button>`).join('')}</div><div class="archive-list">${items.map((item) => `<article class="archive-item"><span class="task-color" style="background:${item.color || '#8A97A1'}"></span><div><strong>${escapeHtml(titleFor(item))}</strong><p>${escapeHtml(metaFor(item))}</p></div><div class="row-actions"><button class="button secondary" data-action="restore-archived" data-entity-type="${state.archiveSection === 'fixed_events' ? 'fixed_event' : state.archiveSection.slice(0, -1)}" data-id="${item.id}">Вернуть</button><button class="button danger" data-action="purge-archived" data-entity-type="${state.archiveSection === 'fixed_events' ? 'fixed_event' : state.archiveSection.slice(0, -1)}" data-id="${item.id}">Удалить навсегда</button></div></article>`).join('') || '<div class="empty"><strong>В этом разделе архива пусто</strong></div>'}</div>`;
  } else renderSettings(view);
}
function renderSettings(view) {
  const weeklyEvents = uniqueEvents();
  view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ЛОКАЛЬНОЕ ПРИЛОЖЕНИЕ</p><h2>Настройки расписания</h2></div></header><section class="settings-card"><h3>Обычная рабочая неделя</h3><p class="muted">Добавляйте несколько промежутков в любой день. Даты с ручным выделением имеют собственные интервалы.</p><div id="template-slots" class="template-slots">${templateRows().join('')}</div><button class="button secondary" data-action="add-template-slot">+ Добавить промежуток</button> <button class="button primary" data-action="save-default-availability">Сохранить шаблон</button></section><section class="settings-card"><h3>Повторяющееся фиксированное расписание</h3><p class="muted">Лекции, занятия и встречи здесь не становятся задачами и не попадают в банк задач.</p>${weeklyEvents.length ? weeklyEvents.map((event) => `<div class="tag-row"><span class="tag-swatch" style="background:${event.color}"></span><strong>${escapeHtml(event.title)}</strong><span class="muted">${event.weekday !== null ? ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][event.weekday] : 'разово'} · ${timeValue(event.start_minute)}–${timeValue(event.end_minute)}</span><div class="row-actions"><button class="button ghost" data-action="edit-event" data-id="${event.id}">✎</button><button class="button ghost" data-action="delete-event" data-id="${event.id}">Удалить</button></div></div>`).join('') : '<p class="muted">Повторяющихся событий пока нет.</p>'}<button class="button secondary" data-action="new-event">+ Добавить расписание</button></section><section class="settings-card"><h3>Подключение</h3><p class="muted">Данные остаются в локальной папке артефактов.</p><button class="button secondary" data-action="api-settings">Изменить адрес API</button></section>`;
  const connectionCard = view.querySelector('.settings-card:last-child');
  connectionCard.insertAdjacentHTML('beforebegin', `<section class="settings-card"><h3>Корзина задач</h3><p class="muted">Удалённые задачи остаются в базе и могут быть восстановлены без потери данных.</p>${state.deletedTasks.length ? state.deletedTasks.map((task) => `<div class="tag-row"><span class="tag-swatch" style="background:${colorForTask(task)}"></span><strong>${escapeHtml(task.title)}</strong><div class="row-actions"><button class="button secondary" data-action="restore" data-id="${task.id}">Восстановить</button></div></div>`).join('') : '<p class="muted">Корзина пока не загружена или пуста.</p>'}<button class="button secondary" data-action="show-trash">Показать корзину</button></section>`);
}
function templateRows() { const slots = state.templateSlots.map((slot) => ({ weekday:slot.weekday, start:slot.start_minute, end:slot.end_minute })); return (slots.length ? slots : [{ weekday:0, start:540, end:720 }]).map(templateRow); }
function templateRow(slot = { weekday:0, start:540, end:720 }) { return `<div class="template-slot"><label class="field">День<select name="template_weekday">${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map((day, index) => `<option value="${index}" ${slot.weekday === index ? 'selected' : ''}>${day}</option>`).join('')}</select></label><label class="field">Начало<input name="template_start" type="time" value="${timeValue(slot.start)}" required /></label><label class="field">Конец<input name="template_end" type="time" value="${timeValue(slot.end)}" required /></label><button class="icon-button compact" type="button" data-action="remove-template-slot" aria-label="Удалить промежуток">×</button></div>`; }
function uniqueEvents() { const all = state.week?.days.flatMap((day) => day.fixed_events) || []; return all.filter((event, index) => all.findIndex((item) => item.id === event.id) === index).map((event) => ({ ...event, start_minute:Number.isInteger(event.start_minute) ? event.start_minute : localMinute(event.start_at), end_minute:Number.isInteger(event.end_minute) ? event.end_minute : localMinute(event.end_at) })); }

function openModal(type, entityId = null, options = {}) {
  const modal = $('#modal'); const task = entityId ? state.tasks.find((item) => item.id === entityId) : null; const direction = entityId ? state.directions.find((item) => item.id === entityId) : null; const label = entityId ? state.labels.find((item) => item.id === entityId) : null; const goalItem = entityId ? state.goals.find((item) => item.id === entityId) : null; const eventItem = entityId ? uniqueEvents().find((item) => item.id === entityId) : null; const blockItem = entityId ? state.week?.days.flatMap((item) => item.blocks).find((item) => item.id === entityId) : null; const day = state.week?.days.find((item) => item.date === dateKey(state.weekStart)); const preselectedGoalId = task?.goal_id || options.goalId || ''; const preselectedParentId = task?.parent_task_id || options.parentTaskId || '';
  const colorField = (value) => `<input name="color" class="color-choice" type="color" value="${value || '#356AE6'}" aria-label="Цвет" />`;
  const parentChoices = parentTaskCandidates(task?.id || '').map((item) => `<button type="button" class="parent-choice" data-action="select-task-parent" data-id="${item.id}" data-title="${escapeHtml(item.title)}">${escapeHtml(item.title)}<small>${taskKindLabel(item)}</small></button>`).join('');
  const taskTemplate = `<div class="form-grid task-form"><section class="form-section"><label class="field">Название<input name="title" required autofocus value="${escapeHtml(task?.title || '')}" /></label></section><section class="form-section"><h3>Связи</h3><label class="field">Цель<select name="goal_id"><option value="">Без цели</option>${state.goals.filter((item) => item.status === 'ACTIVE').map((item) => `<option value="${item.id}" ${preselectedGoalId === item.id ? 'selected' : ''}>${escapeHtml(item.title)}</option>`).join('')}</select></label><input type="hidden" name="parent_task_id" value="${preselectedParentId}" /><label class="checkpoint-switch"><input name="is_checkpoint" type="checkbox" ${preselectedParentId ? 'checked' : ''}/> Это чекпоинт</label><div class="checkpoint-parent" id="checkpoint-parent-controls" ${preselectedParentId ? '' : 'hidden'}><span>Родитель: <strong id="checkpoint-parent-name">${escapeHtml(state.tasks.find((item) => item.id === preselectedParentId)?.title || 'не выбран')}</strong></span><details><summary>Выбрать другого родителя</summary><div class="parent-choice-list">${parentChoices || '<small>Подходящих задач нет.</small>'}</div></details><small class="modal-note">Снимите галочку, чтобы сделать задачу самостоятельной. Параметры чекпоинта можно менять независимо.</small></div></section><section class="form-section"><h3>Планирование</h3><label class="field">Направление<select name="direction_id"><option value="">Без направления</option>${state.directions.map((item) => `<option value="${item.id}" ${task?.direction?.id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><div class="inline-fields"><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${task?.estimate_minutes || 60}" required /></label><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${task?.priority === value || !task && value === 3 ? 'selected' : ''}>${value}</option>`).join('')}</select></label></div><div class="inline-fields"><label class="field">Цвет задачи${colorField(task?.color || task?.direction?.color || '#356AE6')}</label><label class="field">Срок<input name="deadline" type="datetime-local" value="${task?.deadline_at ? task.deadline_at.slice(0, 16) : ''}" /></label></div><div class="task-options"><label><input name="can_split" type="checkbox" ${task?.can_split ? 'checked' : ''}/> Разрешить разбивать на несколько промежутков</label><label><input name="repeat_rule" type="checkbox" ${task?.repeat_rule === 'WEEKLY' ? 'checked' : ''}/> Повторять еженедельно</label></div></section>${state.labels.length ? `<fieldset class="label-picker"><legend>Теги</legend>${state.labels.map((item) => `<label><input type="checkbox" name="label_ids" value="${item.id}" ${task?.labels.some((tag) => tag.id === item.id) ? 'checked' : ''}/> ${escapeHtml(item.name)}</label>`).join('')}</fieldset>` : ''}</div>`;
  const templates = {
    task:{ title:task ? 'Изменить задачу' : 'Новая задача', submit:task ? 'Сохранить' : 'Добавить задачу', html:taskTemplate },
    goal:{ title:goalItem ? 'Изменить цель' : 'Новая цель', submit:goalItem ? 'Сохранить' : 'Создать цель', html:`<div class="form-grid"><label class="field">Название<input name="title" required autofocus value="${escapeHtml(goalItem?.title || '')}" /></label><label class="field">Направление<select name="direction_id"><option value="">Без направления</option>${state.directions.map((item) => `<option value="${item.id}" ${goalItem?.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Описание<textarea name="description" rows="4">${escapeHtml(goalItem?.description || '')}</textarea></label><div class="inline-fields"><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${goalItem?.priority === value || !goalItem && value === 3 ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Цвет${colorField(goalItem?.color || '#356AE6')}</label></div><label class="field">Срок<input name="deadline" type="datetime-local" value="${goalItem?.deadline_at ? goalItem.deadline_at.slice(0, 16) : ''}" /></label></div>` },
    direction:{ title:direction ? 'Изменить направление' : 'Новое направление', submit:direction ? 'Сохранить' : 'Создать направление', html:`<div class="form-grid"><label class="field">Название<input name="name" required autofocus value="${escapeHtml(direction?.name || '')}" /></label><div class="inline-fields"><label class="field">Тип<input name="kind" required value="${escapeHtml(direction?.kind || '')}" placeholder="Учёба, работа, спорт…" /></label><label class="field">Цвет${colorField(direction?.color || '#356AE6')}</label></div><div class="inline-fields"><label class="field">Важность по умолчанию<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${direction?.default_priority === value || !direction && value === 3 ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${direction?.default_estimate_minutes || ''}" /></label></div></div>` },
    label:{ title:label ? 'Изменить тег' : 'Новый тег', submit:label ? 'Сохранить' : 'Создать тег', html:`<div class="form-grid"><label class="field">Название<input name="name" required autofocus value="${escapeHtml(label?.name || '')}" /></label><label class="field">Направление<select name="direction_id"><option value="">Общий тег</option>${state.directions.map((item) => `<option value="${item.id}" ${label?.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Цвет${colorField(label?.color || '#356AE6')}</label></div>` },
    worktime:{ title:'Рабочее время на дату', submit:'Сохранить интервалы', html:`<div class="form-grid"><label class="field">Дата<input name="date" type="date" value="${dateKey(state.weekStart)}" required /></label><div id="work-slots">${(day?.availability.length ? day.availability.map((slot) => workSlot(localMinute(slot.start_at), localMinute(slot.end_at))).join('') : workSlot(540,720))}</div><button type="button" class="button secondary" data-action="add-work-slot">+ Добавить промежуток</button><p class="modal-note">Удалите все строки, чтобы освободить день от рабочего времени.</p></div>` },
    block:{ title:'Изменить блок в плане', submit:'Сохранить время', html:`<div class="form-grid"><p class="modal-note">${escapeHtml(blockItem?.task_title || 'Задача')}</p><label class="field">Дата<input name="date" type="date" value="${blockItem ? dateKey(blockItem.start_at) : dateKey(state.weekStart)}" required /></label><div class="inline-fields"><label class="field">Начало<input name="start" type="time" step="900" value="${blockItem ? timeValue(localMinute(blockItem.start_at)) : '09:00'}" required /></label><label class="field">Конец<input name="end" type="time" step="900" value="${blockItem ? timeValue(localMinute(blockItem.end_at)) : '10:00'}" required /></label></div></div>` },
    complete:{ title:'Завершить задачу', submit:'Завершить', html:`<div class="form-grid"><label class="field">Фактическое время, минут<input name="actual_minutes" type="number" min="1" step="5" placeholder="Например, 75" /></label>${task?.repeat_rule === 'WEEKLY' ? '<p class="modal-note">Срок задачи не изменится. При необходимости верните задачу в активные кнопкой с галочкой.</p>' : ''}</div>` },
    event:{ title:eventItem ? 'Изменить расписание' : 'Неподвижное событие', submit:eventItem ? 'Сохранить' : 'Добавить событие', html:`<div class="form-grid"><label class="field">Название<input name="title" required value="${escapeHtml(eventItem?.title || '')}" /></label><label class="field">Дата<input name="date" type="date" value="${eventItem?.local_date || options.eventDate || dateKey(state.weekStart)}" required /></label><div class="inline-fields"><label class="field">Начало<input name="start" type="time" value="${eventItem ? timeValue(eventItem.start_minute) : '10:00'}" required /></label><label class="field">Конец<input name="end" type="time" value="${eventItem ? timeValue(eventItem.end_minute) : '11:30'}" required /></label></div><label><input type="checkbox" name="weekly" ${eventItem?.weekday !== null && eventItem ? 'checked' : ''}/> Повторять каждую неделю</label><label class="field">Цвет${colorField(eventItem?.color || '#D9DDE2')}</label></div>` },
    manual:{ title:'Внести фактическое время', submit:'Сохранить время', html:`<div class="form-grid"><label class="field">Задача<select name="task_id"><option value="">Общая работа</option>${state.tasks.filter((item) => item.status === 'ACTIVE').map((item) => `<option value="${item.id}">${escapeHtml(item.title)}</option>`).join('')}</select></label><div class="inline-fields"><label class="field">Начало<input name="started_at" type="datetime-local" value="${dateKey(new Date())}T09:00" required /></label><label class="field">Конец<input name="ended_at" type="datetime-local" value="${dateKey(new Date())}T10:00" required /></label></div></div>` },
    api:{ title:'Адрес сервера', submit:'Подключить', html:`<div class="form-grid"><label class="field">API<input name="api" value="${state.apiBase}" required /></label></div>` },
  };
  const template = templates[type];
  $('#modal-kicker').textContent = ['task','goal','direction','label','event'].includes(type) ? 'СОЗДАНИЕ И ИЗМЕНЕНИЕ' : 'НАСТРОЙКА';
  $('#modal-title').textContent = template.title;
  $('#modal-submit').textContent = template.submit;
  $('#modal-content').innerHTML = template.html;
  modal.dataset.type = type;
  modal.dataset.entityId = entityId || '';
  $('#modal-delete').hidden = !(type === 'task' && entityId);
  if (type === 'task') {
    const directionInput = $('#modal-content [name="direction_id"]');
    const applyParent = () => {
      const inherited = state.directions.find((item) => item.id === directionInput.value);
      if (!inherited) return;
      if (!entityId) {
        $('#modal-content [name="estimate"]').value = inherited.default_estimate_minutes || 60;
        $('#modal-content [name="priority"]').value = inherited.default_priority;
        $('#modal-content [name="color"]').value = inherited.color;
      }
      document.querySelectorAll('#modal-content [name="label_ids"]').forEach((input) => {
        const label = state.labels.find((item) => item.id === input.value);
        input.checked = Boolean(label?.direction_id && label.direction_id === inherited.id);
      });
    };
    directionInput.addEventListener('change', applyParent);
    const goalInput = $('#modal-content [name="goal_id"]');
    goalInput?.addEventListener('change', () => {
      const goal = state.goals.find((item) => item.id === goalInput.value);
      if (!goal?.direction_id) return;
      directionInput.value = goal.direction_id;
      applyParent();
    });
    const checkpointInput = $('#modal-content [name="is_checkpoint"]');
    checkpointInput.checked = Boolean(task?.is_checkpoint || preselectedParentId);
    const checkpointNote = $('#checkpoint-parent-controls .modal-note');
    if (checkpointNote) checkpointNote.textContent = 'Флажок определяет, можно ли выполнять эту задачу вместе с её потомками. Связь с родителем он не меняет.';
    checkpointInput?.addEventListener('change', () => {
      const controls = $('#checkpoint-parent-controls');
      controls.hidden = !checkpointInput.checked;
      const parentInput = $('#modal-content [name="parent_task_id"]');
      // Признак чекпоинта не меняет иерархическую связь: иначе снятие
      // галочки неожиданно выбрасывает задачу в корень дерева.
    });
  }
  modal.showModal();
}
function workSlot(start = 540, end = 720) { return `<div class="inline-fields work-slot"><label class="field">Начало<input name="work_start" type="time" value="${timeValue(start)}" required /></label><label class="field">Конец<input name="work_end" type="time" value="${timeValue(end)}" required /></label><button type="button" class="icon-button compact" data-action="remove-work-slot" aria-label="Удалить промежуток">×</button></div>`; }

async function submitModal(event) {
  event.preventDefault(); const modal = $('#modal'); const fields = new FormData(event.currentTarget); const type = modal.dataset.type; const entityId = modal.dataset.entityId; const id = state.workspace.id;
  try {
    if (type === 'task') { const deadline = fields.get('deadline'); const estimate = Number(fields.get('estimate')); const parentTaskId = fields.get('parent_task_id') || null; const payload = { title:fields.get('title'), direction_id:fields.get('direction_id') || null, goal_id:fields.get('goal_id') || null, parent_task_id:parentTaskId, is_checkpoint:Boolean(fields.get('is_checkpoint')), color:fields.get('color'), label_ids:fields.getAll('label_ids'), estimate_minutes:estimate, min_block_minutes:Math.min(30, estimate), preferred_block_minutes:estimate, can_split:Boolean(fields.get('can_split')), priority:Number(fields.get('priority')), deadline_at:deadline ? new Date(deadline).toISOString() : null, repeat_rule:fields.get('repeat_rule') ? 'WEEKLY' : 'NONE' }; await api(entityId ? `/workspaces/${id}/tasks/${entityId}` : `/workspaces/${id}/tasks`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'goal') { const deadline = fields.get('deadline'); const payload = { title:fields.get('title'), direction_id:fields.get('direction_id') || null, description:fields.get('description') || null, color:fields.get('color'), priority:Number(fields.get('priority')), deadline_at:deadline ? new Date(deadline).toISOString() : null }; await api(entityId ? `/workspaces/${id}/goals/${entityId}` : `/workspaces/${id}/goals`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'direction') { const payload = { name:fields.get('name'), kind:fields.get('kind'), color:fields.get('color'), default_priority:Number(fields.get('priority')), default_estimate_minutes:fields.get('estimate') ? Number(fields.get('estimate')) : null }; await api(entityId ? `/workspaces/${id}/directions/${entityId}` : `/workspaces/${id}/directions`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'label') { const payload = { name:fields.get('name'), direction_id:fields.get('direction_id') || null, color:fields.get('color') }; await api(entityId ? `/workspaces/${id}/labels/${entityId}` : `/workspaces/${id}/labels`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'worktime') { const slots = fields.getAll('work_start').map((start, index) => start && fields.getAll('work_end')[index] ? { start_minute:toMinutes(start), end_minute:toMinutes(fields.getAll('work_end')[index]) } : null).filter(Boolean); await api(`/workspaces/${id}/availability/dates/${fields.get('date')}`, { method:'PUT', body:JSON.stringify({ slots }) }); }
    if (type === 'block') { const block = state.week?.days.flatMap((day) => day.blocks).find((item) => item.id === entityId); const start = new Date(`${fields.get('date')}T${fields.get('start')}`); const end = new Date(`${fields.get('date')}T${fields.get('end')}`); const updated = await api(`/workspaces/${id}/blocks/${entityId}`, { method:'PATCH', body:JSON.stringify({ start_at:start.toISOString(), end_at:end.toISOString(), is_pinned:true, allow_conflict:true, move_task_deadline:true }) }); updateTaskDeadlineLocally(block?.task_id, updated.end_at); removeBlockLocally(entityId, true); mergeBlocks([updated], true); modal.close(); renderTasks(); renderWeek(); showToast('Время блока и срок задачи обновлены'); scheduleBackgroundRefresh(); return; }
    if (type === 'complete') { const updated = await api(`/workspaces/${id}/tasks/${entityId}/complete`, { method:'POST', body:JSON.stringify({ actual_minutes:fields.get('actual_minutes') ? Number(fields.get('actual_minutes')) : null }) }); applyTaskStatusLocally(updated); if (updated.status === 'COMPLETED' && state.dailyProgress) state.dailyProgress.completed_count += 1; modal.close(); renderTasks(); renderWeek(); renderDailySuccess(); showToast('Задача завершена — блок остался в плане'); scheduleBackgroundRefresh(); return; }
    if (type === 'event') { const date = String(fields.get('date')); const eventDate = new Date(`${date}T12:00:00`); const payload = { title:fields.get('title'), start_minute:toMinutes(fields.get('start')), end_minute:toMinutes(fields.get('end')), weekday:fields.get('weekly') ? (eventDate.getDay() + 6) % 7 : null, local_date:fields.get('weekly') ? null : date, color:fields.get('color') }; await api(entityId ? `/workspaces/${id}/fixed-events/${entityId}` : `/workspaces/${id}/fixed-events`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'manual') await api(`/workspaces/${id}/work-sessions/manual`, { method:'POST', body:JSON.stringify({ task_id:fields.get('task_id') || null, started_at:new Date(fields.get('started_at')).toISOString(), ended_at:new Date(fields.get('ended_at')).toISOString() }) });
    if (type === 'api') { state.apiBase = String(fields.get('api')).replace(/\/$/, ''); localStorage.setItem('planner-api', state.apiBase); }
    modal.close();
    if (type === 'api') { await initialize(); return; }
    await loadData();
    if (state.section === 'goal-detail' && state.goalDetail?.id) await openGoalPage(state.goalDetail.id);
  } catch (error) { showToast(error.message, true); }
}

async function planSelected() { const selected = state.tasks.filter((task) => state.selected.has(task.id) && canScheduleTask(task) && task.planning_status !== 'PLANNED').map((task) => task.id); if (!selected.length) { showToast('Выберите хотя бы одну задачу, которую можно разместить.', true); return; } if (!state.week.days.reduce((sum, day) => sum + day.free_minutes, 0)) { showToast('Сначала задайте рабочее время.', true); openModal('worktime'); return; } try { const end = new Date(state.weekStart); end.setDate(end.getDate() + 7); state.plan = await api(`/workspaces/${state.workspace.id}/planner/runs`, { method:'POST', body:JSON.stringify({ task_ids:selected, horizon_start:state.weekStart.toISOString(), horizon_end:end.toISOString() }) }); state.planPanelHidden = false; renderWeek(); renderPlan(); } catch (error) { showToast(error.message, true); } }
async function applyPlan() { try { const conflicts = state.plan.proposals.filter((item) => item.has_conflict); const allowConflicts = conflicts.length > 0 && confirm(`В варианте есть пересечения: ${conflicts.length}. Разместить их с пометкой конфликта?`); const accepted = allowConflicts ? state.plan.proposals.map((item) => item.id) : state.plan.proposals.filter((item) => !item.has_conflict).map((item) => item.id); const result = await api(`/workspaces/${state.workspace.id}/planner/runs/${state.plan.id}/apply`, { method:'POST', body:JSON.stringify({ accepted_proposal_ids:accepted, allow_conflicts:allowConflicts }) }); mergeBlocks(result.blocks, true); state.plan = null; state.selected.clear(); renderTasks(); renderWeek(); renderPlan(); showToast('Блоки сразу добавлены в расписание'); scheduleBackgroundRefresh(); } catch (error) { showToast(error.message, true); } }
async function startTask(taskId) { try { state.session = await api(`/workspaces/${state.workspace.id}/work-sessions/start`, { method:'POST', body:JSON.stringify({ task_id:taskId }) }); state.sessionBarHidden = false; localStorage.removeItem('planner-session-hidden'); renderSession(); } catch (error) { showToast(error.message, true); } }
async function toggleSession() { try { const action = state.session.status === 'RUNNING' ? 'pause' : 'resume'; state.session = await api(`/workspaces/${state.workspace.id}/work-sessions/${action}`, { method:'POST' }); renderSession(); } catch (error) { showToast(error.message, true); } }
async function finishSession() { try { await api(`/workspaces/${state.workspace.id}/work-sessions/finish`, { method:'POST' }); state.session = null; renderSession(); showToast('Сессия завершена'); } catch (error) { showToast(error.message, true); } }
async function loadReport() { try { state.report = await api(`/workspaces/${state.workspace.id}/reports/daily/${dateKey(new Date())}`); state.dailyProgress = state.report; state.taskSessionGroups = null; renderDailySuccess(); renderSection(); } catch (error) { showToast(error.message, true); } }
function applyTaskStatusLocally(task) { const taskIndex = state.tasks.findIndex((item) => item.id === task.id); if (taskIndex >= 0) state.tasks[taskIndex] = { ...state.tasks[taskIndex], ...task }; state.week?.days.forEach((day) => day.blocks.forEach((block) => { if (block.task_id === task.id) block.task_status = task.status; })); }
async function transitionTask(taskId, action) { try { const updated = await api(`/workspaces/${state.workspace.id}/tasks/${taskId}/${action}`, { method:'POST' }); applyTaskStatusLocally(updated); renderTasks(); renderWeek(); renderDailySuccess(); await loadData(); } catch (error) { showToast(error.message, true); } }
async function deleteEntity(path, message) { if (!confirm('Удалить? Действие можно будет отменить только вручную.')) return; try { await api(path, { method:'DELETE' }); const taskId = path.match(/\/tasks\/([^/]+)$/)?.[1]; if (taskId) removeArchivedTaskTreeLocally(taskId); await loadData(); showToast(message); } catch (error) { showToast(error.message, true); } }
function updateTaskDeadlineLocally(taskId, deadlineAt) { const index = state.tasks.findIndex((task) => task.id === taskId); if (index >= 0) state.tasks[index] = { ...state.tasks[index], deadline_at:deadlineAt, is_overdue:false }; state.notifications?.forEach((item) => { if (item.task_id === taskId && item.kind === 'OVERDUE') item.status = 'RESOLVED'; }); }
async function deleteTaskFromModal() { const modal = $('#modal'); const taskId = modal.dataset.type === 'task' ? modal.dataset.entityId : ''; if (!taskId) return; if (!confirm('Удалить задачу? Её можно будет восстановить из корзины.')) return; try { await api(`/workspaces/${state.workspace.id}/tasks/${taskId}`, { method:'DELETE' }); removeArchivedTaskTreeLocally(taskId); modal.close(); await loadData(); if (state.section === 'goal-detail' && state.goalDetail?.id) await openGoalPage(state.goalDetail.id); showToast('Задача удалена'); } catch (error) { showToast(error.message, true); } }
async function changeGoalStatus(goalId, action) {
  try {
    await api(`/workspaces/${state.workspace.id}/goals/${goalId}/${action}`, { method:'POST' });
    await loadData();
    if (state.section === 'goal-detail') await openGoalPage(goalId);
    showToast(action === 'reopen' ? 'Цель возвращена в активные' : 'Цель отмечена готовой');
  } catch (error) { showToast(error.message, true); }
}
async function deleteGoal(goalId) {
  if (!confirm('Удалить цель? Её задачи, план и история работы останутся — они станут задачами без цели.')) return;
  try {
    await api(`/workspaces/${state.workspace.id}/goals/${goalId}`, { method:'DELETE' });
    state.goalDetail = null; state.goalTaskIds = []; state.section = 'goals';
    await loadData();
    renderSection();
    showToast('Цель удалена, задачи сохранены');
  } catch (error) { showToast(error.message, true); }
}
async function loadDeletedTasks() { try { const tasks = await api(`/workspaces/${state.workspace.id}/tasks?include_completed=true&include_deleted=true`); state.deletedTasks = tasks.filter((task) => task.deleted_at); renderSection(); } catch (error) { showToast(error.message, true); } }
async function loadArchive() { try { state.archive = await api(`/workspaces/${state.workspace.id}/archive`); renderSection(); } catch (error) { showToast(error.message, true); } }
async function restoreArchivedEntity(entityType, entityId) { try { await api(`/workspaces/${state.workspace.id}/archive/${entityType}/${entityId}/restore`, { method:'POST' }); await loadData(); await loadArchive(); showToast('Сущность возвращена из архива'); } catch (error) { showToast(error.message, true); } }
async function purgeArchivedEntity(entityType, entityId) { if (!confirm('Удалить навсегда? Восстановить это будет нельзя.')) return; try { await api(`/workspaces/${state.workspace.id}/archive/${entityType}/${entityId}`, { method:'DELETE' }); await loadArchive(); showToast('Сущность удалена навсегда'); } catch (error) { showToast(error.message, true); } }

function showToast(message, error = false) { const toast = $('#toast'); $('#toast-message').textContent = message; toast.className = `toast show ${error ? 'error' : ''}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => { toast.className = 'toast'; }, 5000); }
function renderOffline() { $('#week-range').textContent = 'Нет подключения'; $('#task-list').innerHTML = '<div class="empty"><strong>Сервер не запущен</strong></div>'; }

document.addEventListener('click', async (event) => {
  const start = event.target.closest('[data-start-task]'); if (start) { startTask(start.dataset.startTask); return; }
  const taskCard = event.target.closest('[data-task-card]'); if (taskCard && !event.target.closest('button,input')) { const id = taskCard.dataset.taskCard; const task = state.tasks.find((item) => item.id === id); if (task && canScheduleTask(task) && task.planning_status !== 'PLANNED') { state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id); renderTasks(); } return; }
  const fixed = event.target.closest('[data-fixed-event]'); if (fixed && !event.target.closest('[data-action]')) return;
  const action = event.target.closest('[data-action]'); if (!action) return; const id = action.dataset.id;
  if (action.dataset.action === 'new-task') openModal('task');
  if (action.dataset.action === 'select-task-parent') {
    const parent = state.tasks.find((item) => item.id === id);
    const parentInput = $('#modal-content [name="parent_task_id"]');
    if (parent && parentInput) {
      parentInput.value = id; $('#checkpoint-parent-name').textContent = parent.title;
      action.closest('details')?.removeAttribute('open');
      const goalInput = $('#modal-content [name="goal_id"]'); const directionInput = $('#modal-content [name="direction_id"]');
      if (!$('#modal').dataset.entityId) { if (parent.goal_id) goalInput.value = parent.goal_id; if (parent.direction?.id) directionInput.value = parent.direction.id; $('#modal-content [name="priority"]').value = parent.priority; $('#modal-content [name="color"]').value = colorForTask(parent); }
    }
  }
  if (action.dataset.action === 'select-goal-task-parent') {
    const parent = goalTaskForDetail(id);
    if (parent) {
      state.goalTaskDraft.parentTaskId = id;
      const parentInput = $('#goal-new-task-form [name="parent_task_id"]');
      if (parentInput) parentInput.value = id;
      $('#goal-checkpoint-parent-name').textContent = parent.title;
      action.closest('details')?.removeAttribute('open');
    }
  }
  if (action.dataset.action === 'catalog-kind-filter') { state.catalogTaskKind = action.dataset.kind; renderSection(); }
  if (action.dataset.action === 'new-child-task') { const parent = state.tasks.find((item) => item.id === id) || goalTaskForDetail(id); openModal('task', null, { goalId:parent?.goal_id || state.goalDetail?.id, parentTaskId:id }); }
  if (action.dataset.action === 'new-goal') openGoalPage();
  if (action.dataset.action === 'toggle-task-node') { state.collapsedTaskNodes.has(id) ? state.collapsedTaskNodes.delete(id) : state.collapsedTaskNodes.add(id); renderSection(); }
  if (action.dataset.action === 'new-direction') openModal('direction');
  if (action.dataset.action === 'new-label') openModal('label');
  if (action.dataset.action === 'new-event') openModal('event');
  if (action.dataset.action === 'edit-task') openModal('task', id);
  if (action.dataset.action === 'edit-goal') openGoalPage(id);
  if (action.dataset.action === 'open-goal') openGoalPage(id);
  if (action.dataset.action === 'open-goals') { state.section = 'goals'; renderSection(); }
  if (action.dataset.action === 'toggle-goal-task-form') { if (state.goalTaskFormOpen) rememberGoalTaskDraft(); state.goalTaskFormOpen = !state.goalTaskFormOpen; renderSection(); }
  if (action.dataset.action === 'hide-goal-task-form') { rememberGoalTaskDraft(); state.goalTaskFormOpen = false; renderSection(); }
  if (action.dataset.action === 'back-to-goals') { state.section = 'goals'; state.goalDetail = null; renderSection(); }
  if (action.dataset.action === 'edit-goal-task') openModal('task', id, { goalId:state.goalDetail?.id });
  if (action.dataset.action === 'goal-task-remove') { state.goalTaskIds = state.goalTaskIds.filter((taskId) => taskId !== id); await saveGoalTaskSequence(); }
  if (action.dataset.action === 'delete-goal-task') { if (!confirm('Удалить задачу? Её можно будет восстановить из корзины.')) return; try { await api(`/workspaces/${state.workspace.id}/tasks/${id}`, { method:'DELETE' }); removeArchivedTaskTreeLocally(id); await loadData(); await openGoalPage(state.goalDetail.id); showToast('Задача удалена'); } catch (error) { showToast(error.message, true); } }
  if (action.dataset.action === 'goal-task-up' || action.dataset.action === 'goal-task-down') { const index = state.goalTaskIds.indexOf(id); const next = action.dataset.action === 'goal-task-up' ? index - 1 : index + 1; if (index >= 0 && next >= 0 && next < state.goalTaskIds.length) { [state.goalTaskIds[index], state.goalTaskIds[next]] = [state.goalTaskIds[next], state.goalTaskIds[index]]; await saveGoalTaskSequence(); } }
  if (action.dataset.action === 'edit-direction') openModal('direction', id);
  if (action.dataset.action === 'edit-label') openModal('label', id);
  if (action.dataset.action === 'edit-event') openModal('event', id, { eventDate:action.dataset.date });
  if (action.dataset.action === 'edit-block') openModal('block', id);
  if (action.dataset.action === 'complete') openModal('complete', id);
  if (action.dataset.action === 'complete-goal') changeGoalStatus(id, 'complete');
  if (action.dataset.action === 'reopen-goal') changeGoalStatus(id, 'reopen');
  if (action.dataset.action === 'delete-goal') deleteGoal(id);
  if (action.dataset.action === 'start') startTask(id);
  if (action.dataset.action === 'reopen-task') transitionTask(id, 'reopen');
  if (action.dataset.action === 'auto-plan-task') { state.selected = new Set([id]); renderTasks(); planSelected(); }
  if (action.dataset.action === 'delete-task') deleteEntity(`/workspaces/${state.workspace.id}/tasks/${id}`, 'Задача удалена');
  if (action.dataset.action === 'delete-direction') deleteEntity(`/workspaces/${state.workspace.id}/directions/${id}`, 'Направление удалено');
  if (action.dataset.action === 'delete-label') deleteEntity(`/workspaces/${state.workspace.id}/labels/${id}`, 'Тег удалён');
  if (action.dataset.action === 'delete-event') deleteEntity(`/workspaces/${state.workspace.id}/fixed-events/${id}`, 'Расписание удалено');
  if (action.dataset.action === 'cancel-block') cancelBlock(id);
  if (action.dataset.action === 'complete-block') completeBlock(id);
  if (action.dataset.action === 'restore') transitionTask(id, 'restore');
  if (action.dataset.action === 'show-trash') loadDeletedTasks();
  if (action.dataset.action === 'open-archive') { state.section = 'archive'; loadArchive(); }
  if (action.dataset.action === 'archive-section') { state.archiveSection = action.dataset.archiveSection; renderSection(); }
  if (action.dataset.action === 'restore-archived') restoreArchivedEntity(action.dataset.entityType, id);
  if (action.dataset.action === 'purge-archived') purgeArchivedEntity(action.dataset.entityType, id);
  if (action.dataset.action === 'refresh-report') loadReport();
  if (action.dataset.action === 'show-session-timeline') setSessionView('timeline');
  if (action.dataset.action === 'show-session-tasks') setSessionView('tasks');
  if (action.dataset.action === 'add-manual') openModal('manual');
  if (action.dataset.action === 'api-settings') openModal('api');
  if (action.dataset.action === 'add-work-slot') $('#work-slots').insertAdjacentHTML('beforeend', workSlot());
  if (action.dataset.action === 'remove-work-slot') action.closest('.work-slot').remove();
  if (action.dataset.action === 'remove-availability-slot') removeAvailabilitySlot(action.dataset.date, Number(action.dataset.start), Number(action.dataset.end));
  if (action.dataset.action === 'add-template-slot') $('#template-slots').insertAdjacentHTML('beforeend', templateRow());
  if (action.dataset.action === 'remove-template-slot') action.closest('.template-slot').remove();
  if (action.dataset.action === 'save-default-availability') saveDefaultAvailability();
});
async function removeAvailabilitySlot(date, start, end) { const day = state.week?.days.find((item) => item.date === date); const slots = (day?.availability || []).map((slot) => ({ start:localMinute(slot.start_at), end:localMinute(slot.end_at) })).filter((slot) => slot.start !== start || slot.end !== end); try { await api(`/workspaces/${state.workspace.id}/availability/dates/${date}`, { method:'PUT', body:JSON.stringify({ slots:slots.map((slot) => ({ start_minute:slot.start, end_minute:slot.end })) }) }); await loadData(); showToast(`Свободное время ${timeValue(start)}–${timeValue(end)} удалено`); } catch (error) { showToast(error.message, true); } }
async function cancelBlock(blockId) { if (!confirm('Убрать этот блок из плана?')) return; try { await api(`/workspaces/${state.workspace.id}/blocks/${blockId}`, { method:'DELETE' }); removeBlockLocally(blockId, true); renderTasks(); renderWeek(); showToast('Блок убран из плана'); scheduleBackgroundRefresh(); } catch (error) { showToast(error.message, true); } }
async function completeBlock(blockId) { const block = state.week?.days.flatMap((day) => day.blocks).find((item) => item.id === blockId); if (!block?.task_id) return; try { const updated = await api(`/workspaces/${state.workspace.id}/tasks/${block.task_id}/complete`, { method:'POST', body:JSON.stringify({}) }); applyTaskStatusLocally(updated); renderTasks(); renderWeek(); renderDailySuccess(); await loadData(); showToast('Задача завершена во всех разделах'); } catch (error) { showToast(error.message, true); } }
async function saveDefaultAvailability() { const rows = [...document.querySelectorAll('.template-slot')]; const slots = rows.map((row) => ({ weekday:Number(row.querySelector('[name="template_weekday"]').value), start_minute:toMinutes(row.querySelector('[name="template_start"]').value), end_minute:toMinutes(row.querySelector('[name="template_end"]').value) })); try { await api(`/workspaces/${state.workspace.id}/availability/default`, { method:'PUT', body:JSON.stringify({ name:'Обычная неделя', slots }) }); await loadData(); showToast('Шаблон рабочей недели сохранён'); } catch (error) { showToast(error.message, true); } }

$('#task-search').addEventListener('input', (event) => { state.search = event.target.value; renderTasks(); });
$('#task-sort').addEventListener('change', (event) => { state.taskSort = event.target.value; localStorage.setItem('planner-task-sort', state.taskSort); renderTasks(); if (state.section === 'tasks') renderSection(); });
document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => { document.querySelector('.filter.active').classList.remove('active'); button.classList.add('active'); state.filter = button.dataset.filter; renderTasks(); }));
document.querySelectorAll('.kind-filter').forEach((button) => button.addEventListener('click', () => { document.querySelector('.kind-filter.active')?.classList.remove('active'); button.classList.add('active'); state.taskKindFilter = button.dataset.kindFilter; state.selected.clear(); renderTasks(); }));
document.querySelectorAll('.nav-item[data-section]').forEach((button) => button.addEventListener('click', () => { document.querySelector('.nav-item.active').classList.remove('active'); button.classList.add('active'); state.section = button.dataset.section; renderSection(); if (state.section === 'results') loadReport(); if (state.section === 'archive') loadArchive(); }));
$('#theme-toggle').addEventListener('click', () => { const dark = document.documentElement.dataset.theme !== 'dark'; document.documentElement.dataset.theme = dark ? 'dark' : ''; localStorage.setItem('planner-theme', dark ? 'dark' : 'light'); $('#theme-toggle').setAttribute('aria-pressed', String(dark)); });
$('#add-task').addEventListener('click', () => openModal('task')); $('#quick-task').addEventListener('click', () => openModal('task')); $('#edit-worktime').addEventListener('click', () => openModal('worktime')); $('#record-worktime').addEventListener('click', () => openModal('manual')); $('#add-event').addEventListener('click', () => openModal('event')); $('#hide-overview').addEventListener('click', () => { state.planOverviewHidden = true; localStorage.setItem('planner-overview-hidden', 'true'); renderPlanOverview(); }); $('#show-overview').addEventListener('click', () => { state.planOverviewHidden = false; localStorage.removeItem('planner-overview-hidden'); renderPlanOverview(); }); $('#api-settings').addEventListener('click', () => openModal('api'));
$('#toggle-nav-panel').addEventListener('click', () => { state.navPanelHidden = true; localStorage.setItem('planner-nav-panel-hidden', 'true'); renderTaskPanelVisibility(); });
$('#show-nav-panel').addEventListener('click', () => { state.navPanelHidden = false; localStorage.removeItem('planner-nav-panel-hidden'); renderTaskPanelVisibility(); });
$('#toggle-task-panel').addEventListener('click', () => { state.panelHidden = true; localStorage.setItem('planner-task-panel-hidden', 'true'); renderTaskPanelVisibility(); });
$('#show-task-panel').addEventListener('click', () => { state.panelHidden = false; localStorage.removeItem('planner-task-panel-hidden'); renderTaskPanelVisibility(); });
$('#modal-close').addEventListener('click', () => $('#modal').close()); $('#modal-cancel').addEventListener('click', () => $('#modal').close()); $('#modal-delete').addEventListener('click', deleteTaskFromModal); $('#modal-form').addEventListener('submit', submitModal); $('#plan-selected').addEventListener('click', planSelected); $('#clear-selection').addEventListener('click', () => { state.selected.clear(); renderTasks(); }); $('#hide-plan').addEventListener('click', () => { state.planPanelHidden = true; renderPlan(); }); $('#show-plan').addEventListener('click', () => { state.planPanelHidden = false; renderPlan(); }); $('#discard-plan').addEventListener('click', () => { state.plan = null; renderWeek(); renderPlan(); }); $('#replan').addEventListener('click', planSelected); $('#apply-plan').addEventListener('click', applyPlan); $('#session-pause').addEventListener('click', toggleSession); $('#session-finish').addEventListener('click', finishSession); $('#session-hide').addEventListener('click', () => { state.sessionBarHidden = true; localStorage.setItem('planner-session-hidden','true'); renderSession(); }); $('#session-restore').addEventListener('click', () => { state.sessionBarHidden = false; localStorage.removeItem('planner-session-hidden'); renderSession(); }); $('#toast-close').addEventListener('click', () => { $('#toast').className = 'toast'; });
document.addEventListener('submit', (event) => { if (event.target.id === 'goal-detail-form') { event.preventDefault(); saveGoalDetail(event.target); } if (event.target.id === 'goal-new-task-form') { event.preventDefault(); createGoalTask(event.target); } });

document.addEventListener('dragstart', (event) => { if (event.target.closest('.block-actions')) { event.preventDefault(); return; } const block = event.target.closest('[data-schedule-block]'); const card = event.target.closest('[data-drag-task]'); if (!block && !card) return; state.dragSource = block ? { kind:'block', id:block.dataset.scheduleBlock } : { kind:'task', id:card.dataset.dragTask }; event.dataTransfer.setData('text/plain', `${state.dragSource.kind}:${state.dragSource.id}`); event.dataTransfer.effectAllowed = block ? 'move' : 'copy'; (block || card).classList.add('dragging'); });
document.addEventListener('dragend', (event) => { event.target.closest('[data-schedule-block],[data-drag-task]')?.classList.remove('dragging'); document.querySelectorAll('.day-column.drop-target').forEach((day) => day.classList.remove('drop-target')); state.dragSource = null; });
document.addEventListener('dragover', (event) => { const day = event.target.closest('.day-column'); if (!day || !state.dragSource) return; event.preventDefault(); document.querySelectorAll('.day-column.drop-target').forEach((column) => { if (column !== day) column.classList.remove('drop-target'); }); day.classList.add('drop-target'); });
document.addEventListener('dragleave', (event) => { const day = event.target.closest('.day-column'); if (day && !day.contains(event.relatedTarget)) day.classList.remove('drop-target'); });
document.addEventListener('drop', async (event) => { const day = event.target.closest('.day-column'); const source = state.dragSource; if (!day || !source) return; event.preventDefault(); event.stopPropagation(); document.querySelectorAll('.day-column.drop-target').forEach((column) => column.classList.remove('drop-target')); state.dragSource = null; const minute = minuteFromPointer(day, event); const base = new Date(`${day.dataset.date}T00:00:00`); base.setMinutes(minute); try { let updated; if (source.kind === 'block') { const old = state.week.days.flatMap((item) => item.blocks).find((item) => item.id === source.id); if (!old) throw new Error('Исходный блок уже не найден. Обновите неделю и попробуйте снова.'); const duration = Math.round((new Date(old.end_at).getTime() - new Date(old.start_at).getTime()) / 60000); const end = new Date(base); end.setMinutes(end.getMinutes() + duration); updated = await api(`/workspaces/${state.workspace.id}/blocks/${source.id}`, { method:'PATCH', body:JSON.stringify({ start_at:base.toISOString(), end_at:end.toISOString(), is_pinned:true, expected_version:old.version, allow_conflict:true, move_task_deadline:true }) }); updateTaskDeadlineLocally(old.task_id, updated.end_at); removeBlockLocally(source.id, true); showToast('Блок и срок задачи перенесены'); } else { const task = state.tasks.find((item) => item.id === source.id); if (!task) throw new Error('Задача уже не найдена. Обновите неделю и попробуйте снова.'); const remaining = Math.max(1, task.estimate_minutes - task.planned_minutes); const duration = task.can_split ? Math.min(task.preferred_block_minutes || remaining, remaining) : remaining; const end = new Date(base); end.setMinutes(end.getMinutes() + duration); updated = await api(`/workspaces/${state.workspace.id}/blocks`, { method:'POST', body:JSON.stringify({ task_id:task.id, start_at:base.toISOString(), end_at:end.toISOString(), is_pinned:true, allow_conflict:true }) }); showToast('Задача добавлена в план'); } mergeBlocks([updated], true); renderTasks(); renderWeek(); scheduleBackgroundRefresh(); } catch (error) { showToast(error.message, true); } });
function minuteFromPointer(day, event) { const rect = day.getBoundingClientRect(); return snapMinute(DAY_START + (event.clientY - rect.top - HEADER_HEIGHT) / pixelsPerMinute); }

document.addEventListener('pointerdown', (event) => { const day = event.target.closest('.day-column'); if (!day || event.button !== 0 || event.target.closest('.schedule-block,.fixed-event,.day-header,.availability-action')) return; const minute = minuteFromPointer(day, event); state.availabilitySelection = { day, date:day.dataset.date, start:minute, end:minute + 15 }; day.setPointerCapture?.(event.pointerId); renderAvailabilityDraft(); });
document.addEventListener('pointermove', (event) => { if (!state.availabilitySelection) return; state.availabilitySelection.end = minuteFromPointer(state.availabilitySelection.day, event) + 15; renderAvailabilityDraft(); });
document.addEventListener('pointerup', async () => { const selection = state.availabilitySelection; if (!selection) return; state.availabilitySelection = null; document.querySelector('.availability-draft')?.remove(); const start = Math.min(selection.start, selection.end - 15); const end = Math.max(selection.start + 15, selection.end); try { const day = state.week.days.find((item) => item.date === selection.date); const slots = mergeSlots([...(day?.availability || []).map((slot) => ({ start:localMinute(slot.start_at), end:localMinute(slot.end_at) })), { start, end }]); await api(`/workspaces/${state.workspace.id}/availability/dates/${selection.date}`, { method:'PUT', body:JSON.stringify({ slots:slots.map((slot) => ({ start_minute:slot.start, end_minute:slot.end })) }) }); await loadData(); showToast(`Рабочее время добавлено: ${timeValue(start)}–${timeValue(end)}`); } catch (error) { showToast(error.message, true); } });
function renderAvailabilityDraft() { const selection = state.availabilitySelection; if (!selection) return; let draft = selection.day.querySelector('.availability-draft'); if (!draft) { draft = document.createElement('div'); draft.className = 'availability-draft'; selection.day.append(draft); } const start = Math.min(selection.start, selection.end - 15); const end = Math.max(selection.start + 15, selection.end); draft.style.top = `${pixelForMinute(start) + 1}px`; draft.style.height = `${heightForRange(start, end)}px`; }
function mergeSlots(slots) { return slots.sort((a,b) => a.start - b.start).reduce((result, slot) => { const previous = result.at(-1); if (previous && slot.start <= previous.end) previous.end = Math.max(previous.end, slot.end); else result.push({ ...slot }); return result; }, []); }
async function moveWeek(delta) { state.weekStart = new Date(state.weekStart); state.weekStart.setDate(state.weekStart.getDate() + delta * 7); await loadData(); }
$('#previous-week').addEventListener('click', () => moveWeek(-1)); $('#next-week').addEventListener('click', () => moveWeek(1)); $('#today').addEventListener('click', async () => { state.weekStart = monday(new Date()); await loadData(); });
if (localStorage.getItem('planner-theme') === 'dark') { document.documentElement.dataset.theme = 'dark'; $('#theme-toggle').setAttribute('aria-pressed', 'true'); }
initialize();
