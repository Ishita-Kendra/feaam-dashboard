/* ── State ────────────────────────────────────────────────────────────────── */
let slData = null;
let afData = null;
let allPositivesData = null;
let posTab = 'sl';
let afOwnerFilter = '';
let refreshTimer = null;
let countdownVal = 60;
const AUTO_SEC = 60;
const subCharts = {};   // campId -> Chart instance
const leadsCache = {};  // "{source}-{campId}" -> leads[]
let _tooltipHideTimer = null;

/* ── Boot ─────────────────────────────────────────────────────────────────── */
let lastClickFetch = 0;

document.addEventListener('DOMContentLoaded', () => {
  initDates();
  fetchAll();

  // Refresh on any click — but skip inputs/selects/buttons (they have their own handlers)
  // and enforce a 5-second cooldown so rapid clicks don't spam the API.
  document.addEventListener('click', (e) => {
    const tag = e.target.tagName;
    if (['INPUT','SELECT','BUTTON','A'].includes(tag)) return;
    const now = Date.now();
    if (now - lastClickFetch < 5000) return;
    lastClickFetch = now;
    fetchAll();
  });
});

/* ── Dates ────────────────────────────────────────────────────────────────── */
function initDates() {
  const s = document.getElementById('startDate');
  const e = document.getElementById('endDate');
  const today = new Date().toISOString().slice(0, 10);
  const ago30 = new Date(Date.now() - 29 * 864e5).toISOString().slice(0, 10);
  s.value = ago30; e.value = today;
  s.max = e.max = today;
  s.addEventListener('change', () => { e.min = s.value; fetchAll(); });
  e.addEventListener('change', () => { s.max = e.value; fetchAll(); });
}

function dates() {
  return { start_date: document.getElementById('startDate').value,
           end_date:   document.getElementById('endDate').value };
}

/* ── Fetch ────────────────────────────────────────────────────────────────── */
async function fetchAll() {
  setSpinning(true);
  clearError();
  const d = dates();
  const qs = `start_date=${d.start_date}&end_date=${d.end_date}`;
  const errors = [];

  const [slRes, afRes] = await Promise.allSettled([
    fetch(`/api/smartlead?${qs}`).then(r => r.json()),
    fetch('/api/aimfox').then(r => r.json()),
  ]);

  if (slRes.status === 'fulfilled' && slRes.value.ok) {
    slData = slRes.value;
    renderSL();
  } else {
    errors.push('Smartlead: ' + (slRes.reason?.message || slRes.value?.error || 'error'));
  }

  if (afRes.status === 'fulfilled' && afRes.value.ok) {
    afData = afRes.value;
    renderAF();
  } else {
    errors.push('Aimfox: ' + (afRes.reason?.message || afRes.value?.error || 'error'));
  }

  if (errors.length) showError(errors.join(' | '));
  document.getElementById('lastFetch').textContent =
    'Last fetch: ' + new Date().toLocaleTimeString();

  setSpinning(false);
  startCountdown();

  // Fetch all positives independently (slower, doesn't block the main view)
  fetchAllPositives();
}

/* ── All Positive Leads ───────────────────────────────────────────────────── */
async function fetchAllPositives() {
  document.getElementById('posList').innerHTML =
    '<div class="loading-msg">Fetching positive leads across all campaigns…</div>';
  document.getElementById('posSummary').style.display = 'none';
  try {
    const res = await fetch('/api/all-positives').then(r => r.json());
    if (res.ok) {
      allPositivesData = res;
      renderAllPositives();
    } else {
      document.getElementById('posList').innerHTML =
        `<div class="empty-msg">Could not load: ${esc(res.error || 'unknown error')}</div>`;
    }
  } catch (e) {
    document.getElementById('posList').innerHTML =
      '<div class="empty-msg">Failed to fetch positive leads.</div>';
  }
}

function setPosTab(tab, btn) {
  posTab = tab;
  document.querySelectorAll('.ptab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderAllPositives();
}

function renderAllPositives() {
  if (!allPositivesData) return;
  const leads = posTab === 'sl'
    ? (allPositivesData.smartlead || [])
    : (allPositivesData.aimfox   || []);

  const slCount = (allPositivesData.smartlead || []).length;
  const afCount = (allPositivesData.aimfox   || []).length;
  const summary = document.getElementById('posSummary');
  summary.style.display = 'flex';
  document.getElementById('posSummaryText').innerHTML =
    `<span class="pos-sum-chip sl-chip">${slCount} Interested</span>` +
    `<span class="pos-sum-sep">·</span>` +
    `<span class="pos-sum-chip af-chip">${afCount} Replied</span>` +
    `<span class="pos-sum-fetched">as of ${new Date(allPositivesData.fetched_at).toLocaleTimeString()}</span>`;

  const list = document.getElementById('posList');
  if (!leads.length) {
    list.innerHTML = `<div class="empty-msg">No ${posTab === 'sl' ? 'Interested' : 'Replied'} leads found across campaigns.</div>`;
    return;
  }

  // Group by campaign
  const byCamp = {};
  leads.forEach(l => {
    const key = l.campaign || 'Unknown Campaign';
    if (!byCamp[key]) byCamp[key] = { campaign_id: l.campaign_id, leads: [] };
    byCamp[key].leads.push(l);
  });

  list.innerHTML = Object.entries(byCamp).map(([camp, group]) => `
    <div class="pos-camp-group">
      <div class="pos-camp-header">
        <span class="pos-camp-name">${esc(camp)}</span>
        <span class="pos-camp-count">${group.leads.length} lead${group.leads.length > 1 ? 's' : ''}</span>
      </div>
      ${group.leads.map(l => posLeadRow(l, group.campaign_id)).join('')}
    </div>`).join('');
}

function posLeadRow(l, campId) {
  const id         = posTab === 'sl' ? esc(l.email || '') : String(l.id || '');
  const identifier = posTab === 'sl' ? esc(l.email || '') : 'LinkedIn lead';
  return `
<div class="pos-lead-row">
  <div class="pos-lead-left">
    <div class="pos-lead-name">${esc(l.name || '—')}</div>
    <div class="pos-lead-id">${identifier}</div>
  </div>
  <button class="pos-reply-btn"
    onclick="openLeadMessages('${posTab}', ${JSON.stringify(campId)}, '${id}', '${esc(l.name || '')}')">
    View Reply
  </button>
</div>`;
}

/* ── Countdown ────────────────────────────────────────────────────────────── */
function startCountdown() {
  if (refreshTimer) clearInterval(refreshTimer);
  countdownVal = AUTO_SEC;
  const fill  = document.getElementById('countdownFill');
  const label = document.getElementById('countdownLabel');
  fill.style.width = '100%';
  label.textContent = AUTO_SEC + 's';

  refreshTimer = setInterval(() => {
    countdownVal--;
    const pct = (countdownVal / AUTO_SEC) * 100;
    fill.style.width = pct + '%';
    label.textContent = countdownVal + 's';
    if (countdownVal <= 0) { clearInterval(refreshTimer); fetchAll(); }
  }, 1000);
}

/* ── Render Smartlead ─────────────────────────────────────────────────────── */
function renderSL() {
  if (!slData) return;
  const search = (document.getElementById('slSearch').value || '').toLowerCase();
  const status = document.getElementById('slStatus').value;
  const t = slData.totals || {};

  setText('ksl-sent',     fmt(t.sent));
  setText('ksl-opened',   fmt(t.opened) + '  ' + pctSpan(t.opened, t.sent));
  setText('ksl-replied',  fmt(t.replied) + '  ' + pctSpan(t.replied, t.sent));
  setText('ksl-positive', fmt(t.positive));
  setText('ksl-bounced',  fmt(t.bounced));

  let camps = slData.campaigns || [];
  if (search) camps = camps.filter(c => c.name.toLowerCase().includes(search));
  if (status) camps = camps.filter(c => c.status === status);

  setText('ksl-count', camps.length + ' campaign' + (camps.length !== 1 ? 's' : ''));

  const list = document.getElementById('slList');
  if (!camps.length) { list.innerHTML = '<div class="empty-msg">No campaigns match your filters</div>'; return; }

  const withSubs    = camps.filter(c => c.subsequences?.length);
  const withoutSubs = camps.filter(c => !c.subsequences?.length);

  // All main campaign rows grouped together
  const mainRows = camps.map(c => campaignRow(c, false)).join('');

  // Subsequence blocks separated below (collapsed by default)
  const subSection = withSubs.length ? `
    <div class="subs-section-divider">
      <span>Subsequence Analytics</span>
      <span class="subs-section-count">${withSubs.length} campaign${withSubs.length > 1 ? 's' : ''} with subsequences</span>
    </div>
    ${withSubs.map(c => subsBlock(c)).join('')}` : '';

  list.innerHTML = mainRows + subSection;

  // Draw charts (collapsed = no canvas yet, draw when opened)
  requestAnimationFrame(() => {
    withSubs.forEach(c => renderSubChart(c.id));
  });
}

/* ── Render Aimfox ────────────────────────────────────────────────────────── */
function setAfOwner(id, btn) {
  afOwnerFilter = id;
  document.querySelectorAll('.atab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderAF();
}

function renderAF() {
  if (!afData) return;

  // Account KPI cards
  const kpiEl = document.getElementById('afAccountKpis');
  kpiEl.innerHTML = (afData.accounts || []).map(a => `
    <div class="acct-card">
      <div class="acct-name">${esc(a.name)}</div>
      <div class="acct-metrics">
        <div class="acct-metric"><div class="am-label">Sent</div><div class="am-val" style="color:#a5b4fc">${fmt(a.totals?.sent)}</div></div>
        <div class="acct-metric"><div class="am-label">Accepted</div><div class="am-val" style="color:var(--blue)">${fmt(a.totals?.accepted)}</div></div>
        <div class="acct-metric"><div class="am-label">Messages</div><div class="am-val" style="color:var(--purple)">${fmt(a.totals?.messages)}</div></div>
        <div class="acct-metric"><div class="am-label">Replied</div><div class="am-val" style="color:var(--green)">${fmt(a.totals?.replied)}</div></div>
      </div>
    </div>
  `).join('');

  let camps = afData.campaigns || [];
  if (afOwnerFilter) {
    camps = camps.filter(c => c.owners && c.owners.includes(afOwnerFilter));
  }

  const list = document.getElementById('afList');
  if (!camps.length) { list.innerHTML = '<div class="empty-msg">No campaigns for this account</div>'; return; }
  list.innerHTML = camps.map(c => afCampaignRow(c)).join('');
}

/* ── Campaign row HTML ────────────────────────────────────────────────────── */
function campaignRow(c, isSub) {
  const progress = c.progress || 0;
  const ringColor = progress >= 90 ? '#22c55e' : progress >= 50 ? '#7c6ef7' : '#f59e0b';
  const circumference = 2 * Math.PI * 18;
  const offset = circumference * (1 - progress / 100);

  const created = c.created_at ? new Date(c.created_at).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'2-digit'}) : '';
  const hasSubs = c.subsequences && c.subsequences.length > 0;
  const subCount = hasSubs ? c.subsequences.length : 0;

  return `
<div class="camp-row${isSub ? ' is-sub' : ''}">
  <div class="camp-left">
    <div class="ring-wrap">
      <svg width="46" height="46" viewBox="0 0 46 46">
        <circle class="ring-bg" cx="23" cy="23" r="18"/>
        <circle class="ring-fg" cx="23" cy="23" r="18"
          stroke="${ringColor}"
          stroke-dasharray="${circumference}"
          stroke-dashoffset="${offset}"/>
      </svg>
      <div class="ring-label">${Math.round(progress)}%</div>
    </div>
    <div class="camp-info">
      <div class="camp-name" title="${esc(c.name)}">${esc(c.name)}</div>
      <div class="camp-meta">
        <span class="status-badge ${statusClass(c.status)}">${c.status}</span>
        ${created ? `<span class="meta-txt">${created}</span>` : ''}
        ${c.sequence_count ? `<span class="meta-sep">·</span><span class="meta-txt">${c.sequence_count} seq</span>` : ''}
        ${isSub ? '<span class="sub-badge">Subsequence</span>' : ''}
      </div>
      ${hasSubs ? `<span class="has-subs-hint">↳ ${subCount} subsequence${subCount>1?'s':''} below</span>` : ''}
    </div>
  </div>
  <div class="camp-metrics">
    ${metricCol('col-sent',    c.sent,    null,       '📤', 'Sent')}
    ${metricCol('col-opened',  c.opened,  c.open_pct, '📬', 'Opened')}
    ${isSub ? metricCol('col-clicked', c.clicked, null, '🖱️', 'Clicked') : ''}
    ${metricCol('col-replied', c.replied, c.reply_pct,'💬', 'Replied')}
    ${c.positive > 0 ? positiveMetricCol(c) : metricCol('col-positive', 0, null, '✅', 'Positive')}
  </div>
</div>`;
}

function subsBlock(c) {
  if (!c.subsequences || !c.subsequences.length) return '';
  const count = c.subsequences.length;
  return `
<div class="subs-container" id="subs-${c.id}">
  <!-- Clickable header — toggles body -->
  <div class="subs-label subs-label-toggle" onclick="toggleSubsBody(${c.id})">
    <span class="subs-label-left">
      <span class="subs-chevron" id="chevron-${c.id}">▶</span>
      ${esc(c.name)}
    </span>
    <span class="subs-label-right">
      <span class="subs-count">${count} subsequence${count > 1 ? 's' : ''}</span>
      <span class="subs-hint">Click to expand</span>
    </span>
  </div>

  <!-- Collapsible body (hidden by default) -->
  <div class="subs-body" id="subsBody-${c.id}" style="display:none">

    <!-- Chart -->
    <div class="sub-chart-wrap">
      <div class="sub-chart-legend">
        <span><span class="sub-chart-dot sent-dot"></span>Sent</span>
        <span><span class="sub-chart-dot opened-dot"></span>Opened</span>
        <span><span class="sub-chart-dot clicked-dot"></span>Clicked</span>
        <span><span class="sub-chart-dot replied-dot"></span>Replied</span>
        <span><span class="sub-chart-dot positive-dot"></span>Positive</span>
      </div>
      <div class="sub-chart-canvas-wrap">
        <canvas id="subChart-${c.id}"></canvas>
      </div>
    </div>

    <!-- Per-row breakdown -->
    <div class="subs-rows-label">Per Subsequence Breakdown</div>
    ${c.subsequences.map(s => campaignRow(s, true)).join('')}

  </div>
</div>`;
}

function toggleSubsBody(id) {
  const body    = document.getElementById(`subsBody-${id}`);
  const chevron = document.getElementById(`chevron-${id}`);
  const hint    = body?.previousElementSibling?.querySelector('.subs-hint');
  if (!body) return;
  const opening = body.style.display === 'none';
  body.style.display = opening ? 'block' : 'none';
  if (chevron) chevron.textContent = opening ? '▼' : '▶';
  if (hint)    hint.textContent    = opening ? 'Click to collapse' : 'Click to expand';
  if (opening) renderSubChart(id);   // draw chart on first open
}

function renderSubChart(campId) {
  const camp = (slData?.campaigns || []).find(c => c.id === campId);
  if (!camp || !camp.subsequences?.length) return;

  const canvas = document.getElementById(`subChart-${campId}`);
  if (!canvas) return;

  // Destroy old instance if re-rendering
  if (subCharts[campId]) { subCharts[campId].destroy(); delete subCharts[campId]; }

  const subs  = camp.subsequences;
  const names = subs.map(s => shortName(s.name));

  const datasets = [
    { label: 'Sent',     data: subs.map(s => s.sent     || 0), backgroundColor: 'rgba(165,180,252,.85)' },
    { label: 'Opened',   data: subs.map(s => s.opened   || 0), backgroundColor: 'rgba(245,158,11,.85)'  },
    { label: 'Clicked',  data: subs.map(s => s.clicked  || 0), backgroundColor: 'rgba(56,189,248,.85)'  },
    { label: 'Replied',  data: subs.map(s => s.replied  || 0), backgroundColor: 'rgba(34,197,94,.85)'   },
    { label: 'Positive', data: subs.map(s => s.positive || 0), backgroundColor: 'rgba(249,115,22,.85)'  },
  ];

  subCharts[campId] = new Chart(canvas, {
    type: 'bar',
    data: { labels: names, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1e2a',
          borderColor: '#252a38',
          borderWidth: 1,
          titleColor: '#e4e8f4',
          bodyColor: '#94a3b8',
          padding: 10,
        },
      },
      scales: {
        x: {
          ticks: { color: '#7a85a8', font: { size: 11 } },
          grid:  { color: 'rgba(255,255,255,0.04)' },
          title: {
            display: true,
            text: 'Subsequence',
            color: '#5a6380',
            font: { size: 11, weight: '600' },
            padding: { top: 8 },
          },
        },
        y: {
          ticks: { color: '#7a85a8', font: { size: 11 } },
          grid:  { color: 'rgba(255,255,255,0.04)' },
          beginAtZero: true,
          title: {
            display: true,
            text: 'Count',
            color: '#5a6380',
            font: { size: 11, weight: '600' },
            padding: { bottom: 8 },
          },
        },
      },
    },
  });
}

function shortName(name) {
  // Strip common FEAAM prefix and trim to ~22 chars
  return name.replace(/feaam[_\- ]*/i, '').replace(/subsequen(ce)?/i, 'Sub').trim().slice(0, 22);
}

/* ── Aimfox campaign row ──────────────────────────────────────────────────── */
function afCampaignRow(c) {
  const created = c.created_at
    ? new Date(c.created_at).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'2-digit'})
    : '';

  return `
<div class="camp-row af-row">
  <div class="camp-left">
    <div class="ring-wrap">
      <svg width="46" height="46" viewBox="0 0 46 46">
        <circle class="ring-bg" cx="23" cy="23" r="18"/>
        <circle class="ring-fg" cx="23" cy="23" r="18"
          stroke="var(--blue)"
          stroke-dasharray="${2 * Math.PI * 18}"
          stroke-dashoffset="${2 * Math.PI * 18 * (1 - Math.min(c.accept_pct / 100, 1))}"/>
      </svg>
      <div class="ring-label">${c.accept_pct}%</div>
    </div>
    <div class="camp-info">
      <div class="camp-name" title="${esc(c.name)}">${esc(c.name)}</div>
      <div class="camp-meta">
        <span class="status-badge ${statusClass(c.state)}">${c.state}</span>
        ${c.owner_names.map(n => `<span class="owner-badge">${esc(n)}</span>`).join('')}
        ${created ? `<span class="meta-txt">${created}</span>` : ''}
        ${c.target_count ? `<span class="meta-sep">·</span><span class="meta-txt">${fmt(c.target_count)} targets</span>` : ''}
      </div>
    </div>
  </div>
  <div class="camp-metrics">
    ${metricCol('col-sent',     c.sent,     null,           '📤', 'Sent')}
    ${metricCol('col-accepted', c.accepted, c.accept_pct,   '🤝', 'Accepted')}
    ${metricCol('col-messages', c.messages, null,           '💌', 'Messages')}
    ${c.replied > 0 ? afRepliedMetricCol(c) : metricCol('col-replied', 0, null, '💬', 'Replied')}
  </div>
</div>`;
}

/* ── Hoverable + clickable metric cols ───────────────────────────────────── */
function positiveMetricCol(c) {
  const pctVal = c.positive_pct > 0 ? c.positive_pct : null;
  const pctClass = pctVal == null ? '' : pctVal >= 30 ? 'good' : pctVal >= 10 ? 'mid' : 'low';
  return `
<div class="metric-col col-positive positive-hoverable"
     onmouseenter="showLeadTooltip(this,'sl',${c.id})"
     onmouseleave="scheduleHideTooltip()"
     onclick="openCampaignLeads('sl',${c.id},'${esc(c.name)}')">
  <span class="m-val">${fmt(c.positive)}</span>
  ${pctVal != null ? `<span class="m-pct ${pctClass}">${pctVal}%</span>` : '<span class="m-pct neutral">—</span>'}
  <span class="m-label"><span class="m-icon">✅</span>Positive</span>
</div>`;
}

function afRepliedMetricCol(c) {
  const pctVal = c.reply_pct > 0 ? c.reply_pct : null;
  const pctClass = pctVal == null ? '' : pctVal >= 30 ? 'good' : pctVal >= 10 ? 'mid' : 'low';
  return `
<div class="metric-col col-replied positive-hoverable"
     onmouseenter="showLeadTooltip(this,'af','${c.id}')"
     onmouseleave="scheduleHideTooltip()"
     onclick="openCampaignLeads('af','${c.id}','${esc(c.name)}')">
  <span class="m-val">${fmt(c.replied)}</span>
  ${pctVal != null ? `<span class="m-pct ${pctClass}">${pctVal}%</span>` : '<span class="m-pct neutral">—</span>'}
  <span class="m-label"><span class="m-icon">💬</span>Replied</span>
</div>`;
}

/* ── Tooltip logic (shared for SL + AF) ───────────────────────────────────── */
function showLeadTooltip(el, source, campId) {
  cancelHideTooltip();
  const rect = el.getBoundingClientRect();
  const tip  = document.getElementById('positiveTooltip');
  tip.style.top   = (rect.bottom + window.scrollY + 6) + 'px';
  tip.style.left  = 'auto';
  tip.style.right = (document.documentElement.clientWidth - rect.right) + 'px';
  tip.dataset.campId  = campId;
  tip.dataset.source  = source;
  tip.classList.remove('hidden');
  loadLeadTooltip(source, campId);
}

function scheduleHideTooltip() {
  _tooltipHideTimer = setTimeout(() => {
    document.getElementById('positiveTooltip').classList.add('hidden');
  }, 200);
}

function cancelHideTooltip() {
  if (_tooltipHideTimer) { clearTimeout(_tooltipHideTimer); _tooltipHideTimer = null; }
}

function loadLeadTooltip(source, campId) {
  const key     = `${source}-${campId}`;
  const leadsEl = document.getElementById('tooltipLeads');
  const titleEl = document.getElementById('tooltipTitle');

  if (leadsCache[key]) { renderLeadTooltip(leadsCache[key], source, campId); return; }

  titleEl.textContent = source === 'sl' ? 'Positive Leads' : 'Replied Leads';

  // Use pre-fetched allPositivesData — no per-campaign API call needed
  if (allPositivesData) {
    const pool  = source === 'sl' ? allPositivesData.smartlead : allPositivesData.aimfox;
    const leads = (pool || []).filter(l => String(l.campaign_id) === String(campId));
    leadsCache[key] = leads;
    renderLeadTooltip(leads, source, campId);
    return;
  }

  // allPositivesData still loading — show hint
  leadsEl.innerHTML = '<div class="tooltip-loading">Loading… click to open</div>';
}

function renderLeadTooltip(leads, source, campId) {
  const leadsEl = document.getElementById('tooltipLeads');
  const titleEl = document.getElementById('tooltipTitle');
  const label   = source === 'sl' ? 'Positive Leads' : 'Replied Leads';
  titleEl.textContent = `${label} (${leads.length})`;
  if (!leads.length) {
    leadsEl.innerHTML = `<div class="tooltip-loading">No ${label.toLowerCase()} found</div>`;
    return;
  }
  leadsEl.innerHTML = leads.map(l => {
    const id = source === 'sl' ? esc(l.email) : String(l.id || '');
    return `<button class="lead-name-btn"
      onclick="openLeadMessages('${source}',${JSON.stringify(campId)},'${id}','${esc(l.name)}')">
      ${esc(l.name)}
    </button>`;
  }).join('');
}

/* ── Lead message modal ───────────────────────────────────────────────────── */
function openLeadMessages(source, campId, identifier, name) {
  document.getElementById('positiveTooltip').classList.add('hidden');
  document.getElementById('leadModalName').textContent  = 'Reply from ' + name;
  document.getElementById('leadModalEmail').textContent =
    source === 'sl' ? identifier : 'LinkedIn lead';
  document.getElementById('leadModalBody').innerHTML =
    '<div class="tooltip-loading">Loading reply…</div>';
  document.getElementById('leadModal').classList.remove('hidden');

  const url = source === 'sl'
    ? `/api/smartlead/lead-messages?campaign_id=${campId}&email=${encodeURIComponent(identifier)}`
    : `/api/aimfox/lead-messages?campaign_id=${encodeURIComponent(campId)}&lead_id=${encodeURIComponent(identifier)}`;

  fetch(url).then(r => r.json()).then(data => {
    const body = document.getElementById('leadModalBody');
    if (!data.ok) {
      body.innerHTML = `<div class="tooltip-loading">Error: ${esc(data.error || 'unknown')}</div>`;
      return;
    }
    const msgs = data.messages || [];
    // Show only what the lead actually wrote back
    const replies = msgs.filter(m => isLeadReply(m));
    if (replies.length) {
      body.innerHTML = replies.map(m => renderReplyBubble(m)).join('');
    } else if (msgs.length) {
      // Fallback: API didn't distinguish direction — show everything
      body.innerHTML = msgs.map(m => renderReplyBubble(m)).join('');
    } else {
      body.innerHTML = '<div class="tooltip-loading">No reply content found.</div>';
    }
  }).catch(() => {
    document.getElementById('leadModalBody').innerHTML =
      '<div class="tooltip-loading">Failed to load reply.</div>';
  });
}

// Returns true for messages the lead sent (filters out our outbound messages)
function isLeadReply(m) {
  const t = (m.type || m.direction || m.message_type || m.email_type || '').toLowerCase();
  return !(t === 'sent' || t === 'email' || t === 'outgoing' || t === 'out' || t === 'outbound');
}

function renderReplyBubble(m) {
  const rawTime = m.time || m.created_at || m.sent_time || m.updated_at || m.timestamp || '';
  const time    = rawTime ? new Date(rawTime).toLocaleString() : '';
  const content = esc(m.content || m.message || m.body || m.email_body || m.text || m.message_body || '');
  return `
<div class="lead-msg-bubble received">
  ${time ? `<div class="msg-meta">${time}</div>` : ''}
  <div class="msg-body">${content || '<em style="color:var(--muted)">No content</em>'}</div>
</div>`;
}

function closeLeadModal() {
  document.getElementById('leadModal').classList.add('hidden');
}

/* ── Campaign leads modal (name + email + reply per lead) ─────────────────── */
function openCampaignLeads(source, campId, campName) {
  document.getElementById('positiveTooltip').classList.add('hidden');
  cancelHideTooltip();

  const label = source === 'sl' ? 'Positive Leads' : 'Replied Leads';
  document.getElementById('campLeadsTitle').textContent = campName;
  document.getElementById('campLeadsSubtitle').textContent = label;
  document.getElementById('campLeadsBody').innerHTML =
    '<div class="tooltip-loading">Loading…</div>';
  document.getElementById('campLeadsModal').classList.remove('hidden');

  function render(leads) {
    const body = document.getElementById('campLeadsBody');
    if (!leads.length) {
      body.innerHTML = `<div class="tooltip-loading">No ${label.toLowerCase()} found.</div>`;
      return;
    }
    body.innerHTML = leads.map(l => {
      const id = source === 'sl' ? esc(l.email || '') : String(l.id || '');
      const sub = source === 'sl'
        ? `<div class="camp-lead-email">${esc(l.email || '')}</div>`
        : `<div class="camp-lead-email">LinkedIn</div>`;
      return `
<div class="camp-lead-row">
  <div class="camp-lead-info">
    <div class="camp-lead-name">${esc(l.name || '—')}</div>
    ${sub}
  </div>
  <button class="pos-reply-btn"
    onclick="closeCampLeadsModal();openLeadMessages('${source}',${JSON.stringify(campId)},'${id}','${esc(l.name || '')}')">
    View Reply
  </button>
</div>`;
    }).join('');
  }

  // Use pre-fetched data if available
  if (allPositivesData) {
    const pool  = source === 'sl' ? allPositivesData.smartlead : allPositivesData.aimfox;
    render((pool || []).filter(l => String(l.campaign_id) === String(campId)));
    return;
  }

  // Fallback: fetch directly
  const url = source === 'sl'
    ? `/api/smartlead/positive-leads/${campId}`
    : `/api/aimfox/replied-leads/${campId}`;
  fetch(url).then(r => r.json()).then(data => {
    if (data.ok) render(data.leads || []);
    else document.getElementById('campLeadsBody').innerHTML =
      `<div class="tooltip-loading">Error: ${esc(data.error || 'unknown')}</div>`;
  }).catch(() => {
    document.getElementById('campLeadsBody').innerHTML =
      '<div class="tooltip-loading">Failed to load.</div>';
  });
}

function closeCampLeadsModal() {
  document.getElementById('campLeadsModal').classList.add('hidden');
}

/* ── Metric column ────────────────────────────────────────────────────────── */
function metricCol(cls, val, pctVal, icon, label) {
  const pctClass = pctVal == null ? '' : pctVal >= 30 ? 'good' : pctVal >= 10 ? 'mid' : 'low';
  return `
<div class="metric-col ${cls}">
  <span class="m-val">${fmt(val)}</span>
  ${pctVal != null ? `<span class="m-pct ${pctClass}">${pctVal}%</span>` : '<span class="m-pct neutral">—</span>'}
  <span class="m-label"><span class="m-icon">${icon}</span>${label}</span>
</div>`;
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */
function fmt(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return n.toLocaleString();
}

function pctSpan(a, b) {
  if (!b) return '';
  const p = Math.round(a / b * 100);
  const cls = p >= 30 ? 'good' : p >= 10 ? 'mid' : 'low';
  return `<span class="m-pct ${cls}" style="font-size:12px">${p}%</span>`;
}

function statusClass(s) {
  const m = {
    ACTIVE:'status-active', COMPLETED:'status-completed',
    STOPPED:'status-stopped', PAUSED:'status-paused',
    INIT:'status-init',
  };
  return m[(s||'').toUpperCase()] || 'status-unknown';
}

function setText(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function esc(s) {
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setSpinning(on) {
  document.getElementById('refreshBtn').classList.toggle('spin', on);
  document.getElementById('refreshBtn').disabled = on;
}

function clearError() { document.getElementById('globalError').textContent = ''; }
function showError(msg) { document.getElementById('globalError').textContent = '⚠ ' + msg; }

/* ── PDF Export ───────────────────────────────────────────────────────────── */
function downloadPDF() {
  // Expand all sub-bodies so they print
  const bodies = document.querySelectorAll('.subs-body');
  const wasHidden = [];
  bodies.forEach(b => {
    wasHidden.push(b.style.display === 'none');
    b.style.display = 'block';
  });

  window.print();

  // Restore collapse state after print dialog closes
  requestAnimationFrame(() => {
    bodies.forEach((b, i) => { if (wasHidden[i]) b.style.display = 'none'; });
  });
}
