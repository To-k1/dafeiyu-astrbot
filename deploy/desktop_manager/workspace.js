(() => {
  const root = $('fish-workspace');
  const target = root.dataset.target;
  const tabs = ['overview', 'team', 'services', 'archives', 'settings'];
  const statusLabels = {
    prepared:'已准备', dispatching:'正在派发', queued:'等待开始', submitting:'正在提交', submitted:'已提交',
    running:'进行中', go_wait:'等待 Go 额度', cancelling:'正在取消', completed:'已完成',
    needs_review:'待核实', failed:'未完成', cancelled:'已取消', continuing:'待续做', paused:'已暂停', stopping:'正在结束'
  };
  const phaseLabels = {
    connecting_worker:'连接工作进程', preparing_media:'准备媒体', fetching_media:'获取素材',
    extracting_frames:'抽取画面', analyzing:'分析素材', running_worker:'工作进程处理中',
    starting_tools:'启动工具', waiting_local_resources:'等待本机资源', restarting_worker:'重新连接工作进程',
    waiting_for_go_quota:'等待 Go 额度'
  };
  const jobKinds = {video:'DSH 代看', web:'网页检索', file:'文件任务', compute:'计算任务'};
  let services = [];
  let connected = false;
  let observed;
  let connectionError = '';
  let pausedByBrowser = false;
  let serviceRows = null;
  let activityRead = false;
  const state = {meetings: null, activity: null};
  const readTimes = {};

  const classic = root.nextElementSibling;
  const shortcuts = classic.querySelector('.jump .actions');
  $('fw-shortcuts').prepend(...Array.from(shortcuts.querySelectorAll('a[href]')).slice(0, 2));
  $('fw-shortcuts').firstElementChild.textContent = '房间 ↗';
  $('fw-message').append($('message'));
  $('fw-services').append($('services-title').parentElement, $('checks-title').closest('section'));
  $('fw-archives').append($('archive-title').parentElement);
  $('fw-team').append($('dsh-team'));
  $('fw-meeting-links').append(shortcuts.querySelector('a[href="/meetings"]'), $('dsh-meeting-ui'));
  $('fw-meeting-links').firstElementChild.textContent = '查看全部组会';
  $('dsh-meeting-ui').textContent = '在 DSH 中打开';
  $('fw-service-links').append(...shortcuts.children);
  $('fw-settings-actions').append($('refresh'), $('theme'));
  $('fw-settings').append(...classic.querySelectorAll(':scope > details'));
  const launchPermitted = !$('start').disabled;

  function currentTab() {
    const selected = new URLSearchParams(location.search).get('tab');
    return tabs.includes(selected) ? selected : 'overview';
  }

  function selectTab() {
    const selected = currentTab();
    for (const link of root.querySelectorAll('[data-tab]')) {
      if (link.dataset.tab === selected) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    for (const page of root.querySelectorAll('[data-page]')) page.hidden = page.dataset.page !== selected;
    if (selected === 'team') $('dsh-team').open = true;
  }

  root.querySelector('.fw-nav').addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const url = new URL(location.href);
    url.searchParams.set('tab', link.dataset.tab);
    history.pushState(null, '', url);
    selectTab();
  });
  window.addEventListener('popstate', selectTab);

  function clock(seconds) {
    return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}).format(
        seconds ? new Date(seconds * 1000) : new Date());
  }

  function writeAvailability() {
    const enabled = connected && !pausedByBrowser;
    const startable = services.some(service => service.state === 'stopped');
    $('fw-start').hidden = !launchPermitted || !startable;
    $('fw-start').disabled = $('fw-service-start').disabled = !enabled || !launchPermitted || !startable;
    $('fw-stop').disabled = !enabled || !services.some(service => service.stoppable);
    $('fw-services').querySelectorAll('#services .actions button').forEach(button => {
      if (!enabled) {
        if (!button.hasAttribute('data-offline')) button.dataset.offline = button.disabled ? 'disabled' : 'enabled';
        button.disabled = true;
      } else if (button.hasAttribute('data-offline')) {
        button.disabled = button.dataset.offline === 'disabled';
        delete button.dataset.offline;
      }
    });
    for (const form of root.querySelectorAll('form:not([method="dialog"])')) form.inert = !enabled;
    for (const container of [$('fw-archives'), $('fw-team'), $('fw-settings')]) {
      container.querySelectorAll('button').forEach(button => {
        if (button.closest('form') || /refresh|history|read|list/i.test(button.id)) return;
        button.inert = !enabled;
      });
    }
    if (!enabled && $('fw-stop-dialog').open) $('fw-stop-dialog').close('cancel');
  }

  function health() {
    const running = services.filter(service => service.state === 'running' && service.recognized);
    const uncertain = services.filter(service => service.state === 'running' && !service.recognized);
    const essential = new Set(['astrbot','dsh','napcat','budget']);
    const stopped = services.filter(service => essential.has(service.id) && service.state === 'stopped');
    $('fw-health').textContent = pausedByBrowser ? '浏览器已离线' : connected ?
      `${running.length} / ${services.length} 项服务在线` : observed ? '连接中断' : '尚未连接';
    $('fw-observed').textContent = observed ? (connected && !pausedByBrowser ? '服务检查 ' : '上次服务检查 ') +
      observed + ' 北京时间' : '';
    const alert = $('fw-alert');
    if (!connected || pausedByBrowser) {
      alert.textContent = connectionError || (pausedByBrowser ? '浏览器离线，操作已暂停。' : '');
    } else {
      alert.textContent = [...stopped.map(s => s.name + '已停止'), ...uncertain.map(s => s.name + '端口待识别')].join(' · ');
    }
    alert.hidden = !alert.textContent;
    writeAvailability();
  }

  function record(title, description, action) {
    const row = node('div', '', 'fw-record');
    const info = node('div');
    info.append(node('strong', title), node('small', description));
    row.append(info);
    if (action) row.append(action);
    return row;
  }

  function drawList(id, rows, empty) {
    $(id).replaceChildren(...(rows.length ? rows : [node('p', empty, 'muted')]));
  }

  function showReadTime(kind, stale = false) {
    const stamp = readTimes[kind];
    const ids = kind === 'meetings' ? ['fw-live-time', 'fw-ended-time'] : ['fw-activity-time', 'fw-results-time'];
    for (const id of ids) {
      const label = $(id);
      label.classList.toggle('is-stale', stale);
      label.textContent = stamp ? (stale ? '读取中断 · 上次读到 ' : '读到 ') + stamp
        : stale ? '暂时无法读取' : '';
    }
    if (kind === 'activity') $('fw-results-time').hidden = !stale;
    else $('fw-ended-time').hidden = !stale && state.meetings?.meetings.some(meeting => meetingActive.has(meeting.status));
  }

  function drawMeetings(snapshot) {
    const live = snapshot.meetings.filter(meeting => meetingActive.has(meeting.status));
    const ended = snapshot.meetings.filter(meeting => !meetingActive.has(meeting.status)).slice(0, 5);
    const draw = meeting => {
      const round = meeting.round ? '第 ' + meeting.round + ' 轮' + (meeting.rounds ? ' / ' + meeting.rounds : '') : '';
      const label = meetingActive.has(meeting.status) ? '旁听' : '回看';
      return record(meeting.topic, [meetingStates[meeting.status] || meeting.status, round].filter(Boolean).join(' · '),
        meetingLink(meeting, snapshot.group, label));
    };
    drawList('fw-live', live.map(draw), '没有正在开的组会');
    drawList('fw-ended', ended.map(draw), '暂无最近散场的组会');
    $('fw-live').closest('section').hidden = !live.length;
    $('fw-ended-all').hidden = !!live.length;
    $('fw-ended-time').hidden = !!live.length;
    root.querySelector('.fw-grid').dataset.idle = String(!live.length);
    $('fw-team-live').replaceChildren(...live.map(draw));
  }

  function drawActivity(snapshot) {
    const draw = row => record(row.title, [row.actor || jobKinds[row.kind] || 'DSH',
      phaseLabels[row.phase] || statusLabels[row.state] || row.state,
      row.at && ((row.time_label || '记录') + ' ' + clock(row.at))].filter(Boolean).join(' · '));
    drawList('fw-running', snapshot.running.map(draw), '没有已知的运行中任务');
    drawList('fw-results', snapshot.recent.map(draw), '暂无近期成果');
  }

  async function readActivity() {
    if (activityRead) return;
    activityRead = true;
    try {
      const snapshot = await api('/api/dashboard');
      readTimes.activity = clock();
      showReadTime('activity');
      if (JSON.stringify(snapshot) !== JSON.stringify(state.activity)) {
        drawActivity(snapshot);
        state.activity = snapshot;
      }
    } catch {
      showReadTime('activity', true);
      if (!state.activity) {
        drawList('fw-running', [], '任务状态暂时不可读');
        drawList('fw-results', [], '近期成果暂时不可读');
      }
    } finally {
      activityRead = false;
    }
  }

  window.fishhomeWorkspace = {
    canWrite: () => connected && !pausedByBrowser,
    hasConnectionFailure: () => pausedByBrowser || !!connectionError,
    serviceSnapshot(snapshot) {
      services = snapshot;
      connected = true;
      observed = clock();
      connectionError = '';
      health();
    },
    redrawServices(snapshot, acting) {
      const signature = JSON.stringify([snapshot, acting]);
      if (signature === serviceRows) return false;
      serviceRows = signature;
      return true;
    },
    connectionLost(error) {
      connected = false;
      const message = error?.message || '';
      connectionError = message ? message + ' 操作已暂停。' : '暂时联系不到 ' + target + '，操作已暂停。';
      health();
    },
    meetings(snapshot) {
      readTimes.meetings = clock();
      if (JSON.stringify(snapshot) === JSON.stringify(state.meetings)) {
        showReadTime('meetings');
        return;
      }
      state.meetings = snapshot;
      drawMeetings(snapshot);
      showReadTime('meetings');
    },
    meetingsFailed() { showReadTime('meetings', true); },
    activity: readActivity
  };
  window.addEventListener('offline', () => {
    pausedByBrowser = true;
    health();
  });
  window.addEventListener('online', () => {
    pausedByBrowser = false;
    refresh();
  });
  $('fw-start').onclick = $('fw-service-start').onclick = () => act('all','start');
  $('fw-stop').onclick = () => {
    const dialog = $('fw-stop-dialog');
    $('fw-stop-target').textContent = '操作目标 · ' + target;
    $('fw-stop-list').replaceChildren(...services.filter(service => service.stoppable)
      .map(service => node('li', service.name)));
    dialog.showModal();
  };
  $('fw-stop-dialog').addEventListener('close', event => {
    if (event.target.returnValue === 'stop' && window.fishhomeWorkspace.canWrite()) act('all','stop',true);
  });
  selectTab();
  health();
  for (const id of ['fw-live','fw-ended','fw-running','fw-results']) drawList(id, [], '正在读取');
})();
