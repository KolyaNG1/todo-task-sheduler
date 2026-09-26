const DAY_START = 8 * 60;
const DAY_END = 24 * 60;
const HEADER_HEIGHT = 68;
const QUARTER_HEIGHT = 18;
const DEFAULT_API_BASE = 'http://127.0.0.1:8100/api/v1';
const storedApiBase = localStorage.getItem('planner-api');

function normalizeApiBase(value) {
  if (!value) return DEFAULT_API_BASE;
  try {
    const url = new URL(value);
    if (['127.0.0.1', 'localhost'].includes(url.hostname)) return DEFAULT_API_BASE;
    return value.replace(/\/$/, '');
  } catch (_error) {
    return DEFAULT_API_BASE;
  }
}

const initialApiBase = normalizeApiBase(storedApiBase);
if (storedApiBase !== initialApiBase) localStorage.setItem('planner-api', initialApiBase);
const state = {
  apiBase: initialApiBase, workspace: null,
  weekStart: monday(new Date()), week: null, tasks: [], directions: [], projects: [], projectGroups: [], labels: [], notifications: [], selected: new Set(), selectedProjectIds: new Set(),
  filter: 'all', taskKindFilter: 'all', catalogTaskKind: 'all', archive: null, archiveSection: 'tasks', collapsedTaskNodes: new Set(), search: '', plan: null, planPanelHidden: false, session: null,
  sessionBarHidden: localStorage.getItem('planner-session-hidden') === 'true', sessionReceivedAt: 0, timer: null,
  section: 'plan', report: null, reportDate: dateKey(new Date()), reportWeekStart: dateKey(monday(new Date())), reportHistory: [], reportTrend: [], dailyProgress: null, calendarDays: 21, templateSlots: [], goals: [], goalDetail: null, goalTaskIds: [], goalTaskFormOpen: false, goalTaskDraft: { title:'', parentTaskId:'', estimate:60, priority:3 }, projectDetail: null, planOverviewHidden: localStorage.getItem('planner-overview-hidden') === 'true', deletedTasks: [], sessionView: localStorage.getItem('planner-session-view') || 'timeline', taskSessionGroups: null, panelHidden: localStorage.getItem('planner-task-panel-hidden') === 'true', navPanelHidden: localStorage.getItem('planner-nav-panel-hidden') === 'true', taskSort: localStorage.getItem('planner-task-sort') || 'deadline', availabilitySelection: null, dragSource: null,
  planMode: localStorage.getItem('planner-plan-mode') === 'capacity' ? 'capacity' : 'schedule', projectDrag: null, projectGroupDrag: null, projectDragEndedAt: 0,
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
function colorForTask(task) { return task.color || task.project?.color || task.direction?.color || '#356AE6'; }
function rangeText(start, end) { return `${new Date(start).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}–${new Date(end).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`; }
function deadlineText(iso) { const value = new Date(iso); return value < new Date() ? 'просрочено' : `до ${value.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}`; }
function isPastCompletedTask(task) { return task.status === 'COMPLETED' && task.completed_at && dateKey(task.completed_at) < dateKey(new Date()); }
function taskTitle(id) { return state.tasks.find((task) => task.id === id)?.title || 'Задача'; }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char])); }
function toMinutes(value) { const [hours, minutes] = String(value).split(':').map(Number); return hours * 60 + minutes; }
function timeValue(minutes) { return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`; }
function formatSeconds(value) { return formatMinutes(Math.round((value || 0) / 60)); }

async function api(path, options = {}) { const response = await fetch(`${state.apiBase}${path}`, { headers: { 'Content-Type':'application/json', ...(options.headers || {}) }, ...options }); if (response.status === 204) return null; const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.error?.message || `Ошибка сервера: ${response.status}`); return body; }
async function connectToPlanner() { const bootstrap = await api('/bootstrap'); state.workspace = bootstrap.workspace; await loadData(); }
async function initialize() {
  try {
    await connectToPlanner();
    showToast('Планировщик готов');
  } catch (firstError) {
    if (state.apiBase !== DEFAULT_API_BASE) {
      state.apiBase = DEFAULT_API_BASE;
      localStorage.setItem('planner-api', DEFAULT_API_BASE);
      try {
        await connectToPlanner();
        showToast('Подключение к локальному серверу восстановлено');
        return;
      } catch (fallbackError) {
        firstError = fallbackError;
      }
    }
    showToast(`Не удалось подключиться к серверу. ${firstError.message}`, true);
    renderOffline();
  }
}
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
    state.week = startup.week; state.tasks = startup.tasks; state.projects = startup.projects || startup.directions || []; state.projectGroups = startup.project_groups || []; state.selectedProjectIds = new Set([...state.selectedProjectIds].filter((projectId) => state.projects.some((project) => project.id === projectId))); state.directions = state.projects; state.labels = labels; state.notifications = notifications; state.goals = goals; state.session = startup.active_session; state.templateSlots = startup.default_availability || []; state.dailyProgress = progress;
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
  state.week = startup.week; state.tasks = startup.tasks; state.projects = startup.projects || startup.directions || []; state.projectGroups = startup.project_groups || []; state.selectedProjectIds = new Set([...state.selectedProjectIds].filter((projectId) => state.projects.some((project) => project.id === projectId))); state.directions = state.projects; state.session = startup.active_session; state.templateSlots = startup.default_availability || []; render();
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
function render() { renderHeader(); renderDailySuccess(); renderGoalStrip(); renderPlanOverview(); renderTaskPanelVisibility(); renderTasks(); renderWeek(); renderWeekDashboard(); renderSession(); renderPlan(); renderSection(); renderPlanMode(); }
function renderHeader() { const selectedDays = state.week.days.slice(0, 7); const first = new Date(`${selectedDays[0].date}T12:00:00`); const last = new Date(`${selectedDays.at(-1).date}T12:00:00`); const options = { day:'numeric', month:'long' }; $('#week-range').textContent = `${first.toLocaleDateString('ru-RU', options)} — ${last.toLocaleDateString('ru-RU', options)}`; const total = selectedDays.reduce((sum, day) => sum + day.capacity_minutes, 0); const free = selectedDays.reduce((sum, day) => sum + day.free_minutes, 0); $('#week-capacity').textContent = `${formatMinutes(free)} / ${formatMinutes(total)}`; const workload = state.week.deadline_workload || { task_count:0, required_minutes:0, available_minutes:0, balance_minutes:0 }; const loadRoot = $('#week-deadline-load').closest('.deadline-capacity'); loadRoot.classList.toggle('shortage', workload.balance_minutes < 0); loadRoot.classList.toggle('reserve', workload.balance_minutes >= 0 && workload.task_count > 0); $('#week-deadline-load').textContent = `${formatMinutes(workload.required_minutes)} на ${workload.task_count} задач`; $('#week-deadline-balance').textContent = workload.balance_minutes < 0 ? `Свободно ${formatMinutes(workload.available_minutes)} · не хватает ${formatMinutes(Math.abs(workload.balance_minutes))}` : `Свободно ${formatMinutes(workload.available_minutes)} · запас ${formatMinutes(workload.balance_minutes)}`; }
function weeklyDashboardDays() { return state.week?.days.slice(0, 7) || []; }
function renderWeekDashboard() {
  const root = $('#week-dashboard');
  if (!root || !state.week) return;
  const days = weeklyDashboardDays();
  const workload = state.week.deadline_workload || { task_count:0, required_minutes:0, available_minutes:0, balance_minutes:0 };
  const weeklyFree = days.reduce((sum, day) => sum + day.free_minutes, 0);
  const weeklyCapacity = days.reduce((sum, day) => sum + day.capacity_minutes, 0);
  const demandRatio = workload.required_minutes ? Math.min(100, Math.round(workload.available_minutes * 100 / workload.required_minutes)) : 100;
  const balanceClass = workload.balance_minutes < 0 ? 'shortage' : workload.task_count ? 'reserve' : 'neutral';
  const balanceLabel = workload.balance_minutes < 0 ? `Не хватает ${formatMinutes(Math.abs(workload.balance_minutes))}` : workload.task_count ? `Запас ${formatMinutes(workload.balance_minutes)}` : 'Нет задач со сроком';
  const taskRows = days.flatMap((day) => day.deadline_tasks.map((task) => ({ ...task, day:day.date }))).sort((left, right) => new Date(left.deadline_at) - new Date(right.deadline_at));
  root.innerHTML = `<div class="week-dashboard-inner">
    <header class="dashboard-heading"><div><p class="eyebrow">ВМЕСТИМОСТЬ ВЫБРАННОЙ НЕДЕЛИ</p><h2>${formatWeek(new Date(`${days[0]?.date || dateKey(state.weekStart)}T12:00:00`))}</h2><p class="muted">Здесь собраны сроки недели и реальный запас рабочего времени до них.</p></div><button class="button secondary" data-action="dashboard-worktime">Изменить рабочее время</button></header>
    <div class="capacity-metrics">
      <article><span>Нужно на задачи</span><strong>${formatMinutes(workload.required_minutes)}</strong><small>${workload.task_count} ${workload.task_count === 1 ? 'задача' : workload.task_count < 5 ? 'задачи' : 'задач'} со сроком</small></article>
      <article><span>Доступно до сроков</span><strong>${formatMinutes(workload.available_minutes)}</strong><small>с учётом занятых блоков</small></article>
      <article class="${balanceClass}"><span>Баланс недели</span><strong>${balanceLabel}</strong><small>меняется вместе с рабочим временем</small></article>
      <article><span>Вся свободная неделя</span><strong>${formatMinutes(weeklyFree)}</strong><small>из ${formatMinutes(weeklyCapacity)} рабочего времени</small></article>
    </div>
    <section class="capacity-balance ${balanceClass}"><div><strong>${balanceLabel}</strong><span>${workload.task_count ? `${formatMinutes(workload.available_minutes)} доступно на ${formatMinutes(workload.required_minutes)} работы` : 'Добавьте срок задаче, чтобы увидеть расчёт нагрузки.'}</span></div><div class="capacity-track"><i style="width:${demandRatio}%"></i></div></section>
    <div class="week-dashboard-content">
      <section class="deadline-agenda"><div class="dashboard-section-title"><div><p class="eyebrow">ДЕДЛАЙНЫ</p><h3>Что нужно закрыть</h3></div><span>${taskRows.length}</span></div>${taskRows.length ? taskRows.map((task) => `<article class="deadline-task-row" style="--project-color:${task.project_color || '#356AE6'}"><i></i><div><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.project_name || 'Без проекта')} · осталось ${formatMinutes(task.remaining_minutes)}</small></div><time>${new Date(task.deadline_at).toLocaleString('ru-RU', { weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' })}</time><button class="task-icon" data-action="edit-task" data-id="${task.id}" title="Открыть задачу" aria-label="Открыть задачу">✎</button></article>`).join('') : '<div class="dashboard-empty"><strong>На этой неделе нет дедлайнов</strong><span>Можно использовать свободное время для задач без жёсткого срока.</span></div>'}</section>
      <section class="capacity-days"><div class="dashboard-section-title"><div><p class="eyebrow">ПО ДНЯМ</p><h3>Нагрузка и свободное время</h3></div></div>${days.map((day) => { const required = day.deadline_tasks.reduce((sum, task) => sum + task.remaining_minutes, 0); const maximum = Math.max(day.free_minutes, required, 1); const date = new Date(`${day.date}T12:00:00`); return `<article class="capacity-day ${day.deadline_count ? 'has-deadline' : ''}"><div><strong>${date.toLocaleDateString('ru-RU', { weekday:'short', day:'numeric', month:'short' })}</strong><small>${day.deadline_count ? `${day.deadline_count} ${day.deadline_count === 1 ? 'срок' : 'срока'}` : 'без дедлайнов'}</small></div><div class="day-load-bars"><span title="Свободно ${formatMinutes(day.free_minutes)}"><i class="free" style="width:${Math.round(day.free_minutes * 100 / maximum)}%"></i></span><span title="Нужно ${formatMinutes(required)}"><i class="required" style="width:${Math.round(required * 100 / maximum)}%"></i></span></div><div class="day-load-values"><b>${formatMinutes(day.free_minutes)} свободно</b><em>${required ? `${formatMinutes(required)} нужно` : '—'}</em></div></article>`; }).join('')}</section>
    </div>
  </div>`;
}
function renderPlanMode() {
  const schedule = state.planMode === 'schedule';
  $('.week-board').hidden = !schedule;
  $('#week-dashboard').hidden = schedule;
  $('#planner-review').hidden = !schedule || !state.plan || state.planPanelHidden;
  $('#show-schedule-view').classList.toggle('active', schedule);
  $('#show-capacity-view').classList.toggle('active', !schedule);
  $('#show-schedule-view').setAttribute('aria-selected', String(schedule));
  $('#show-capacity-view').setAttribute('aria-selected', String(!schedule));
  renderPlanOverview();
}
function setPlanMode(mode) { state.planMode = mode === 'capacity' ? 'capacity' : 'schedule'; localStorage.setItem('planner-plan-mode', state.planMode); renderPlanMode(); }
function renderDailySuccess() { const root = $('#daily-success'); const progress = state.dailyProgress; if (!progress) { root.hidden = true; return; } const goal = Math.round((progress.worked_percent + progress.planning_percent + (progress.planned_task_count ? Math.min(100, Math.round(progress.completed_count * 100 / progress.planned_task_count)) : 0)) / 3); const taskText = progress.planned_task_count ? `${progress.completed_count} из ${progress.planned_task_count}` : `${progress.completed_count}`; root.hidden = false; root.innerHTML = `<div class="success-head"><div><span class="success-emoji">${goal >= 75 ? '🔥' : goal >= 40 ? '🌱' : '✨'}</span><strong>Ритм дня: ${goal}%</strong><small>маленькие шаги складываются в результат</small></div><span class="success-score">${goal}/100</span></div><div class="success-metrics"><article><span>⏱️ В работе</span><strong>${progress.worked_percent}%</strong><small>${formatSeconds(progress.actual_task_seconds)} из ${formatSeconds(progress.planned_seconds)}</small><i><b style="width:${progress.worked_percent}%"></b></i></article><article><span>🧩 План заполнен</span><strong>${progress.planning_percent}%</strong><small>${formatSeconds(progress.planned_seconds)} из ${formatSeconds(progress.capacity_seconds)}</small><i><b style="width:${progress.planning_percent}%"></b></i></article><article><span>✅ Задачи</span><strong>${taskText}</strong><small>выполнено сегодня</small><i><b style="width:${progress.planned_task_count ? Math.min(100, Math.round(progress.completed_count * 100 / progress.planned_task_count)) : 0}%"></b></i></article></div>`; }
function renderGoalStrip() { const root = $('#goal-strip'); const goals = [...state.goals].sort((left, right) => (left.status === 'ACTIVE' ? 0 : 1) - (right.status === 'ACTIVE' ? 0 : 1)); const canShow = state.section === 'plan' && goals.length > 0; root.hidden = !canShow; if (root.hidden) return; root.innerHTML = `<div class="goal-strip-title"><span>🎯</span><strong>Все цели</strong><button class="button ghost" data-action="open-goals">Открыть →</button></div><div class="goal-strip-list">${goals.map((goal) => `<button class="goal-mini-card ${goal.status === 'COMPLETED' ? 'completed' : ''}" data-action="open-goal" data-id="${goal.id}" style="--goal-color:${goal.color || '#356AE6'}"><span>${escapeHtml(goal.title)}</span><strong>${goal.progress_percent || 0}%</strong></button>`).join('')}</div>`; }
function renderPlanOverview() { const root = $('#plan-overview'); const reveal = $('#show-overview'); const visible = state.section === 'plan' && state.planMode === 'schedule' && !state.planOverviewHidden; root.hidden = !visible; reveal.hidden = state.section !== 'plan' || state.planMode !== 'schedule' || visible; }
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

function taskKind(task) { return task.is_leaf ? 'tasks' : task.is_actionable_group || task.is_checkpoint ? 'actionable-groups' : 'groups'; }
function taskKindLabel(task) { return ({ tasks:'Задача', 'actionable-groups':'Группа-задача', groups:'Группа' })[taskKind(task)]; }
function taskMatchesKind(task, kind) { if (kind === 'all') return canScheduleTask(task); if (kind === 'groups') return !task.is_leaf; return taskKind(task) === kind; }
function canScheduleTask(task) { return Boolean(task.can_schedule ?? task.is_leaf); }

function renderTasks() {
  const tasks = sortTasks(state.tasks.filter((task) => task.status !== 'ARCHIVED' && !isPastCompletedTask(task) && taskMatchesKind(task, state.taskKindFilter) && task.title.toLowerCase().includes(state.search.toLowerCase()) && (state.filter === 'all' || state.filter === 'unplanned' && task.status === 'ACTIVE' && canScheduleTask(task) && task.planning_status === 'UNPLANNED' || state.filter === 'overdue' && task.is_overdue)));
  $('#task-count').textContent = state.tasks.filter((task) => task.status === 'ACTIVE' && canScheduleTask(task)).length;
  $('#task-list').innerHTML = tasks.length ? tasks.map((task) => {
    const completed = task.status === 'COMPLETED'; const canPlan = task.status === 'ACTIVE' && canScheduleTask(task) && task.planning_status !== 'PLANNED';
    return `<article class="task-card ${completed ? 'completed' : ''} ${state.selected.has(task.id) ? 'selected' : ''} ${task.is_overdue ? 'overdue' : ''} ${task.is_leaf ? '' : 'task-parent'}" ${canPlan ? `draggable="true" data-drag-task="${task.id}"` : ''} data-task-card="${task.id}">
      <input class="task-selector" type="checkbox" ${state.selected.has(task.id) ? 'checked' : ''} ${canPlan ? '' : 'disabled'} aria-label="Выбрать ${escapeHtml(task.title)}" />
      <span class="task-color" style="background:${colorForTask(task)}"></span>
      <div><div class="task-name">${completed ? '✓ ' : ''}${escapeHtml(task.title)}</div><div class="task-meta ${task.is_overdue ? 'danger' : ''}">${completed ? 'выполнено' : task.parent_task_title ? `внутри «${escapeHtml(task.parent_task_title)}» · ` : ''}${completed ? '' : canScheduleTask(task) ? `${escapeHtml(task.project?.name || task.direction?.name || 'Без проекта')} · ${task.deadline_at ? deadlineText(task.deadline_at) : 'без срока'}` : `${task.child_count} пункт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'} · ${task.progress_percent || 0}%`}${task.repeat_rule === 'WEEKLY' && !completed ? ' · еженедельно' : ''}</div><div class="task-actions">${completed ? `<button class="task-icon" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные" aria-label="Вернуть в активные">✓</button>` : canScheduleTask(task) ? `<button class="task-icon" data-start-task="${task.id}" title="Начать" aria-label="Начать ${escapeHtml(task.title)}">▶</button><button class="task-icon" data-action="auto-plan-task" data-id="${task.id}" ${canPlan ? '' : 'disabled'} title="Распределить автоматически" aria-label="Распределить автоматически">◷</button>` : ''}${task.status === 'ACTIVE' ? `<button class="task-icon" data-action="new-child-task" data-id="${task.id}" title="Добавить вложенный пункт" aria-label="Добавить вложенный пункт к ${escapeHtml(task.title)}">＋</button>` : ''}<button class="task-icon" data-action="edit-task" data-id="${task.id}" title="Редактировать" aria-label="Редактировать">✎</button><button class="task-icon task-delete" data-action="delete-task" data-id="${task.id}" title="В архив" aria-label="В архив: ${escapeHtml(task.title)}">×</button></div></div>
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
    const events = day.fixed_events.map((event) => positioned('fixed-event', localMinute(event.start_at), localMinute(event.end_at), `<div class="block-actions"><button type="button" class="block-action" draggable="false" data-action="edit-event" data-id="${event.id}" data-date="${day.date}" aria-label="Изменить событие ${escapeHtml(event.title)}" title="Изменить событие">✎</button><button type="button" class="block-action" draggable="false" data-action="delete-event" data-id="${event.id}" data-date="${day.date}" aria-label="Удалить событие ${escapeHtml(event.title)}" title="Удалить событие">×</button></div><strong>${escapeHtml(event.title)}</strong><span class="block-time">${rangeText(event.start_at, event.end_at)}</span>`, event.color, `data-fixed-event="${event.id}"`)).join('');
    const blocks = day.blocks.filter((block) => { const task = state.tasks.find((item) => item.id === block.task_id); return task && task.status !== 'ARCHIVED' && !task.deleted_at; }).map((block) => { const completed = block.status === 'COMPLETED' || block.task_status === 'COMPLETED'; const start = localMinute(block.start_at); const end = localMinute(block.end_at); const compact = end - start <= 30; const actions = completed ? `<button type="button" class="block-action" draggable="false" data-action="reopen-task" data-id="${block.task_id}" aria-label="Вернуть задачу в активные" title="Вернуть в активные">✓</button>` : `<button type="button" class="block-action" draggable="false" data-start-task="${block.task_id}" aria-label="Начать ${escapeHtml(block.task_title || 'задачу')}" title="Начать">▶</button><button type="button" class="block-action" draggable="false" data-action="complete-block" data-id="${block.id}" aria-label="Завершить блок" title="Завершить">✓</button>`; return positioned(`schedule-block ${compact ? 'compact-block' : ''} ${block.has_conflict ? 'conflict' : ''} ${block.is_pinned ? 'pinned' : ''} ${completed ? 'completed' : ''}`, start, end, `<div class="block-actions">${actions}<button type="button" class="block-action" draggable="false" data-action="edit-block" data-id="${block.id}" aria-label="Изменить время" title="Изменить время">✎</button><button type="button" class="block-action" draggable="false" data-action="cancel-block" data-id="${block.id}" aria-label="Убрать из плана" title="Убрать из плана">×</button></div><strong>${completed ? '✓ ' : ''}${escapeHtml(block.task_title || 'Задача')}</strong><span class="block-time">${rangeText(block.start_at, block.end_at)}${completed ? ' · готово' : ''}</span>`, block.task_color || block.direction_color || '#356AE6', `${completed ? '' : 'draggable="true"'} data-schedule-block="${block.id}"`); }).join('');
    const proposals = state.plan?.proposals.filter((item) => dateKey(item.start_at) === day.date).map((item) => positioned(`proposal-block ${item.has_conflict ? 'conflict' : ''}`, localMinute(item.start_at), localMinute(item.end_at), `<strong>${escapeHtml(taskTitle(item.task_id))}</strong><span class="block-time">предложено</span>`)).join('') || '';
    const nowLine = day.date === today ? `<span class="now-line" style="top:${pixelForMinute(new Date().getHours() * 60 + new Date().getMinutes())}px"></span>` : '';
    const label = new Date(`${day.date}T12:00:00`).toLocaleDateString('ru-RU', { weekday:'short', day:'numeric', month:'short' });
    const deadlineItems = day.deadline_tasks || [];
    const deadlineBadge = day.deadline_count ? `<button type="button" class="deadline-badge" aria-label="Показать ${day.deadline_count} дедлайна за день">${day.deadline_count}<span class="deadline-popover" role="tooltip"><strong>Срок заканчивается:</strong>${deadlineItems.map((task) => `<span><em>${escapeHtml(task.title)}</em><time>${new Date(task.deadline_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}</time></span>`).join('')}</span></button>` : '';
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
    const nodeMeta = task.is_leaf ? `${task.estimate_minutes} мин · важность ${task.priority}` : `${task.child_count} пункт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'} · ${task.progress_percent || 0}%${task.is_actionable_group ? ' · выполняется как задача' : ''}`;
    goalRows.push(`<article class="goal-task-row" style="--task-depth:${depth}"><span class="goal-order">${isRoot ? rootIndex + 1 : '↳'}</span><div><div class="goal-task-title">${toggle}<strong class="${task.status === 'COMPLETED' ? 'completed-title' : ''}">${task.status === 'COMPLETED' ? '✓ ' : ''}${escapeHtml(task.title)}</strong>${task.is_leaf ? '' : `<span class="node-progress">${task.progress_percent || 0}%</span>`}</div><small>${task.status === 'COMPLETED' ? 'выполнено' : nodeMeta}</small></div><div class="row-actions">${isRoot ? `<button class="button ghost" type="button" data-action="goal-task-up" data-id="${task.id}" ${rootIndex === 0 ? 'disabled' : ''}>↑</button><button class="button ghost" type="button" data-action="goal-task-down" data-id="${task.id}" ${rootIndex === roots.length - 1 ? 'disabled' : ''}>↓</button>` : ''}${task.status === 'COMPLETED' ? `<button class="button ghost" type="button" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные">✓</button>` : ''}<button class="button ghost" type="button" data-action="new-child-task" data-id="${task.id}" title="Добавить вложенный пункт">＋</button><button class="button ghost" type="button" data-action="edit-goal-task" data-id="${task.id}">✎</button><button class="button ghost task-delete" type="button" data-action="${isRoot ? 'goal-task-remove' : 'delete-goal-task'}" data-id="${task.id}" title="${isRoot ? 'Убрать из цели' : 'В архив'}" aria-label="${isRoot ? 'Убрать из цели' : 'В архив'}">×</button></div></article>`);
    if (!collapsed) children.forEach((child) => renderNode(child, depth + 1));
  };
  roots.forEach((task, index) => renderNode(task, 0, index));
  const deadline = goal.deadline_at ? goal.deadline_at.slice(0, 16) : '';
  const draft = state.goalTaskDraft;
  const draftParent = tasks.find((item) => item.id === draft.parentTaskId);
  const newTaskPanel = state.goalTaskFormOpen ? `<form id="goal-new-task-form" class="goal-new-task"><div class="goal-new-task-heading"><div><p class="eyebrow">НОВЫЙ ПУНКТ</p>${draftParent ? `<small class="muted">Внутри «${escapeHtml(draftParent.title)}»</small>` : '<small class="muted">В корне цели</small>'}</div><button class="panel-toggle" type="button" data-action="hide-goal-task-form" aria-label="Скрыть форму">×</button></div><input name="parent_task_id" type="hidden" value="${draft.parentTaskId}" /><label class="field">Название<input name="title" required autofocus value="${escapeHtml(draft.title)}" placeholder="Например, разобрать главу" /></label><div class="inline-fields"><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${draft.estimate}" required /></label><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${value === draft.priority ? 'selected' : ''}>${value}</option>`).join('')}</select></label></div><button class="button primary" type="submit">Добавить пункт</button></form>` : '';
  const goalActions = goal.id ? `<div class="goal-lifecycle-actions">${goal.status === 'COMPLETED' ? '<button class="button secondary" type="button" data-action="reopen-goal" data-id="' + goal.id + '">Вернуть в активные</button>' : '<button class="button ghost" type="button" data-action="complete-goal" data-id="' + goal.id + '">Отметить готовой</button>'}<button class="button danger" type="button" data-action="delete-goal" data-id="${goal.id}">Удалить цель</button></div>` : '';
  view.innerHTML = `<header class="page-header"><div><button class="button ghost" data-action="back-to-goals">← Все цели</button><p class="eyebrow">ЦЕЛЬ И ДЕРЕВО РАБОТЫ</p><h2>${escapeHtml(goal.title || 'Новая цель')}</h2></div><div class="goal-progress"><strong>${goal.progress_percent || 0}%</strong><span>достигнуто</span><i><b style="width:${goal.progress_percent || 0}%"></b></i></div></header><div class="goal-detail-layout"><form id="goal-detail-form" class="settings-card form-grid"><h3>${goal.id ? 'Параметры цели' : 'Создать цель'}</h3><label class="field">Название<input name="title" required value="${escapeHtml(goal.title || '')}" /></label><label class="field">Проект<select name="direction_id"><option value="">Без проекта</option>${state.projects.map((item) => `<option value="${item.id}" ${goal.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Описание<textarea name="description" rows="5">${escapeHtml(goal.description || '')}</textarea></label><div class="inline-fields"><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${goal.priority === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Цвет<input name="color" type="color" value="${goal.color || '#356AE6'}" /></label></div><label class="field">Срок<input name="deadline" type="datetime-local" value="${deadline}" /></label><button class="button primary" type="submit">${goal.id ? 'Сохранить цель и порядок' : 'Создать цель'}</button>${goalActions}</form><section class="settings-card goal-sequence"><div class="goal-sequence-head"><div><p class="eyebrow">ДЕРЕВО РАБОТЫ</p><h3>Группы и задачи цели</h3><p class="muted">Лист — задача. Узел с вложенными пунктами — группа. Группу можно отдельно разрешить выполнять как задачу.</p></div><button class="button secondary" type="button" data-action="toggle-goal-task-form">+ Добавить в корень</button></div><div class="goal-task-list">${goalRows.length ? goalRows.join('') : '<div class="empty"><strong>Пунктов пока нет</strong><p>Добавьте этап, спринт, группу или конкретную задачу.</p></div>'}</div>${newTaskPanel}</section></div>`;
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
    await api(`/workspaces/${state.workspace.id}/tasks`, { method:'POST', body:JSON.stringify({ title:fields.get('title'), goal_id:state.goalDetail.id, parent_task_id:parentTaskId, estimate_minutes:estimate, min_block_minutes:Math.min(30, estimate), preferred_block_minutes:estimate, priority:Number(fields.get('priority')), color:state.goalDetail.color || null }) });
    state.goalTaskFormOpen = false;
    state.goalTaskDraft = { title:'', parentTaskId:'', estimate:60, priority:3 };
    await loadData();
    await openGoalPage(state.goalDetail.id);
    showToast('Задача добавлена в цель');
  } catch (error) { showToast(error.message, true); }
}

async function openProjectPage(projectId) {
  try {
    state.projectDetail = await api(`/workspaces/${state.workspace.id}/projects/${projectId}`);
    state.section = 'project-detail';
    renderSection();
  } catch (error) { showToast(error.message, true); }
}

function projectTaskForDetail(taskId) { return state.projectDetail?.tasks?.find((task) => task.id === taskId); }

function renderProjectDetail(view) {
  const project = state.projectDetail;
  if (!project) { state.section = 'projects'; renderSection(); return; }
  const tasks = project.tasks || [];
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const children = new Map();
  tasks.forEach((task) => { if (task.parent_task_id && byId.has(task.parent_task_id)) children.set(task.parent_task_id, [...(children.get(task.parent_task_id) || []), task]); });
  const rows = [];
  const renderNode = (task, depth) => {
    const nested = (children.get(task.id) || []).sort((left, right) => left.child_position - right.child_position);
    const collapsed = state.collapsedTaskNodes.has(task.id);
    const toggle = nested.length ? `<button class="tree-toggle" type="button" data-action="toggle-task-node" data-id="${task.id}" aria-label="${collapsed ? 'Развернуть' : 'Свернуть'}">${collapsed ? '▸' : '▾'}</button>` : '<span class="tree-toggle-placeholder"></span>';
    const meta = task.status === 'COMPLETED' ? 'выполнено' : task.is_leaf ? `${task.estimate_minutes} мин · важность ${task.priority}${task.deadline_at ? ` · ${deadlineText(task.deadline_at)}` : ''}` : `${task.child_count} пункт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'} · ${task.progress_percent || 0}%${task.is_actionable_group ? ' · выполняется как задача' : ''}`;
    rows.push(`<article class="goal-task-row project-task-row" style="--task-depth:${depth}"><span class="goal-order">${depth ? '↳' : '•'}</span><div><div class="goal-task-title">${toggle}<strong class="${task.status === 'COMPLETED' ? 'completed-title' : ''}">${task.status === 'COMPLETED' ? '✓ ' : ''}${escapeHtml(task.title)}</strong>${task.is_leaf ? '' : `<span class="node-progress">${task.progress_percent || 0}%</span>`}</div><small>${meta}</small></div><div class="row-actions">${task.status === 'COMPLETED' ? `<button class="button ghost" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные">✓</button>` : `<button class="button ghost" data-action="new-project-child" data-id="${task.id}" title="Добавить вложенный пункт">＋</button>`}<button class="button ghost" data-action="edit-project-task" data-id="${task.id}">✎</button><button class="button ghost task-delete" data-action="delete-project-task" data-id="${task.id}" title="В архив">×</button></div></article>`);
    if (!collapsed) nested.forEach((child) => renderNode(child, depth + 1));
  };
  tasks.filter((task) => !task.parent_task_id || !byId.has(task.parent_task_id)).sort((left, right) => left.child_position - right.child_position).forEach((task) => renderNode(task, 0));
  const projectDeadline = project.default_deadline_at ? project.default_deadline_at.slice(0, 16) : '';
  const nearest = project.nearest_deadline_at ? `${project.is_overdue ? 'Просрочен: ' : 'Ближайший срок: '}${new Date(project.nearest_deadline_at).toLocaleString('ru-RU', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' })}` : 'Сроки пока не заданы';
  view.innerHTML = `<header class="page-header project-detail-header"><div><button class="button ghost" data-action="back-to-projects">← Все проекты</button><p class="eyebrow">ПРОЕКТ И ДЕРЕВО РАБОТЫ</p><h2>${escapeHtml(project.name)}</h2><p class="muted ${project.is_overdue ? 'danger' : ''}">${nearest}</p></div><div class="goal-progress"><strong>${project.progress_percent || 0}%</strong><span>выполнено</span><i><b style="width:${project.progress_percent || 0}%;background:${project.color}"></b></i></div></header><div class="goal-detail-layout"><form id="project-detail-form" class="settings-card form-grid"><h3>Параметры проекта</h3><label class="field">Название<input name="name" required value="${escapeHtml(project.name)}" /></label><label class="field">Тип<input name="kind" required value="${escapeHtml(project.kind)}" placeholder="Лаба, учёба, работа…" /></label><div class="inline-fields"><label class="field">Важность по умолчанию<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${project.default_priority === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Оценка по умолчанию, минут<input name="estimate" type="number" min="1" value="${project.default_estimate_minutes || ''}" /></label></div><div class="inline-fields"><label class="field">Цвет<input name="color" type="color" value="${project.color}" /></label><label class="field">Общий срок<input name="deadline" type="datetime-local" value="${projectDeadline}" /></label></div><button class="button primary" type="submit">Сохранить проект</button><button class="button danger" type="button" data-action="delete-project" data-id="${project.id}">Переместить в архив</button></form><section class="settings-card goal-sequence"><div class="goal-sequence-head"><div><p class="eyebrow">ДЕРЕВО ПРОЕКТА</p><h3>Группы и задачи</h3><p class="muted">Стройте любую глубину: этапы, спринты и конкретные задачи. В расписание попадают листья и явно исполняемые группы.</p></div><button class="button secondary" type="button" data-action="new-project-root" data-id="${project.id}">+ Добавить в корень</button></div><div class="goal-task-list">${rows.length ? rows.join('') : '<div class="empty"><strong>В проекте пока нет задач</strong><p>Добавьте первый этап или конкретную задачу.</p></div>'}</div></section></div>`;
}

async function saveProjectDetail(form) {
  const fields = new FormData(form);
  const deadline = fields.get('deadline');
  const payload = { name:fields.get('name'), kind:fields.get('kind'), color:fields.get('color'), default_priority:Number(fields.get('priority')), default_estimate_minutes:fields.get('estimate') ? Number(fields.get('estimate')) : null, default_deadline_at:deadline ? new Date(deadline).toISOString() : null };
  try {
    await api(`/workspaces/${state.workspace.id}/projects/${state.projectDetail.id}`, { method:'PATCH', body:JSON.stringify(payload) });
    await loadData();
    await openProjectPage(state.projectDetail.id);
    showToast('Проект сохранён');
  } catch (error) { showToast(error.message, true); }
}

function sessionMoment(iso) { return new Date(iso).toLocaleString('ru-RU', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' }); }
function sessionIntervals(sessions) { return sessions.map((session) => `<details class="session-period"><summary><span>${sessionMoment(session.started_at)}${session.ended_at ? ` — ${new Date(session.ended_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}` : ' — сейчас'}</span><strong>${formatSeconds(session.elapsed_seconds)}</strong></summary><div class="session-segments">${session.segments.map((segment) => `<span>${new Date(segment.started_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}–${segment.ended_at ? new Date(segment.ended_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' }) : 'сейчас'} <b>${formatSeconds(segment.elapsed_seconds)}</b></span>`).join('') || '<span>Нет закрытых отрезков.</span>'}</div></details>`).join(''); }
function reportSessionGroups(sessions = []) { const groups = new Map(); sessions.forEach((session) => { const key = session.task_id || 'none'; const group = groups.get(key) || { title:session.task_title || 'Общее время', sessions:[], actual_seconds:0 }; group.sessions.push(session); group.actual_seconds += session.elapsed_seconds; groups.set(key, group); }); return [...groups.values()]; }
function renderOverdueSummary(tasks = []) {
  if (!tasks.length) return '';
  return `<details class="overdue-summary"><summary><span class="overdue-symbol">!</span><div><strong>Срок пропущен</strong><small>${tasks.length} ${tasks.length === 1 ? 'задача' : tasks.length < 5 ? 'задачи' : 'задач'} в этот день</small></div><span class="overdue-toggle">Показать список</span></summary><div class="overdue-items">${tasks.map((task) => `<div><strong>${escapeHtml(task.title)}</strong><span>Срок: ${new Date(task.deadline_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' })}</span></div>`).join('')}</div></details>`;
}
function timelineMinute(value) { const moment = new Date(value); return moment.getHours() * 60 + moment.getMinutes() + moment.getSeconds() / 60; }
function renderDayComparison(report) {
  const actual = report?.actual_timeline || [];
  const planned = report?.planned_timeline || [];
  const rangeStart = 6 * 60;
  const rangeEnd = 24 * 60;
  const defaultStart = 9 * 60;
  const pixelsPerMinute = .85;
  const height = (rangeEnd - rangeStart) * pixelsPerMinute;
  const hours = [];
  for (let minute = rangeStart; minute <= rangeEnd; minute += 60) hours.push(minute);
  const clock = (minute) => minute === 24 * 60 ? '24:00' : `${String(Math.floor(minute / 60)).padStart(2, '0')}:00`;
  const guides = hours.map((minute) => `<i class="timeline-guide" style="top:${(minute - rangeStart) * pixelsPerMinute}px"></i>`).join('');
  const axis = hours.map((minute) => `<span style="top:${(minute - rangeStart) * pixelsPerMinute}px">${clock(minute)}</span>`).join('');
  const blocks = (items, kind) => items.map((item) => {
    const start = Math.max(rangeStart, timelineMinute(item.start_at));
    const end = Math.min(rangeEnd, timelineMinute(item.end_at));
    const top = (start - rangeStart) * pixelsPerMinute;
    const blockHeight = Math.max(20, (end - start) * pixelsPerMinute);
    const from = new Date(item.start_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' });
    const to = new Date(item.end_at).toLocaleTimeString('ru-RU', { hour:'2-digit', minute:'2-digit' });
    return `<article class="timeline-block ${kind}" style="top:${top}px;height:${blockHeight}px;--block-color:${item.color || '#356AE6'}" title="${escapeHtml(item.title)} · ${from}–${to}"><strong>${escapeHtml(item.title)}</strong><small>${from}–${to}</small></article>`;
  }).join('');
  const track = (items, kind, empty) => `<div class="timeline-track" style="height:${height}px">${guides}${items.length ? blocks(items, kind) : `<p class="timeline-empty">${empty}</p>`}</div>`;
  const defaultScroll = (defaultStart - rangeStart) * pixelsPerMinute;
  return `<section class="report-section day-comparison"><div class="report-section-head"><div><h3>Как прошёл день</h3><p class="muted">Шкала доступна с 06:00 до 24:00 и открывается на 09:00. Факт и план прокручиваются вместе.</p></div></div><div class="timeline-head"><span></span><strong>Как получилось</strong><strong>Как было в плане</strong></div><div class="timeline-scroll" data-default-scroll="${defaultScroll}"><div class="timeline-body"><div class="timeline-axis" style="height:${height}px">${axis}</div>${track(actual, 'actual', 'Работа не зафиксирована')}${track(planned, 'planned', 'На это время ничего не запланировано')}</div></div></section>`;
}
async function setSessionView(mode) { state.sessionView = mode; localStorage.setItem('planner-session-view', mode); renderSection(); }
function selectReportDate(value, syncWeek = true) { state.reportDate = value; if (syncWeek) state.reportWeekStart = dateKey(monday(new Date(`${value}T12:00:00`))); loadReport(); }
function shiftReportDate(offset) { const value = new Date(`${state.reportDate}T12:00:00`); value.setDate(value.getDate() + offset); selectReportDate(dateKey(value)); }
function shiftReportWeek(offset) { const selected = new Date(`${state.reportDate}T12:00:00`); selected.setDate(selected.getDate() + offset * 7); state.reportWeekStart = dateKey(monday(selected)); state.reportDate = dateKey(selected); loadReport(); }
function showCurrentReportWeek() { const today = new Date(); state.reportDate = dateKey(today); state.reportWeekStart = dateKey(monday(today)); loadReport(); }
function reportWeekDays(history = []) {
  const byDate = new Map(history.map((day) => [day.date, day]));
  const start = new Date(`${state.reportWeekStart}T12:00:00`);
  return Array.from({ length:7 }, (_, index) => {
    const value = new Date(start); value.setDate(value.getDate() + index);
    const date = dateKey(value);
    return byDate.get(date) || { date, score:0, actual_task_seconds:0 };
  });
}

function reportTrendRange(selectedDate = state.reportDate) {
  const today = dateKey(new Date());
  const anchorKey = selectedDate > today ? today : selectedDate;
  const anchor = new Date(`${anchorKey}T12:00:00`);
  const start = new Date(anchor); start.setDate(start.getDate() - 9);
  const end = new Date(anchor); end.setDate(end.getDate() + 4);
  return { anchorKey, startKey:dateKey(start), endKey:dateKey(end) };
}

function renderReportTrend(history = [], selectedDate = state.reportDate) {
  const { anchorKey, startKey } = reportTrendRange(selectedDate);
  const byDate = new Map(history.map((day) => [day.date, day]));
  const start = new Date(`${startKey}T12:00:00`);
  const days = Array.from({ length:14 }, (_, index) => {
    const value = new Date(start); value.setDate(value.getDate() + index);
    const date = dateKey(value);
    return { date, ...(byDate.get(date) || {}), visible:date <= anchorKey };
  });
  const measured = days.filter((day) => day.visible);
  const average = (key) => measured.length ? Math.round(measured.reduce((sum, day) => sum + Number(day[key] || 0), 0) / measured.length) : 0;
  const width = 1060; const height = 286; const left = 48; const right = 18; const top = 18; const bottom = 50;
  const plotWidth = width - left - right; const plotHeight = height - top - bottom;
  const x = (index) => left + (plotWidth * index / 13);
  const y = (score) => top + plotHeight * (1 - Math.max(0, Math.min(100, Number(score || 0))) / 100);
  const visiblePoints = days.map((day, index) => day.visible ? `${x(index)},${y(day.score)}` : null).filter(Boolean);
  const areaPoints = visiblePoints.length ? `${x(0)},${top + plotHeight} ${visiblePoints.join(' ')} ${x(measured.length - 1)},${top + plotHeight}` : '';
  const grid = [100, 75, 50, 25, 0].map((value) => `<g class="trend-grid"><line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}"></line><text x="${left - 9}" y="${y(value) + 4}">${value}%</text></g>`).join('');
  const labels = days.map((day, index) => `<text class="trend-date ${day.visible ? '' : 'future'}" x="${x(index)}" y="${height - 17}" text-anchor="middle">${new Date(`${day.date}T12:00:00`).toLocaleDateString('ru-RU', { day:'2-digit', month:'2-digit' })}</text>`).join('');
  const points = days.map((day, index) => day.visible ? `<circle class="trend-point ${day.date === anchorKey ? 'anchor' : ''}" cx="${x(index)}" cy="${y(day.score)}" r="${day.date === anchorKey ? 6 : 4}"><title>${new Date(`${day.date}T12:00:00`).toLocaleDateString('ru-RU', { day:'numeric', month:'long' })}: ${day.score || 0}%</title></circle>` : '').join('');
  const futureStart = (x(9) + x(10)) / 2;
  return `<section class="report-section report-trend"><div class="report-section-head"><div><p class="eyebrow">СКОЛЬЗЯЩЕЕ ОКНО · 14 ДНЕЙ</p><h3>Динамика ритма дня</h3><p class="muted">Девять дней до выбранной даты и четыре позиции после неё. Конец кривой остаётся в последней трети графика.</p></div><strong class="trend-current">${days[9].score || 0}%<small>${new Date(`${anchorKey}T12:00:00`).toLocaleDateString('ru-RU', { day:'numeric', month:'short' })}</small></strong></div><div class="trend-averages"><article><span>Средний ритм</span><strong>${average('score')}%</strong></article><article><span>Средний факт</span><strong>${formatSeconds(average('actual_task_seconds'))}</strong></article><article><span>Средний план</span><strong>${formatSeconds(average('planned_seconds'))}</strong></article><article><span>Выполнено в среднем</span><strong>${average('completed_count')}</strong></article></div><div class="trend-chart-wrap"><svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="trend-chart-title trend-chart-description"><title id="trend-chart-title">Ритм дня за 14 дней</title><desc id="trend-chart-description">Линия дневного процента заканчивается на выбранной или последней доступной дате и оставляет четыре будущие позиции справа.</desc><rect class="trend-future-zone" x="${futureStart}" y="${top}" width="${width - right - futureStart}" height="${plotHeight}"></rect>${grid}${areaPoints ? `<polygon class="trend-area" points="${areaPoints}"></polygon><polyline class="trend-line" points="${visiblePoints.join(' ')}"></polyline>` : ''}<line class="trend-anchor-line" x1="${x(9)}" y1="${top}" x2="${x(9)}" y2="${top + plotHeight}"></line>${points}${labels}</svg></div><p class="trend-note">Средние рассчитаны по ${measured.length} доступным дням окна; будущие дни не считаются нулями.</p></section>`;
}

function orderedProjects(groupId = null) {
  return state.projects.filter((project) => (project.group_id || null) === groupId).sort((left, right) => (left.position || 0) - (right.position || 0) || left.name.localeCompare(right.name, 'ru'));
}
function orderedProjectRoot() {
  return [
    ...orderedProjects(null).map((item) => ({ kind:'project', id:item.id, position:item.position || 0, name:item.name, item })),
    ...state.projectGroups.map((item) => ({ kind:'group', id:item.id, position:item.position || 0, name:item.name, item })),
  ].sort((left, right) => left.position - right.position || left.name.localeCompare(right.name, 'ru'));
}
function projectCardMarkup(item) {
  const nearest = item.nearest_deadline_at ? `${item.is_overdue ? 'Просрочен' : 'Ближайший срок'}: ${new Date(item.nearest_deadline_at).toLocaleDateString('ru-RU', { day:'numeric', month:'short', year:'numeric' })}` : 'Сроки пока не заданы';
  const selected = state.selectedProjectIds.has(item.id);
  return `<article class="direction-card project-card ${item.is_overdue ? 'overdue' : item.is_urgent ? 'urgent' : ''} ${selected ? 'selected' : ''}" draggable="true" data-project-card="${item.id}" data-project-group="${item.group_id || ''}" ${item.group_id ? '' : `data-root-kind="project" data-root-id="${item.id}"`}>
    <div class="project-card-controls"><button class="project-select" data-action="toggle-project-selection" data-id="${item.id}" aria-pressed="${selected}" title="${selected ? 'Убрать из выделения' : 'Выделить проект'}">${selected ? '✓' : ''}</button><span class="project-drag-handle" title="Перетащить проект" aria-hidden="true">⠿</span></div>
    <span class="direction-dot" style="background:${item.color}"></span><div class="project-card-title"><div><p class="eyebrow">${escapeHtml(item.kind)}</p><h3>${escapeHtml(item.name)}</h3></div><strong>${item.progress_percent || 0}%</strong></div><p>${item.actionable_task_count ? `${item.completed_actionable_task_count} из ${item.actionable_task_count} задач завершено` : 'Задач пока нет'}</p><p class="project-deadline ${item.is_overdue ? 'danger' : ''}">${nearest}</p><div class="goal-card-progress"><span>Прогресс проекта</span><i><b style="width:${item.progress_percent || 0}%;background:${item.color}"></b></i></div><div class="direction-actions"><button class="button secondary" data-action="open-project" data-id="${item.id}">Открыть</button><button class="button ghost task-delete" data-action="delete-project" data-id="${item.id}" title="В архив">×</button></div></article>`;
}
function projectGroupMarkup(group) {
  const projects = orderedProjects(group.id);
  return `<section class="project-group ${group.is_collapsed ? 'collapsed' : 'expanded'}" style="--group-color:${group.color || '#356AE6'}" draggable="true" data-project-group-section="${group.id}" data-project-dropzone="${group.id}" data-root-kind="group" data-root-id="${group.id}">
    <div class="project-group-heading"><div class="project-group-icon" aria-hidden="true">${projects.slice(0, 4).map((project) => `<span style="--project-color:${project.color}" title="${escapeHtml(project.name)}">${escapeHtml(project.name.slice(0, 1))}</span>`).join('') || '<span class="project-group-icon-empty">＋</span>'}</div><div class="project-group-name"><p class="eyebrow">ГРУППА ПРОЕКТОВ</p><h3>${escapeHtml(group.name)}</h3><small>${projects.length} проект${projects.length === 1 ? '' : projects.length < 5 ? 'а' : 'ов'}</small></div><span class="project-drag-handle" title="Перетащить группу" aria-hidden="true">⠿</span></div>
    <div class="project-group-actions"><button class="button secondary" data-action="toggle-project-group" data-id="${group.id}" aria-expanded="${!group.is_collapsed}">${group.is_collapsed ? 'Открыть' : 'Свернуть'}</button><button class="button ghost" data-action="edit-project-group" data-id="${group.id}" title="Параметры группы">⚙</button><button class="button ghost task-delete" data-action="dissolve-project-group" data-id="${group.id}" title="Расформировать группу">×</button></div>
    ${group.is_collapsed ? '' : `<div class="project-group-contents">${projects.map(projectCardMarkup).join('') || '<div class="project-drop-empty">Перетащите проект сюда</div>'}</div>`}</section>`;
}
function renderProjectBoard(view) {
  const root = orderedProjectRoot();
  const selectedCount = state.selectedProjectIds.size;
  view.innerHTML = `<header class="page-header"><div><p class="eyebrow">РАБОТА ПО ОБЛАСТЯМ</p><h2>Проекты</h2><p class="muted">Перетаскивайте карточки, собирайте связанные проекты в группы и открывайте каждый проект как отдельное рабочее пространство.</p></div><div class="page-header-actions"><button class="button secondary" data-action="new-label">+ Тег</button><button class="button secondary" data-action="new-project-group">+ Группа</button><button class="button primary" data-action="new-project">+ Проект</button></div></header>
    ${selectedCount ? `<div class="project-selection-toolbar"><div><strong>Выбрано: ${selectedCount}</strong><span>Объедините карточки в новую группу.</span></div><button class="button primary" data-action="new-project-group">Создать группу из выбранных</button><button class="button ghost" data-action="clear-project-selection">Снять выделение</button></div>` : ''}
    <div class="project-board" data-project-dropzone="">${root.map((entry) => entry.kind === 'group' ? projectGroupMarkup(entry.item) : projectCardMarkup(entry.item)).join('') || '<div class="project-drop-empty">Создайте первый проект или группу</div>'}</div>
    <section class="settings-card tag-management"><h3>Теги проектов</h3>${state.labels.length ? state.labels.map((tag) => `<div class="tag-row"><span class="tag-swatch" style="background:${tag.color || '#356AE6'}"></span><strong>${escapeHtml(tag.name)}</strong><span class="muted">${escapeHtml(state.projects.find((item) => item.id === tag.direction_id)?.name || 'Общий')}</span><div class="row-actions"><button class="button ghost" data-action="edit-label" data-id="${tag.id}">✎</button><button class="button ghost" data-action="delete-label" data-id="${tag.id}">Удалить</button></div></div>`).join('') : '<p class="muted">Тегов пока нет.</p>'}</section>`;
}

function renderSection() {
  const plan = $('#plan-workspace'); const view = $('#page-view'); plan.hidden = state.section !== 'plan'; view.hidden = state.section === 'plan'; renderTaskPanelVisibility(); if (state.section === 'plan') return;
  const active = state.tasks.filter((item) => item.status === 'ACTIVE');
  if (state.section === 'goal-detail') { renderGoalDetail(view); return; }
  if (state.section === 'project-detail') { renderProjectDetail(view); return; }
  if (state.section === 'tasks') {
    const catalogTasks = sortTasks(state.tasks.filter((task) => !isPastCompletedTask(task) && taskMatchesKind(task, state.catalogTaskKind)));
    const catalogButton = (kind, title) => `<button class="button ${state.catalogTaskKind === kind ? 'secondary' : 'ghost'}" data-action="catalog-kind-filter" data-kind="${kind}">${title}</button>`;
    const taskActions = (task) => `<div class="catalog-task-actions">${task.status === 'ACTIVE' && canScheduleTask(task) ? `<button class="task-icon" data-action="start" data-id="${task.id}" title="Начать" aria-label="Начать">▶</button><button class="task-icon" data-action="auto-plan-task" data-id="${task.id}" title="Распределить" aria-label="Распределить">◷</button><button class="task-icon" data-action="complete" data-id="${task.id}" title="Готово" aria-label="Готово">✓</button>` : ''}${task.status === 'COMPLETED' ? `<button class="task-icon" data-action="reopen-task" data-id="${task.id}" title="Вернуть в активные" aria-label="Вернуть в активные">✓</button>` : task.status === 'ACTIVE' ? `<button class="task-icon" data-action="new-child-task" data-id="${task.id}" title="Добавить вложенный пункт" aria-label="Добавить вложенный пункт">＋</button>` : ''}<button class="task-icon" data-action="edit-task" data-id="${task.id}" title="Редактировать" aria-label="Редактировать">✎</button><button class="task-icon task-delete" data-action="delete-task" data-id="${task.id}" title="В архив" aria-label="В архив">×</button></div>`;
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">РАБОЧИЕ ЕДИНИЦЫ</p><h2>Задачи</h2><p class="muted">Листы выполняются как задачи. Группы видны отдельно и попадают в план только по явному выбору.</p></div><button class="button primary" data-action="new-task">+ Задача</button></header><div class="catalog-toolbar">${catalogButton('all', 'Все задачи')}${catalogButton('tasks', 'Листы')}${catalogButton('groups', 'Группы')}${catalogButton('actionable-groups', 'Группы-задачи')}</div><div class="task-catalog-grid">${catalogTasks.map((task) => `<article class="catalog-task ${task.status === 'COMPLETED' ? 'completed' : ''} ${task.is_overdue ? 'overdue' : ''} ${task.is_leaf ? '' : 'task-parent'}"><span class="task-color" style="background:${colorForTask(task)}"></span><div class="catalog-task-copy"><div><strong>${task.status === 'COMPLETED' ? '✓ ' : ''}${escapeHtml(task.title)}</strong><span class="status">${task.status === 'COMPLETED' ? 'Готово' : taskKindLabel(task)}</span></div><p>${task.status === 'COMPLETED' ? 'выполнено' : task.parent_task_title ? `внутри «${escapeHtml(task.parent_task_title)}» · ` : ''}${task.status === 'COMPLETED' ? '' : task.is_leaf ? `${task.project?.name || task.direction?.name || 'Без проекта'} · ${task.deadline_at ? deadlineText(task.deadline_at) : 'без срока'} · ${task.estimate_minutes}м` : `${task.child_count} пункт${task.child_count === 1 ? '' : task.child_count < 5 ? 'а' : 'ов'} · ${task.progress_percent || 0}%`}</p></div>${taskActions(task)}</article>`).join('') || '<div class="empty"><strong>Задач этого типа пока нет</strong></div>'}</div>`;
  } else if (state.section === 'projects') {
    renderProjectBoard(view);
    return;
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">РАБОТА ПО ОБЛАСТЯМ</p><h2>Проекты</h2><p class="muted">Ведите этапы, спринты и задачи проекта в одном дереве. Настройки проекта наследуются новыми задачами.</p></div><div><button class="button secondary" data-action="new-label">+ Тег</button><button class="button primary" data-action="new-project">+ Проект</button></div></header><div class="card-grid project-grid">${state.projects.map((item) => { const nearest = item.nearest_deadline_at ? `${item.is_overdue ? 'Просрочен' : 'Ближайший срок'}: ${new Date(item.nearest_deadline_at).toLocaleDateString('ru-RU', { day:'numeric', month:'short', year:'numeric' })}` : 'Сроки пока не заданы'; return `<article class="direction-card project-card ${item.is_overdue ? 'overdue' : ''}"><span class="direction-dot" style="background:${item.color}"></span><div class="project-card-title"><div><p class="eyebrow">${escapeHtml(item.kind)}</p><h3>${escapeHtml(item.name)}</h3></div><strong>${item.progress_percent || 0}%</strong></div><p>${item.actionable_task_count ? `${item.completed_actionable_task_count} из ${item.actionable_task_count} задач завершено` : 'Задач пока нет'}</p><p class="project-deadline ${item.is_overdue ? 'danger' : ''}">${nearest}</p><div class="goal-card-progress"><span>Прогресс проекта</span><i><b style="width:${item.progress_percent || 0}%;background:${item.color}"></b></i></div><div class="direction-actions"><button class="button secondary" data-action="open-project" data-id="${item.id}">Открыть</button><button class="button ghost" data-action="edit-project" data-id="${item.id}">Параметры</button><button class="button ghost task-delete" data-action="delete-project" data-id="${item.id}" title="В архив">×</button></div></article>`; }).join('') || '<div class="empty"><strong>Проектов пока нет</strong><p>Создайте проект для лаборатории, курса или направления работы.</p></div>'}</div><section class="settings-card tag-management"><h3>Теги проектов</h3>${state.labels.length ? state.labels.map((tag) => `<div class="tag-row"><span class="tag-swatch" style="background:${tag.color || '#356AE6'}"></span><strong>${escapeHtml(tag.name)}</strong><span class="muted">${escapeHtml(state.projects.find((item) => item.id === tag.direction_id)?.name || 'Общий')}</span><div class="row-actions"><button class="button ghost" data-action="edit-label" data-id="${tag.id}">✎</button><button class="button ghost" data-action="delete-label" data-id="${tag.id}">Удалить</button></div></div>`).join('') : '<p class="muted">Тегов пока нет.</p>'}</section>`;
    state.projects.forEach((item) => view.querySelector(`[data-action="open-project"][data-id="${item.id}"]`)?.closest('.project-card')?.classList.toggle('urgent', Boolean(item.is_urgent)));
    view.querySelectorAll('[data-action="edit-project"]').forEach((button) => button.remove());
  } else if (state.section === 'goals') {
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ДОЛГОСРОЧНЫЙ ФОКУС</p><h2>Цели</h2></div><button class="button primary" data-action="new-goal">+ Цель</button></header><div class="card-grid">${state.goals.map((goal) => `<article class="direction-card goal-card ${goal.is_overdue ? 'overdue' : ''}"><span class="direction-dot" style="background:${goal.color || '#356AE6'}"></span><h3>${escapeHtml(goal.title)}</h3><p>${goal.actionable_task_count ? `${goal.completed_actionable_task_count} из ${goal.actionable_task_count} задач завершено` : 'Задач пока нет'}${goal.deadline_at ? ` · ${deadlineText(goal.deadline_at)}` : ''}</p><div class="goal-card-progress"><span>Достигнуто ${goal.progress_percent || 0}%</span><i><b style="width:${goal.progress_percent || 0}%"></b></i></div><p>${escapeHtml(goal.description || 'Без описания')}</p><div class="direction-actions goal-card-actions">${goal.status === 'ACTIVE' ? `<button class="button secondary" data-action="open-goal" data-id="${goal.id}">Открыть</button><button class="button ghost" data-action="complete-goal" data-id="${goal.id}">Готово</button>` : `<button class="button secondary" data-action="open-goal" data-id="${goal.id}">Открыть</button><button class="button ghost" data-action="reopen-goal" data-id="${goal.id}" title="Вернуть цель в активные">Вернуть</button>`}<button class="button danger" data-action="delete-goal" data-id="${goal.id}">Удалить</button></div></article>`).join('') || '<div class="empty"><strong>Целей пока нет</strong><p>Цель объединяет связанные задачи и помогает держать курс.</p></div>'}</div>`;
  } else if (state.section === 'results') {
    const report = state.report;
    const byTask = state.sessionView === 'tasks';
    const groups = reportSessionGroups(report?.sessions);
    const sessionContent = byTask
      ? (groups.length ? `<div class="task-session-list">${groups.map((group) => `<details class="task-session-group"><summary><div><strong>${escapeHtml(group.title)}</strong><small>${group.sessions.length} сесс.</small></div><b>${formatSeconds(group.actual_seconds)}</b></summary><div class="task-session-periods">${sessionIntervals(group.sessions)}</div></details>`).join('')}</div>` : '<p class="muted">Сессий за этот день нет.</p>')
      : (report?.sessions.length ? report.sessions.map((item) => `<div class="session-row"><span>${escapeHtml(item.task_title || 'Общая работа')}</span><strong>${formatSeconds(item.elapsed_seconds)}</strong><small>${sessionMoment(item.started_at)}</small></div>`).join('') : '<p class="muted">За этот день сессий нет.</p>');
    const history = reportWeekDays(state.reportHistory);
    const weekStart = new Date(`${state.reportWeekStart}T12:00:00`);
    const todayKey = dateKey(new Date());
    const warnings = renderOverdueSummary(report?.overdue_tasks);
    const comparison = report ? renderDayComparison(report) : '';
    const trend = report ? renderReportTrend(state.reportTrend, state.reportDate) : '';
    view.innerHTML = `<header class="page-header"><div><p class="eyebrow">ФАКТ И НАГРУЗКА ЗА КОНКРЕТНЫЙ ДЕНЬ</p><h2>Итоги дня</h2><div class="report-date-control"><button class="button ghost" data-action="report-previous" aria-label="Предыдущий день">‹</button><input id="report-date" type="date" value="${state.reportDate}" /><button class="button ghost" data-action="report-next" aria-label="Следующий день">›</button><button class="button ghost" data-action="report-today">Сегодня</button></div></div><div><button class="button secondary" data-action="add-manual">+ Внести время</button><button class="button primary" data-action="refresh-report">Обновить</button></div></header>${warnings}${report ? `<div class="metrics"><article><span>План</span><strong>${formatSeconds(report.planned_seconds)}</strong></article><article><span>Факт</span><strong>${formatSeconds(report.actual_seconds)}</strong></article><article><span>Выполнено</span><strong>${report.completed_count}</strong></article><article><span>Ритм дня</span><strong>${report.score || 0}%</strong></article></div><section class="report-history"><div class="report-week-heading"><div><h3>Неделя</h3><span>${formatWeek(weekStart)}</span></div><div><button class="button ghost" data-action="report-week-previous" aria-label="Предыдущая неделя">‹</button><button class="button ghost current-week-button" data-action="report-current-week">Текущая неделя</button><button class="button ghost" data-action="report-week-next" aria-label="Следующая неделя">›</button></div></div><div class="report-week-days">${history.map((day, index) => `<button class="history-day ${day.date === state.reportDate ? 'active' : ''} ${day.date > todayKey ? 'future' : ''}" data-action="select-report-date" data-date="${day.date}"><span class="history-weekday">${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][index]}</span><span>${new Date(`${day.date}T12:00:00`).toLocaleDateString('ru-RU', { day:'numeric', month:'short' })}</span><strong>${day.score || 0}%</strong><small>${formatSeconds(day.actual_task_seconds)} работы</small></button>`).join('')}</div></section>${comparison}<section class="report-section"><div class="report-section-head"><h3>${byTask ? 'Работа по задачам' : 'Сессии за день'}</h3><div class="view-toggle"><button class="button ${byTask ? 'ghost' : 'secondary'}" data-action="show-session-timeline">По времени</button><button class="button ${byTask ? 'secondary' : 'ghost'}" data-action="show-session-tasks">По задачам</button></div></div><p class="muted">${byTask ? 'Все группы ниже относятся только к выбранному дню.' : 'Хронологический вид выбранного дня.'}</p>${sessionContent}</section>${trend}` : '<div class="empty">Собираю итог дня…</div>'}`;
    const timelineScroll = view.querySelector('.timeline-scroll');
    if (timelineScroll) timelineScroll.scrollTop = Number(timelineScroll.dataset.defaultScroll || 0);
  } else if (state.section === 'archive') {
    const archive = state.archive || { tasks:[], goals:[], directions:[], labels:[], fixed_events:[] };
    const sections = [['tasks','Задачи'],['goals','Цели'],['directions','Проекты'],['labels','Теги'],['fixed_events','События']];
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

function eventFormTemplate(eventItem, options, colorField) {
  const occurrence = Boolean(options.eventDate && eventItem?.weekday !== null && eventItem?.weekday !== undefined);
  const date = eventItem?.local_date || options.eventDate || dateKey(state.weekStart);
  const dateInput = occurrence ? `<input type="date" value="${date}" disabled /><input type="hidden" name="date" value="${date}" />` : `<input name="date" type="date" value="${date}" required />`;
  const recurrence = occurrence ? `<label class="event-scope-choice"><input type="checkbox" name="apply_all" /> Применить изменения ко всем повторам</label><p class="modal-note">Без галочки изменится только ${new Date(`${date}T12:00:00`).toLocaleDateString('ru-RU', { day:'numeric', month:'long' })}.</p>` : `<label><input type="checkbox" name="weekly" ${eventItem?.weekday !== null && eventItem ? 'checked' : ''}/> Повторять каждую неделю</label>`;
  return `<div class="form-grid"><label class="field">Название<input name="title" required value="${escapeHtml(eventItem?.title || '')}" /></label><label class="field">Дата${dateInput}</label><div class="inline-fields"><label class="field">Начало<input name="start" type="time" value="${eventItem ? timeValue(eventItem.start_minute) : '10:00'}" required /></label><label class="field">Конец<input name="end" type="time" value="${eventItem ? timeValue(eventItem.end_minute) : '11:30'}" required /></label></div>${recurrence}<label class="field">Цвет${colorField(eventItem?.color || '#D9DDE2')}</label></div>`;
}

function openModal(type, entityId = null, options = {}) {
  const modal = $('#modal');
  const blockItem = entityId ? state.week?.days.flatMap((item) => item.blocks).find((item) => item.id === entityId) : null;
  const taskEntityId = type === 'block' ? blockItem?.task_id : entityId;
  const task = taskEntityId ? state.tasks.find((item) => item.id === taskEntityId) || projectTaskForDetail(taskEntityId) : null;
  const direction = entityId ? state.projects.find((item) => item.id === entityId) : null;
  const projectGroup = entityId ? state.projectGroups.find((item) => item.id === entityId) : null;
  const label = entityId ? state.labels.find((item) => item.id === entityId) : null;
  const goalItem = entityId ? state.goals.find((item) => item.id === entityId) : null;
  const eventItem = entityId ? (state.week?.days.find((item) => item.date === options.eventDate)?.fixed_events.find((item) => item.id === entityId) || uniqueEvents().find((item) => item.id === entityId)) : null;
  const day = state.week?.days.find((item) => item.date === dateKey(state.weekStart));
  const preselectedGoalId = task?.goal_id || options.goalId || '';
  const preselectedParentId = task?.parent_task_id || options.parentTaskId || '';
  const preselectedProjectId = task?.project_id || task?.direction?.id || options.projectId || '';
  const colorField = (value) => `<input name="color" class="color-choice" type="color" value="${value || '#356AE6'}" aria-label="Цвет" />`;
  const parent = state.tasks.find((item) => item.id === preselectedParentId) || projectTaskForDetail(preselectedParentId) || goalTaskForDetail(preselectedParentId);
  const taskTemplate = `<div class="form-grid task-form"><section class="form-section"><label class="field">Название<input name="title" required autofocus value="${escapeHtml(task?.title || '')}" /></label></section><section class="form-section"><h3>Место в дереве</h3><label class="field">Цель<select name="goal_id" ${preselectedParentId ? 'disabled' : ''}><option value="">Без цели</option>${state.goals.filter((item) => item.status === 'ACTIVE').map((item) => `<option value="${item.id}" ${preselectedGoalId === item.id ? 'selected' : ''}>${escapeHtml(item.title)}</option>`).join('')}</select></label><input type="hidden" name="parent_task_id" value="${preselectedParentId}" />${parent ? `<div class="task-parent-readonly"><span>Внутри</span><strong>${escapeHtml(parent.title)}</strong><small>Проект и цель наследуются от родителя.</small></div>` : '<p class="modal-note">Корневая задача. Чтобы добавить вложенный пункт, используйте «+» у этой задачи после сохранения.</p>'}${task && !task.is_leaf ? `<label class="checkpoint-switch"><input name="is_checkpoint" type="checkbox" ${task.is_actionable_group ? 'checked' : ''}/> Считать эту группу задачей</label><p class="modal-note">Включите, если у группы есть собственная работа помимо вложенных задач.</p>` : ''}</section><section class="form-section"><h3>Планирование</h3><label class="field">Проект<select name="direction_id" ${preselectedParentId ? 'disabled' : ''}><option value="">Без проекта</option>${state.projects.map((item) => `<option value="${item.id}" ${preselectedProjectId === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><div class="inline-fields"><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${task?.estimate_minutes || parent?.estimate_minutes || state.projects.find((item) => item.id === preselectedProjectId)?.default_estimate_minutes || 60}" required /></label><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${task?.priority === value || !task && value === (parent?.priority || state.projects.find((item) => item.id === preselectedProjectId)?.default_priority || 3) ? 'selected' : ''}>${value}</option>`).join('')}</select></label></div><div class="inline-fields"><label class="field">Цвет задачи${colorField(task?.color || parent?.color || state.projects.find((item) => item.id === preselectedProjectId)?.color || '#356AE6')}</label><label class="field">Срок<input name="deadline" type="datetime-local" value="${task?.deadline_at ? task.deadline_at.slice(0, 16) : state.projects.find((item) => item.id === preselectedProjectId)?.default_deadline_at?.slice(0, 16) || ''}" /></label></div><div class="task-options"><label><input name="can_split" type="checkbox" ${task?.can_split ? 'checked' : ''}/> Разрешить разбивать на несколько промежутков</label><label><input name="repeat_rule" type="checkbox" ${task?.repeat_rule === 'WEEKLY' ? 'checked' : ''}/> Повторять еженедельно</label></div></section>${state.labels.length ? `<fieldset class="label-picker"><legend>Теги</legend>${state.labels.map((item) => `<label><input type="checkbox" name="label_ids" value="${item.id}" ${task?.labels.some((tag) => tag.id === item.id) ? 'checked' : ''}/> ${escapeHtml(item.name)}</label>`).join('')}</fieldset>` : ''}</div>`;
  const templates = {
    task:{ title:task ? 'Изменить задачу' : 'Новая задача', submit:task ? 'Сохранить' : 'Добавить задачу', html:taskTemplate },
    goal:{ title:goalItem ? 'Изменить цель' : 'Новая цель', submit:goalItem ? 'Сохранить' : 'Создать цель', html:`<div class="form-grid"><label class="field">Название<input name="title" required autofocus value="${escapeHtml(goalItem?.title || '')}" /></label><label class="field">Проект<select name="direction_id"><option value="">Без проекта</option>${state.projects.map((item) => `<option value="${item.id}" ${goalItem?.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Описание<textarea name="description" rows="4">${escapeHtml(goalItem?.description || '')}</textarea></label><div class="inline-fields"><label class="field">Важность<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${goalItem?.priority === value || !goalItem && value === 3 ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Цвет${colorField(goalItem?.color || '#356AE6')}</label></div><label class="field">Срок<input name="deadline" type="datetime-local" value="${goalItem?.deadline_at ? goalItem.deadline_at.slice(0, 16) : ''}" /></label></div>` },
    direction:{ title:direction ? 'Изменить проект' : 'Новый проект', submit:direction ? 'Сохранить' : 'Создать проект', html:`<div class="form-grid"><label class="field">Название<input name="name" required autofocus value="${escapeHtml(direction?.name || '')}" /></label><div class="inline-fields"><label class="field">Тип<input name="kind" required value="${escapeHtml(direction?.kind || '')}" placeholder="Лаба, учёба, работа…" /></label><label class="field">Цвет${colorField(direction?.color || '#356AE6')}</label></div><div class="inline-fields"><label class="field">Важность по умолчанию<select name="priority">${priorityValues.map((value) => `<option value="${value}" ${direction?.default_priority === value || !direction && value === 3 ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label class="field">Оценка, минут<input name="estimate" type="number" min="1" step="1" value="${direction?.default_estimate_minutes || ''}" /></label></div><label class="field">Общий срок проекта<input name="deadline" type="datetime-local" value="${direction?.default_deadline_at ? direction.default_deadline_at.slice(0, 16) : ''}" /></label></div>` },
    'project-group':{ title:projectGroup ? 'Параметры группы' : 'Новая группа проектов', submit:projectGroup ? 'Сохранить' : 'Создать группу', html:`<div class="form-grid"><label class="field">Название группы<input name="name" required autofocus value="${escapeHtml(projectGroup?.name || '')}" placeholder="Например, Лаборатории" /></label><label class="field">Цвет группы${colorField(projectGroup?.color || '#356AE6')}</label>${projectGroup ? '<p class="modal-note">Карточки можно добавлять и убирать простым перетаскиванием на странице проектов.</p>' : `<fieldset class="project-group-picker"><legend>Проекты в группе</legend>${state.projects.map((item) => `<label><input type="checkbox" name="project_ids" value="${item.id}" ${state.selectedProjectIds.has(item.id) ? 'checked' : ''}/> <span style="--project-color:${item.color}"></span>${escapeHtml(item.name)}</label>`).join('') || '<p class="muted">Проектов пока нет. Можно создать пустую группу.</p>'}</fieldset>`}</div>` },
    label:{ title:label ? 'Изменить тег' : 'Новый тег', submit:label ? 'Сохранить' : 'Создать тег', html:`<div class="form-grid"><label class="field">Название<input name="name" required autofocus value="${escapeHtml(label?.name || '')}" /></label><label class="field">Проект<select name="direction_id"><option value="">Общий тег</option>${state.projects.map((item) => `<option value="${item.id}" ${label?.direction_id === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label class="field">Цвет${colorField(label?.color || '#356AE6')}</label></div>` },
    worktime:{ title:'Рабочее время на дату', submit:'Сохранить интервалы', html:`<div class="form-grid"><label class="field">Дата<input name="date" type="date" value="${dateKey(state.weekStart)}" required /></label><div id="work-slots">${(day?.availability.length ? day.availability.map((slot) => workSlot(localMinute(slot.start_at), localMinute(slot.end_at))).join('') : workSlot(540,720))}</div><button type="button" class="button secondary" data-action="add-work-slot">+ Добавить промежуток</button><p class="modal-note">Удалите все строки, чтобы освободить день от рабочего времени.</p></div>` },
    block:{ title:'Изменить задачу и блок', submit:'Сохранить изменения', html:`<div class="combined-block-editor">${taskTemplate}<section class="form-section block-placement-fields"><h3>Размещение в плане</h3><label class="field">Дата<input name="date" type="date" value="${blockItem ? dateKey(blockItem.start_at) : dateKey(state.weekStart)}" required /></label><div class="inline-fields"><label class="field">Начало<input name="start" type="time" step="60" value="${blockItem ? timeValue(localMinute(blockItem.start_at)) : '09:00'}" required /></label><label class="field">Конец<input name="end" type="time" step="60" value="${blockItem ? timeValue(localMinute(blockItem.end_at)) : '10:00'}" required /></label></div><p class="modal-note">Время можно указать с точностью до минуты. Изменение блока само по себе не меняет срок задачи.</p></section></div>` },
    complete:{ title:'Завершить задачу', submit:'Завершить', html:`<div class="form-grid"><label class="field">Фактическое время, минут<input name="actual_minutes" type="number" min="1" step="5" placeholder="Например, 75" /></label>${task?.repeat_rule === 'WEEKLY' ? '<p class="modal-note">Срок задачи не изменится. При необходимости верните задачу в активные кнопкой с галочкой.</p>' : ''}</div>` },
    event:{ title:eventItem ? 'Изменить событие' : 'Неподвижное событие', submit:eventItem ? 'Сохранить' : 'Добавить событие', html:eventFormTemplate(eventItem, options, colorField) },
    'event-delete':{ title:'Удалить событие', submit:'Удалить', html:`<div class="form-grid"><p>«${escapeHtml(eventItem?.title || 'Событие')}» ${options.eventDate ? `за ${new Date(`${options.eventDate}T12:00:00`).toLocaleDateString('ru-RU', { day:'numeric', month:'long' })}` : ''}</p>${eventItem?.weekday !== null && options.eventDate ? '<label class="event-scope-choice"><input type="checkbox" name="apply_all" /> Применить ко всем повторам событий</label><p class="modal-note">Без галочки удалится только выбранный день.</p>' : '<p class="modal-note">Событие перейдёт в архив.</p>'}</div>` },
    manual:{ title:'Внести фактическое время', submit:'Сохранить время', html:`<div class="form-grid"><label class="field">Задача<select name="task_id"><option value="">Общая работа</option>${state.tasks.filter((item) => item.status === 'ACTIVE').map((item) => `<option value="${item.id}">${escapeHtml(item.title)}</option>`).join('')}</select></label><div class="inline-fields"><label class="field">Начало<input name="started_at" type="datetime-local" value="${dateKey(new Date())}T09:00" required /></label><label class="field">Конец<input name="ended_at" type="datetime-local" value="${dateKey(new Date())}T10:00" required /></label></div></div>` },
    api:{ title:'Адрес сервера', submit:'Подключить', html:`<div class="form-grid"><label class="field">API<input name="api" value="${state.apiBase}" required /></label></div>` },
  };
  const template = templates[type];
  $('#modal-kicker').textContent = ['task','goal','direction','project-group','label','event'].includes(type) ? 'СОЗДАНИЕ И ИЗМЕНЕНИЕ' : 'НАСТРОЙКА';
  $('#modal-title').textContent = template.title;
  $('#modal-submit').textContent = template.submit;
  $('#modal-content').innerHTML = template.html;
  modal.dataset.type = type;
  modal.dataset.entityId = entityId || '';
  modal.dataset.eventDate = options.eventDate || '';
  modal.dataset.eventRepeats = String(eventItem?.weekday !== null && eventItem?.weekday !== undefined);
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
        if (inherited.default_deadline_at) $('#modal-content [name="deadline"]').value = inherited.default_deadline_at.slice(0, 16);
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
  }
  modal.showModal();
}
function workSlot(start = 540, end = 720) { return `<div class="inline-fields work-slot"><label class="field">Начало<input name="work_start" type="time" value="${timeValue(start)}" required /></label><label class="field">Конец<input name="work_end" type="time" value="${timeValue(end)}" required /></label><button type="button" class="icon-button compact" data-action="remove-work-slot" aria-label="Удалить промежуток">×</button></div>`; }

function taskPayloadFromFields(fields, currentTask = null) {
  const estimate = Number(fields.get('estimate'));
  const deadline = fields.get('deadline');
  return {
    title:fields.get('title'),
    direction_id:fields.get('direction_id') || currentTask?.project_id || currentTask?.direction?.id || null,
    goal_id:fields.get('goal_id') || currentTask?.goal_id || null,
    parent_task_id:fields.get('parent_task_id') || currentTask?.parent_task_id || null,
    is_checkpoint:fields.has('is_checkpoint') ? Boolean(fields.get('is_checkpoint')) : Boolean(currentTask?.is_checkpoint),
    color:fields.get('color'),
    label_ids:fields.getAll('label_ids'),
    estimate_minutes:estimate,
    min_block_minutes:Math.min(30, estimate),
    preferred_block_minutes:estimate,
    can_split:Boolean(fields.get('can_split')),
    priority:Number(fields.get('priority')),
    deadline_at:deadline ? new Date(deadline).toISOString() : null,
    repeat_rule:fields.get('repeat_rule') ? 'WEEKLY' : 'NONE',
  };
}

async function submitModal(event) {
  event.preventDefault(); const modal = $('#modal'); const fields = new FormData(event.currentTarget); const type = modal.dataset.type; const entityId = modal.dataset.entityId; const id = state.workspace.id;
  try {
    if (type === 'task') { const currentTask = entityId ? state.tasks.find((item) => item.id === entityId) || projectTaskForDetail(entityId) : null; const payload = taskPayloadFromFields(fields, currentTask); await api(entityId ? `/workspaces/${id}/tasks/${entityId}` : `/workspaces/${id}/tasks`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'goal') { const deadline = fields.get('deadline'); const payload = { title:fields.get('title'), direction_id:fields.get('direction_id') || null, description:fields.get('description') || null, color:fields.get('color'), priority:Number(fields.get('priority')), deadline_at:deadline ? new Date(deadline).toISOString() : null }; await api(entityId ? `/workspaces/${id}/goals/${entityId}` : `/workspaces/${id}/goals`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'direction') { const deadline = fields.get('deadline'); const payload = { name:fields.get('name'), kind:fields.get('kind'), color:fields.get('color'), default_priority:Number(fields.get('priority')), default_estimate_minutes:fields.get('estimate') ? Number(fields.get('estimate')) : null, default_deadline_at:deadline ? new Date(deadline).toISOString() : null }; await api(entityId ? `/workspaces/${id}/projects/${entityId}` : `/workspaces/${id}/projects`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'project-group') { const payload = entityId ? { name:fields.get('name'), color:fields.get('color') } : { name:fields.get('name'), color:fields.get('color'), project_ids:fields.getAll('project_ids') }; await api(entityId ? `/workspaces/${id}/project-groups/${entityId}` : `/workspaces/${id}/project-groups`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); state.selectedProjectIds.clear(); }
    if (type === 'label') { const payload = { name:fields.get('name'), direction_id:fields.get('direction_id') || null, color:fields.get('color') }; await api(entityId ? `/workspaces/${id}/labels/${entityId}` : `/workspaces/${id}/labels`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); }
    if (type === 'worktime') { const slots = fields.getAll('work_start').map((start, index) => start && fields.getAll('work_end')[index] ? { start_minute:toMinutes(start), end_minute:toMinutes(fields.getAll('work_end')[index]) } : null).filter(Boolean); await api(`/workspaces/${id}/availability/dates/${fields.get('date')}`, { method:'PUT', body:JSON.stringify({ slots }) }); }
    if (type === 'block') { const block = state.week?.days.flatMap((day) => day.blocks).find((item) => item.id === entityId); const task = state.tasks.find((item) => item.id === block?.task_id); if (!block || !task) throw new Error('Задача или блок уже не найдены. Обновите расписание.'); const start = new Date(`${fields.get('date')}T${fields.get('start')}`); const end = new Date(`${fields.get('date')}T${fields.get('end')}`); if (end <= start) throw new Error('Конец блока должен быть позже начала.'); const updatedTask = await api(`/workspaces/${id}/tasks/${task.id}`, { method:'PATCH', body:JSON.stringify(taskPayloadFromFields(fields, task)) }); const updated = await api(`/workspaces/${id}/blocks/${entityId}`, { method:'PATCH', body:JSON.stringify({ start_at:start.toISOString(), end_at:end.toISOString(), is_pinned:true, allow_conflict:true, move_task_deadline:false }) }); applyTaskStatusLocally(updatedTask); removeBlockLocally(entityId, true); mergeBlocks([updated], true); modal.close(); renderTasks(); renderWeek(); showToast('Задача и её размещение обновлены'); scheduleBackgroundRefresh(); return; }
    if (type === 'complete') { const updated = await api(`/workspaces/${id}/tasks/${entityId}/complete`, { method:'POST', body:JSON.stringify({ actual_minutes:fields.get('actual_minutes') ? Number(fields.get('actual_minutes')) : null }) }); applyTaskStatusLocally(updated); if (updated.status === 'COMPLETED' && state.dailyProgress) state.dailyProgress.completed_count += 1; modal.close(); renderTasks(); renderWeek(); renderDailySuccess(); showToast('Задача завершена — блок остался в плане'); scheduleBackgroundRefresh(); return; }
    if (type === 'event') { const date = String(fields.get('date')); const eventDate = new Date(`${date}T12:00:00`); const occurrence = Boolean(entityId && modal.dataset.eventDate && modal.dataset.eventRepeats === 'true'); const values = { title:fields.get('title'), start_minute:toMinutes(fields.get('start')), end_minute:toMinutes(fields.get('end')), color:fields.get('color') }; if (occurrence && !fields.get('apply_all')) await api(`/workspaces/${id}/fixed-events/${entityId}/occurrences/${date}`, { method:'PATCH', body:JSON.stringify(values) }); else { const weekly = occurrence || Boolean(fields.get('weekly')); const payload = { ...values, weekday:weekly ? (eventDate.getDay() + 6) % 7 : null, local_date:weekly ? null : date }; await api(entityId ? `/workspaces/${id}/fixed-events/${entityId}` : `/workspaces/${id}/fixed-events`, { method:entityId ? 'PATCH' : 'POST', body:JSON.stringify(payload) }); } }
    if (type === 'event-delete') { const occurrence = Boolean(modal.dataset.eventDate && modal.dataset.eventRepeats === 'true' && !fields.get('apply_all')); const path = `/workspaces/${id}/fixed-events/${entityId}${occurrence ? `/occurrences/${modal.dataset.eventDate}` : ''}`; await api(path, { method:'DELETE' }); showToast(occurrence ? 'Повторение удалено только в выбранный день' : 'Серия событий перемещена в архив'); }
    if (type === 'manual') await api(`/workspaces/${id}/work-sessions/manual`, { method:'POST', body:JSON.stringify({ task_id:fields.get('task_id') || null, started_at:new Date(fields.get('started_at')).toISOString(), ended_at:new Date(fields.get('ended_at')).toISOString() }) });
    if (type === 'api') { state.apiBase = String(fields.get('api')).replace(/\/$/, ''); localStorage.setItem('planner-api', state.apiBase); }
    modal.close();
    if (type === 'api') { await initialize(); return; }
    await loadData();
    if (state.section === 'goal-detail' && state.goalDetail?.id) await openGoalPage(state.goalDetail.id);
    if (state.section === 'project-detail' && state.projectDetail?.id) await openProjectPage(state.projectDetail.id);
  } catch (error) { showToast(error.message, true); }
}

async function planSelected() { const selected = state.tasks.filter((task) => state.selected.has(task.id) && canScheduleTask(task) && task.planning_status !== 'PLANNED').map((task) => task.id); if (!selected.length) { showToast('Выберите хотя бы одну задачу, которую можно разместить.', true); return; } const requestedStart = new Date(state.weekStart); const end = new Date(requestedStart); end.setDate(end.getDate() + 7); const now = new Date(); const start = requestedStart > now ? requestedStart : now; if (end <= start) { showToast('Эта неделя уже завершилась. Автоплан не размещает задачи в прошлом.', true); return; } if (!state.week.days.reduce((sum, day) => sum + day.free_minutes, 0)) { showToast('Сначала задайте рабочее время.', true); openModal('worktime'); return; } try { state.plan = await api(`/workspaces/${state.workspace.id}/planner/runs`, { method:'POST', body:JSON.stringify({ task_ids:selected, horizon_start:start.toISOString(), horizon_end:end.toISOString() }) }); state.planPanelHidden = false; renderWeek(); renderPlan(); } catch (error) { showToast(error.message, true); } }
async function applyPlan() { try { const conflicts = state.plan.proposals.filter((item) => item.has_conflict); const allowConflicts = conflicts.length > 0 && confirm(`В варианте есть пересечения: ${conflicts.length}. Разместить их с пометкой конфликта?`); const accepted = allowConflicts ? state.plan.proposals.map((item) => item.id) : state.plan.proposals.filter((item) => !item.has_conflict).map((item) => item.id); const result = await api(`/workspaces/${state.workspace.id}/planner/runs/${state.plan.id}/apply`, { method:'POST', body:JSON.stringify({ accepted_proposal_ids:accepted, allow_conflicts:allowConflicts }) }); mergeBlocks(result.blocks, true); state.plan = null; state.selected.clear(); renderTasks(); renderWeek(); renderPlan(); showToast('Блоки сразу добавлены в расписание'); scheduleBackgroundRefresh(); } catch (error) { showToast(error.message, true); } }
async function startTask(taskId) { try { state.session = await api(`/workspaces/${state.workspace.id}/work-sessions/start`, { method:'POST', body:JSON.stringify({ task_id:taskId }) }); state.sessionBarHidden = false; localStorage.removeItem('planner-session-hidden'); renderSession(); } catch (error) { showToast(error.message, true); } }
async function toggleSession() { try { const action = state.session.status === 'RUNNING' ? 'pause' : 'resume'; state.session = await api(`/workspaces/${state.workspace.id}/work-sessions/${action}`, { method:'POST' }); renderSession(); } catch (error) { showToast(error.message, true); } }
async function finishSession() { try { await api(`/workspaces/${state.workspace.id}/work-sessions/finish`, { method:'POST' }); state.session = null; renderSession(); showToast('Сессия завершена'); } catch (error) { showToast(error.message, true); } }
async function loadReport() {
  try {
    const weekStart = new Date(`${state.reportWeekStart}T12:00:00`);
    const weekEnd = new Date(weekStart); weekEnd.setDate(weekEnd.getDate() + 6);
    const trendRange = reportTrendRange(state.reportDate);
    const [report, history, trend] = await Promise.all([
      api(`/workspaces/${state.workspace.id}/reports/daily/${state.reportDate}`),
      api(`/workspaces/${state.workspace.id}/reports/history?start_date=${state.reportWeekStart}&end_date=${dateKey(weekEnd)}`),
      api(`/workspaces/${state.workspace.id}/reports/history?start_date=${trendRange.startKey}&end_date=${trendRange.endKey}`),
    ]);
    state.report = report;
    state.reportHistory = history.days || [];
    state.reportTrend = trend.days || [];
    if (state.reportDate === dateKey(new Date())) state.dailyProgress = report;
    renderDailySuccess();
    renderSection();
  } catch (error) { showToast(error.message, true); }
}
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

function projectLayoutPayload() {
  const root = orderedProjectRoot();
  return { ungrouped_project_ids:root.filter((entry) => entry.kind === 'project').map((entry) => entry.id), groups:state.projectGroups.map((group) => ({ id:group.id, project_ids:orderedProjects(group.id).map((project) => project.id) })), root_items:root.map((entry) => ({ kind:entry.kind, id:entry.id })) };
}
function setProjectRootOrder(root) {
  root.forEach((entry, position) => { entry.item.position = position; });
}
function moveProjectGroupLocally(groupId, targetKind, targetId, after) {
  const root = orderedProjectRoot().filter((entry) => !(entry.kind === 'group' && entry.id === groupId));
  const targetIndex = root.findIndex((entry) => entry.kind === targetKind && entry.id === targetId);
  const source = state.projectGroups.find((group) => group.id === groupId);
  if (!source) throw new Error('Группа больше не существует.');
  root.splice(targetIndex < 0 ? root.length : targetIndex + (after ? 1 : 0), 0, { kind:'group', id:source.id, item:source });
  setProjectRootOrder(root);
}
function moveProjectLocally(projectId, targetGroupId, targetProjectId = null, placeAfter = false) {
  const normalizedGroupId = targetGroupId || null;
  const root = orderedProjectRoot().filter((entry) => !(entry.kind === 'project' && entry.id === projectId));
  const containers = new Map([[null, orderedProjects(null).map((project) => project.id)]]);
  state.projectGroups.forEach((group) => containers.set(group.id, orderedProjects(group.id).map((project) => project.id)));
  for (const ids of containers.values()) { const index = ids.indexOf(projectId); if (index >= 0) ids.splice(index, 1); }
  const destination = containers.get(normalizedGroupId);
  if (!destination) throw new Error('Группа для перемещения больше не существует.');
  const targetIndex = targetProjectId ? destination.indexOf(targetProjectId) : -1;
  destination.splice(targetIndex >= 0 ? targetIndex + (placeAfter ? 1 : 0) : destination.length, 0, projectId);
  for (const [groupId, ids] of containers) ids.forEach((id, position) => { const project = state.projects.find((item) => item.id === id); if (project) { project.group_id = groupId; if (groupId) project.position = position; } });
  if (!normalizedGroupId) {
    const project = state.projects.find((item) => item.id === projectId);
    const rootTargetIndex = root.findIndex((entry) => entry.kind === 'project' && entry.id === targetProjectId);
    root.splice(rootTargetIndex < 0 ? root.length : rootTargetIndex + (placeAfter ? 1 : 0), 0, { kind:'project', id:projectId, item:project });
  }
  setProjectRootOrder(root);
}
async function saveProjectLayout() {
  await api(`/workspaces/${state.workspace.id}/project-layout`, { method:'PUT', body:JSON.stringify(projectLayoutPayload()) });
}
async function toggleProjectGroup(groupId) {
  const group = state.projectGroups.find((item) => item.id === groupId); if (!group) return;
  try { await api(`/workspaces/${state.workspace.id}/project-groups/${groupId}`, { method:'PATCH', body:JSON.stringify({ is_collapsed:!group.is_collapsed }) }); await loadData(); } catch (error) { showToast(error.message, true); }
}
async function dissolveProjectGroup(groupId) {
  if (!confirm('Расформировать группу? Проекты останутся и вернутся в общий список.')) return;
  try { await api(`/workspaces/${state.workspace.id}/project-groups/${groupId}`, { method:'DELETE' }); await loadData(); showToast('Группа расформирована, проекты сохранены'); } catch (error) { showToast(error.message, true); }
}

function showToast(message, error = false) { const toast = $('#toast'); $('#toast-message').textContent = message; toast.className = `toast show ${error ? 'error' : ''}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => { toast.className = 'toast'; }, 5000); }
function renderOffline() { $('#week-range').textContent = 'Нет подключения'; $('#task-list').innerHTML = '<div class="empty"><strong>Сервер не запущен</strong></div>'; }

document.addEventListener('click', async (event) => {
  const start = event.target.closest('[data-start-task]'); if (start) { startTask(start.dataset.startTask); return; }
  const projectCard = event.target.closest('[data-project-card]'); if (projectCard && !event.target.closest('button,.project-drag-handle') && Date.now() - state.projectDragEndedAt > 300) { openProjectPage(projectCard.dataset.projectCard); return; }
  const taskCard = event.target.closest('[data-task-card]'); if (taskCard && !event.target.closest('button,input')) { const id = taskCard.dataset.taskCard; const task = state.tasks.find((item) => item.id === id); if (task && canScheduleTask(task) && task.planning_status !== 'PLANNED') { state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id); renderTasks(); } return; }
  const fixed = event.target.closest('[data-fixed-event]'); if (fixed && !event.target.closest('[data-action]')) return;
  const action = event.target.closest('[data-action]'); if (!action) return; const id = action.dataset.id;
  if (action.dataset.action === 'new-task') openModal('task');
  if (action.dataset.action === 'catalog-kind-filter') { state.catalogTaskKind = action.dataset.kind; renderSection(); }
  if (action.dataset.action === 'new-child-task') { const parent = state.tasks.find((item) => item.id === id) || goalTaskForDetail(id) || projectTaskForDetail(id); openModal('task', null, { goalId:parent?.goal_id || state.goalDetail?.id, projectId:parent?.project_id || state.projectDetail?.id, parentTaskId:id }); }
  if (action.dataset.action === 'new-goal') openGoalPage();
  if (action.dataset.action === 'toggle-task-node') { state.collapsedTaskNodes.has(id) ? state.collapsedTaskNodes.delete(id) : state.collapsedTaskNodes.add(id); renderSection(); }
  if (action.dataset.action === 'new-direction') openModal('direction');
  if (action.dataset.action === 'new-project') openModal('direction');
  if (action.dataset.action === 'new-project-group') openModal('project-group');
  if (action.dataset.action === 'edit-project-group') openModal('project-group', id);
  if (action.dataset.action === 'toggle-project-group') toggleProjectGroup(id);
  if (action.dataset.action === 'dissolve-project-group') dissolveProjectGroup(id);
  if (action.dataset.action === 'toggle-project-selection') { state.selectedProjectIds.has(id) ? state.selectedProjectIds.delete(id) : state.selectedProjectIds.add(id); renderSection(); }
  if (action.dataset.action === 'clear-project-selection') { state.selectedProjectIds.clear(); renderSection(); }
  if (action.dataset.action === 'open-project') openProjectPage(id);
  if (action.dataset.action === 'edit-project') openModal('direction', id);
  if (action.dataset.action === 'back-to-projects') { state.section = 'projects'; state.projectDetail = null; renderSection(); }
  if (action.dataset.action === 'new-project-root') openModal('task', null, { projectId:id });
  if (action.dataset.action === 'new-project-child') openModal('task', null, { projectId:state.projectDetail?.id, parentTaskId:id });
  if (action.dataset.action === 'edit-project-task') openModal('task', id, { projectId:state.projectDetail?.id });
  if (action.dataset.action === 'delete-project-task') { if (!confirm('Переместить задачу и её вложенные пункты в архив?')) return; try { await api(`/workspaces/${state.workspace.id}/tasks/${id}`, { method:'DELETE' }); removeArchivedTaskTreeLocally(id); await loadData(); await openProjectPage(state.projectDetail.id); showToast('Задача перемещена в архив'); } catch (error) { showToast(error.message, true); } }
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
  if (action.dataset.action === 'delete-direction') deleteEntity(`/workspaces/${state.workspace.id}/directions/${id}`, 'Проект перемещён в архив');
  if (action.dataset.action === 'delete-project') { if (!confirm('Переместить проект в архив? Задачи сохранятся и останутся в расписании.')) return; try { await api(`/workspaces/${state.workspace.id}/projects/${id}`, { method:'DELETE' }); state.projectDetail = null; state.section = 'projects'; await loadData(); showToast('Проект перемещён в архив'); } catch (error) { showToast(error.message, true); } }
  if (action.dataset.action === 'delete-label') deleteEntity(`/workspaces/${state.workspace.id}/labels/${id}`, 'Тег удалён');
  if (action.dataset.action === 'delete-event') { if (action.dataset.date) openModal('event-delete', id, { eventDate:action.dataset.date }); else deleteEntity(`/workspaces/${state.workspace.id}/fixed-events/${id}`, 'Расписание удалено'); }
  if (action.dataset.action === 'cancel-block') cancelBlock(id);
  if (action.dataset.action === 'complete-block') completeBlock(id);
  if (action.dataset.action === 'restore') transitionTask(id, 'restore');
  if (action.dataset.action === 'show-trash') loadDeletedTasks();
  if (action.dataset.action === 'open-archive') { state.section = 'archive'; loadArchive(); }
  if (action.dataset.action === 'archive-section') { state.archiveSection = action.dataset.archiveSection; renderSection(); }
  if (action.dataset.action === 'restore-archived') restoreArchivedEntity(action.dataset.entityType, id);
  if (action.dataset.action === 'purge-archived') purgeArchivedEntity(action.dataset.entityType, id);
  if (action.dataset.action === 'refresh-report') loadReport();
  if (action.dataset.action === 'report-previous') shiftReportDate(-1);
  if (action.dataset.action === 'report-next') shiftReportDate(1);
  if (action.dataset.action === 'report-today' || action.dataset.action === 'report-current-week') showCurrentReportWeek();
  if (action.dataset.action === 'report-week-previous') shiftReportWeek(-1);
  if (action.dataset.action === 'report-week-next') shiftReportWeek(1);
  if (action.dataset.action === 'select-report-date') selectReportDate(action.dataset.date);
  if (action.dataset.action === 'show-session-timeline') setSessionView('timeline');
  if (action.dataset.action === 'show-session-tasks') setSessionView('tasks');
  if (action.dataset.action === 'add-manual') openModal('manual');
  if (action.dataset.action === 'dashboard-worktime') openModal('worktime');
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
$('#show-schedule-view').addEventListener('click', () => setPlanMode('schedule'));
$('#show-capacity-view').addEventListener('click', () => setPlanMode('capacity'));
$('#task-sort').addEventListener('change', (event) => { state.taskSort = event.target.value; localStorage.setItem('planner-task-sort', state.taskSort); renderTasks(); if (state.section === 'tasks') renderSection(); });
document.addEventListener('change', (event) => { if (event.target.id === 'report-date' && event.target.value) selectReportDate(event.target.value); });
document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => { document.querySelector('.filter.active').classList.remove('active'); button.classList.add('active'); state.filter = button.dataset.filter; renderTasks(); }));
document.querySelectorAll('.kind-filter').forEach((button) => button.addEventListener('click', () => { document.querySelector('.kind-filter.active')?.classList.remove('active'); button.classList.add('active'); state.taskKindFilter = button.dataset.kindFilter; state.selected.clear(); renderTasks(); }));
document.querySelectorAll('.nav-item[data-section]').forEach((button) => button.addEventListener('click', () => { document.querySelector('.nav-item.active').classList.remove('active'); button.classList.add('active'); state.section = button.dataset.section; renderSection(); if (state.section === 'results') showCurrentReportWeek(); if (state.section === 'archive') loadArchive(); }));
$('#theme-toggle').addEventListener('click', () => { const dark = document.documentElement.dataset.theme !== 'dark'; document.documentElement.dataset.theme = dark ? 'dark' : ''; localStorage.setItem('planner-theme', dark ? 'dark' : 'light'); $('#theme-toggle').setAttribute('aria-pressed', String(dark)); });
$('#add-task').addEventListener('click', () => openModal('task')); $('#quick-task').addEventListener('click', () => openModal('task')); $('#edit-worktime').addEventListener('click', () => openModal('worktime')); $('#record-worktime').addEventListener('click', () => openModal('manual')); $('#add-event').addEventListener('click', () => openModal('event')); $('#hide-overview').addEventListener('click', () => { state.planOverviewHidden = true; localStorage.setItem('planner-overview-hidden', 'true'); renderPlanOverview(); }); $('#show-overview').addEventListener('click', () => { state.planOverviewHidden = false; localStorage.removeItem('planner-overview-hidden'); renderPlanOverview(); }); $('#api-settings').addEventListener('click', () => openModal('api'));
$('#toggle-nav-panel').addEventListener('click', () => { state.navPanelHidden = true; localStorage.setItem('planner-nav-panel-hidden', 'true'); renderTaskPanelVisibility(); });
$('#show-nav-panel').addEventListener('click', () => { state.navPanelHidden = false; localStorage.removeItem('planner-nav-panel-hidden'); renderTaskPanelVisibility(); });
$('#toggle-task-panel').addEventListener('click', () => { state.panelHidden = true; localStorage.setItem('planner-task-panel-hidden', 'true'); renderTaskPanelVisibility(); });
$('#show-task-panel').addEventListener('click', () => { state.panelHidden = false; localStorage.removeItem('planner-task-panel-hidden'); renderTaskPanelVisibility(); });
$('#modal-close').addEventListener('click', () => $('#modal').close()); $('#modal-cancel').addEventListener('click', () => $('#modal').close()); $('#modal-delete').addEventListener('click', deleteTaskFromModal); $('#modal-form').addEventListener('submit', submitModal); $('#plan-selected').addEventListener('click', planSelected); $('#clear-selection').addEventListener('click', () => { state.selected.clear(); renderTasks(); }); $('#hide-plan').addEventListener('click', () => { state.planPanelHidden = true; renderPlan(); }); $('#show-plan').addEventListener('click', () => { state.planPanelHidden = false; renderPlan(); }); $('#discard-plan').addEventListener('click', () => { state.plan = null; renderWeek(); renderPlan(); }); $('#replan').addEventListener('click', planSelected); $('#apply-plan').addEventListener('click', applyPlan); $('#session-pause').addEventListener('click', toggleSession); $('#session-finish').addEventListener('click', finishSession); $('#session-hide').addEventListener('click', () => { state.sessionBarHidden = true; localStorage.setItem('planner-session-hidden','true'); renderSession(); }); $('#session-restore').addEventListener('click', () => { state.sessionBarHidden = false; localStorage.removeItem('planner-session-hidden'); renderSession(); }); $('#toast-close').addEventListener('click', () => { $('#toast').className = 'toast'; });
document.addEventListener('submit', (event) => { if (event.target.id === 'goal-detail-form') { event.preventDefault(); saveGoalDetail(event.target); } if (event.target.id === 'goal-new-task-form') { event.preventDefault(); createGoalTask(event.target); } if (event.target.id === 'project-detail-form') { event.preventDefault(); saveProjectDetail(event.target); } });

function clearProjectDropStyles() { document.querySelectorAll('.project-board.drop-target,.project-group.drop-target,.project-card.drop-before,.project-card.drop-after,.project-group.drop-before,.project-group.drop-after').forEach((item) => item.classList.remove('drop-target','drop-before','drop-after')); }
document.addEventListener('dragstart', (event) => {
  const projectCard = event.target.closest('[data-project-card]');
  if (projectCard) {
    if (event.target.closest('button')) { event.preventDefault(); return; }
    state.projectDrag = { id:projectCard.dataset.projectCard };
    event.dataTransfer.setData('text/plain', `project:${state.projectDrag.id}`);
    event.dataTransfer.effectAllowed = 'move';
    projectCard.classList.add('dragging');
    return;
  }
  const projectGroup = event.target.closest('[data-project-group-section]');
  if (projectGroup) {
    if (event.target.closest('button')) { event.preventDefault(); return; }
    state.projectGroupDrag = { id:projectGroup.dataset.projectGroupSection };
    event.dataTransfer.setData('text/plain', `project-group:${state.projectGroupDrag.id}`);
    event.dataTransfer.effectAllowed = 'move'; projectGroup.classList.add('dragging'); return;
  }
  if (event.target.closest('.block-actions')) { event.preventDefault(); return; }
  const block = event.target.closest('[data-schedule-block]'); const card = event.target.closest('[data-drag-task]'); if (!block && !card) return;
  state.dragSource = block ? { kind:'block', id:block.dataset.scheduleBlock } : { kind:'task', id:card.dataset.dragTask };
  event.dataTransfer.setData('text/plain', `${state.dragSource.kind}:${state.dragSource.id}`); event.dataTransfer.effectAllowed = block ? 'move' : 'copy'; (block || card).classList.add('dragging');
});
document.addEventListener('dragend', (event) => {
  event.target.closest('[data-schedule-block],[data-drag-task],[data-project-card],[data-project-group-section]')?.classList.remove('dragging');
  document.querySelectorAll('.day-column.drop-target').forEach((day) => day.classList.remove('drop-target'));
  if (state.projectDrag || state.projectGroupDrag) state.projectDragEndedAt = Date.now();
  clearProjectDropStyles(); state.dragSource = null; state.projectDrag = null; state.projectGroupDrag = null;
});
document.addEventListener('dragover', (event) => {
  if (state.projectGroupDrag) {
    const board = event.target.closest('.project-board'); if (!board) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; clearProjectDropStyles(); board.classList.add('drop-target');
    const target = event.target.closest('[data-root-kind]');
    if (target && !(target.dataset.rootKind === 'group' && target.dataset.rootId === state.projectGroupDrag.id)) { const rect = target.getBoundingClientRect(); target.classList.add(event.clientX > rect.left + rect.width / 2 ? 'drop-after' : 'drop-before'); }
    return;
  }
  if (state.projectDrag) {
    const zone = event.target.closest('[data-project-dropzone]'); if (!zone) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; clearProjectDropStyles(); zone.classList.add('drop-target');
    const target = event.target.closest('[data-project-card]');
    if (target && target.dataset.projectCard !== state.projectDrag.id) { const rect = target.getBoundingClientRect(); const after = event.clientY > rect.top + rect.height * .7 || Math.abs(event.clientY - (rect.top + rect.height / 2)) < rect.height * .25 && event.clientX > rect.left + rect.width / 2; target.classList.add(after ? 'drop-after' : 'drop-before'); }
    return;
  }
  const day = event.target.closest('.day-column'); if (!day || !state.dragSource) return; event.preventDefault(); document.querySelectorAll('.day-column.drop-target').forEach((column) => { if (column !== day) column.classList.remove('drop-target'); }); day.classList.add('drop-target');
});
document.addEventListener('dragleave', (event) => { const day = event.target.closest('.day-column'); if (day && !day.contains(event.relatedTarget)) day.classList.remove('drop-target'); const zone = event.target.closest('[data-project-dropzone]'); if (zone && !zone.contains(event.relatedTarget)) clearProjectDropStyles(); });
document.addEventListener('drop', async (event) => {
  if (state.projectGroupDrag) {
    const board = event.target.closest('.project-board'); if (!board) return;
    event.preventDefault(); event.stopPropagation(); const source = state.projectGroupDrag;
    const target = event.target.closest('[data-root-kind]'); const rect = target?.getBoundingClientRect();
    const targetKind = target?.dataset.rootKind || null; const targetId = target?.dataset.rootId || null;
    const after = Boolean(rect && (target.classList.contains('drop-after') || event.clientX > rect.left + rect.width / 2));
    state.projectGroupDrag = null; state.projectDragEndedAt = Date.now(); clearProjectDropStyles();
    try { moveProjectGroupLocally(source.id, targetKind, targetId, after); renderSection(); await saveProjectLayout(); showToast('Порядок групп сохранён'); } catch (error) { await loadData(); showToast(error.message, true); }
    return;
  }
  if (state.projectDrag) {
    const zone = event.target.closest('[data-project-dropzone]'); if (!zone) return;
    event.preventDefault(); event.stopPropagation(); const source = state.projectDrag; const target = event.target.closest('[data-project-card]'); const targetId = target?.dataset.projectCard === source.id ? null : target?.dataset.projectCard || null; let after = false;
    if (target && targetId) { const rect = target.getBoundingClientRect(); after = target.classList.contains('drop-after') || event.clientY > rect.top + rect.height * .7 || Math.abs(event.clientY - (rect.top + rect.height / 2)) < rect.height * .25 && event.clientX > rect.left + rect.width / 2; }
    state.projectDrag = null; state.projectDragEndedAt = Date.now(); clearProjectDropStyles();
    try { moveProjectLocally(source.id, zone.dataset.projectDropzone || null, targetId, after); renderSection(); await saveProjectLayout(); showToast('Порядок проектов сохранён'); } catch (error) { await loadData(); showToast(error.message, true); }
    return;
  }
  const day = event.target.closest('.day-column'); const source = state.dragSource; if (!day || !source) return; event.preventDefault(); event.stopPropagation(); document.querySelectorAll('.day-column.drop-target').forEach((column) => column.classList.remove('drop-target')); state.dragSource = null; const minute = minuteFromPointer(day, event); const base = new Date(`${day.dataset.date}T00:00:00`); base.setMinutes(minute); try { let updated; if (source.kind === 'block') { const old = state.week.days.flatMap((item) => item.blocks).find((item) => item.id === source.id); if (!old) throw new Error('Исходный блок уже не найден. Обновите неделю и попробуйте снова.'); const duration = Math.round((new Date(old.end_at).getTime() - new Date(old.start_at).getTime()) / 60000); const end = new Date(base); end.setMinutes(end.getMinutes() + duration); updated = await api(`/workspaces/${state.workspace.id}/blocks/${source.id}`, { method:'PATCH', body:JSON.stringify({ start_at:base.toISOString(), end_at:end.toISOString(), is_pinned:true, expected_version:old.version, allow_conflict:true, move_task_deadline:true }) }); updateTaskDeadlineLocally(old.task_id, updated.end_at); removeBlockLocally(source.id, true); showToast('Блок и срок задачи перенесены'); } else { const task = state.tasks.find((item) => item.id === source.id); if (!task) throw new Error('Задача уже не найдена. Обновите неделю и попробуйте снова.'); const remaining = Math.max(1, task.estimate_minutes - task.planned_minutes); const duration = task.can_split ? Math.min(task.preferred_block_minutes || remaining, remaining) : remaining; const end = new Date(base); end.setMinutes(end.getMinutes() + duration); updated = await api(`/workspaces/${state.workspace.id}/blocks`, { method:'POST', body:JSON.stringify({ task_id:task.id, start_at:base.toISOString(), end_at:end.toISOString(), is_pinned:true, allow_conflict:true }) }); showToast('Задача добавлена в план'); } mergeBlocks([updated], true); renderTasks(); renderWeek(); scheduleBackgroundRefresh(); } catch (error) { showToast(error.message, true); }
});
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
