const TASK_LABELS = {
  pre_market: '盘前准备',
  intraday: '盘中检查',
  close_review: '收盘复盘',
};

const ISSUE_STATES = new Set(['error', 'unavailable']);
const WARNING_STATES = new Set(['degraded', 'stopped']);

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function nextLabel(next) {
  if (!next || !next.at) return '';
  const at = new Date(next.at);
  if (Number.isNaN(at.getTime())) return '';
  const day = at.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  const time = at.toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return `下一次：${day} ${time}${text(next.label) ? ` · ${text(next.label)}` : ''}`;
}

export function buildServiceCenterStatus(routineValue, eventValue) {
  const routine = routineValue || {};
  const routineConfig = routine.config || {};
  const tasks = routineConfig.tasks || {};
  const activeTasks = Object.entries(TASK_LABELS)
    .filter(([key]) => tasks[key] === true)
    .map(([, label]) => label);
  const event = eventValue || {};
  const eventConfig = event.config || {};
  const eventEnabled = eventConfig.enabled === true;
  const enabledItems = [...activeTasks, ...(eventEnabled ? ['事件影响雷达'] : [])];
  const routineState = text(routine.runtime && routine.runtime.state) || 'disabled';
  const eventState = text(event.state || event.runtime && event.runtime.state)
    || (eventEnabled ? 'starting' : 'disabled');
  const routineAuthorized = activeTasks.length > 0;
  const issue = (routineAuthorized && ISSUE_STATES.has(routineState))
    || (eventEnabled && ISSUE_STATES.has(eventState));
  const warning = (routineAuthorized && WARNING_STATES.has(routineState))
    || (eventEnabled && WARNING_STATES.has(eventState));
  const paused = routineAuthorized && routineState === 'paused';

  let state = enabledItems.length ? 'active' : 'idle';
  let stateLabel = enabledItems.length ? '运行中' : '未开启';
  let alert = '';
  if (paused && enabledItems.length) {
    state = 'paused';
    stateLabel = '已暂停';
    alert = '日程已暂停到明早';
  }
  if (warning) {
    state = 'warning';
    stateLabel = '部分降级';
    alert = '部分主动服务需要检查';
  }
  if (issue) {
    state = 'error';
    stateLabel = '需要处理';
    alert = '主动服务连接或来源异常';
  }

  const summary = enabledItems.length
    ? `${enabledItems.length} 项已开启：${enabledItems.join('、')}`
    : '尚未开启持续主动服务';
  const next = nextLabel(routine.next_service)
    || (enabledItems.length ? '等待下一个已授权时段或新事件' : '可按需开启，不会默认访问外部来源');

  return {
    state,
    stateLabel,
    summary,
    next,
    alert,
    enabledCount: enabledItems.length,
    enabledItems,
    routineState,
    eventState,
  };
}
