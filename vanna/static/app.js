// ── State ─────────────────────────────────────────────
let sessionId = null;
let isOpen = false;
let isSidePanel = false;
let exchangeCount = 0;
const MAX_EXCHANGES = 20;
let currentAbort = null;
let isDashboardMode = false;
let dpmSessionId = null;

// ── Panel controls ────────────────────────────────────

function togglePanel() {
  if (isSidePanel) return;
  isOpen = !isOpen;
  document.getElementById('chat-panel').classList.toggle('open', isOpen);
  if (isOpen) document.getElementById('user-input').focus();
}

function toggleSidePanel() {
  isSidePanel = !isSidePanel;
  document.body.classList.toggle('side-panel', isSidePanel);
  document.getElementById('expand-icon').innerHTML = isSidePanel
    ? '<polyline points="1,6 1,1 6,1"/><line x1="1" y1="1" x2="6" y2="6"/><polyline points="12,7 12,12 7,12"/><line x1="12" y1="12" x2="7" y2="7"/>'
    : '<polyline points="8,1 12,1 12,5"/><line x1="12" y1="1" x2="7" y2="6"/><polyline points="5,12 1,12 1,8"/><line x1="1" y1="12" x2="6" y2="7"/>';
  document.getElementById('expand-btn').title = isSidePanel ? 'Back to popup' : 'Expand to side panel';
  document.getElementById('close-btn').style.display = isSidePanel ? 'none' : '';
  if (isSidePanel) {
    document.getElementById('chat-panel').classList.add('open');
    isOpen = true;
    document.getElementById('user-input').focus();
  }
}

// ── DOM helpers ───────────────────────────────────────

function scrollToBottom() {
  const m = document.getElementById('messages');
  m.scrollTop = m.scrollHeight;
}

function appendMessage(role, contentEl) {
  const bubble = document.createElement('div');
  bubble.className = `msg ${role}`;
  if (typeof contentEl === 'string') {
    bubble.textContent = contentEl;
  } else {
    bubble.appendChild(contentEl);
  }
  document.getElementById('messages').appendChild(bubble);
  scrollToBottom();
  return bubble;
}

function showTyping() {
  const bubble = document.createElement('div');
  bubble.className = 'msg assistant';
  bubble.id = 'typing-bubble';
  bubble.innerHTML = '<div class="typing-wrap"><span></span><span></span><span></span></div>';
  document.getElementById('messages').appendChild(bubble);
  scrollToBottom();
  return bubble;
}

// ── Markdown helpers ──────────────────────────────────

function stripCodeBlocks(text) {
  let out = text.replace(/```[\w]*\n[\s\S]*?```/g, '').replace(/```[\s\S]*?```/g, '');
  out = out.split('\n').filter(l => !l.trim().startsWith('|')).join('\n');
  return out.replace(/\n{3,}/g, '\n\n').trim();
}

function md(text) {
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const inline = raw => esc(raw)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,     '<em>$1</em>')
    .replace(/`([^`]+)`/g,     '<code style="background:#f0f0f5;padding:1px 4px;border-radius:3px;font-size:11px">$1</code>');

  const lines = text.split('\n');
  const out = [];
  let listType = null;
  let tableRows = [];

  const flushList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
  const flushTable = () => {
    if (!tableRows.length) return;
    out.push('<table style="border-collapse:collapse;width:100%;font-size:11.5px;margin:.4em 0">');
    let firstRow = true;
    tableRows.forEach(row => {
      const cells = row.split('|').slice(1, -1).map(c => c.trim());
      if (firstRow) {
        out.push('<thead><tr>' + cells.map(c =>
          `<th style="background:#f5f5fa;padding:4px 8px;text-align:left;border-bottom:1px solid #e0e0e8;font-weight:600;color:#555;white-space:nowrap">${inline(c)}</th>`
        ).join('') + '</tr></thead><tbody>');
        firstRow = false;
      } else {
        out.push('<tr>' + cells.map(c =>
          `<td style="padding:3px 8px;border-bottom:1px solid #f0f0f5;color:#333">${inline(c)}</td>`
        ).join('') + '</tr>');
      }
    });
    out.push('</tbody></table>');
    tableRows = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    const isTableRow = trimmed.startsWith('|') && trimmed.endsWith('|');
    const isSepRow  = /^\|[\s\-:|]+\|$/.test(trimmed);

    if (isSepRow)   { continue; }
    if (isTableRow) { flushList(); tableRows.push(trimmed); continue; }
    flushTable();

    const h3 = line.match(/^###\s+(.*)/);
    const h2 = line.match(/^##\s+(.*)/);
    const h1 = line.match(/^#\s+(.*)/);
    const ol = line.match(/^(\d+)\.\s+(.*)/);
    const ul = line.match(/^[-*]\s+(.*)/);

    if (h1)      { flushList(); out.push(`<div style="font-weight:700;font-size:14px;margin:.6em 0 .2em;color:#111">${inline(h1[1])}</div>`); }
    else if (h2) { flushList(); out.push(`<div style="font-weight:700;font-size:13px;margin:.5em 0 .2em;color:#222">${inline(h2[1])}</div>`); }
    else if (h3) { flushList(); out.push(`<div style="font-weight:600;font-size:12px;margin:.4em 0 .2em;color:#333">${inline(h3[1])}</div>`); }
    else if (ol) {
      if (listType !== 'ol') { flushList(); out.push('<ol style="margin:.4em 0 .4em 1.4em;padding:0">'); listType = 'ol'; }
      out.push(`<li>${inline(ol[2])}</li>`);
    } else if (ul) {
      if (listType !== 'ul') { flushList(); out.push('<ul style="margin:.4em 0 .4em 1.4em;padding:0">'); listType = 'ul'; }
      out.push(`<li>${inline(ul[1])}</li>`);
    } else {
      flushList();
      out.push(line === '' ? '<br>' : `<span>${inline(line)}</span><br>`);
    }
  }
  flushList();
  flushTable();
  return out.join('').replace(/^(<br>)+/, '').replace(/(<br>)+$/, '');
}

// ── Chart rendering ───────────────────────────────────

const CHART_COLORS = ['#7262ff','#ff6b6b','#ffd93d','#6bcb77','#4d96ff','#ff922b','#cc5de8'];

function renderChart(columns, data, chartSpec) {
  if (!chartSpec || !chartSpec.type || typeof Plotly === 'undefined') return null;
  const spec = chartSpec;

  const el = document.createElement('div');
  el.className = 'chart-wrap';

  const layout = {
    margin: { t: 10, r: 20, b: 50, l: 55 },
    height: 185,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    font: { size: 10, color: '#555' },
    xaxis: { tickfont: { size: 9 }, tickangle: -30 },
    yaxis: { tickfont: { size: 9 }, gridcolor: '#ebebf0' },
    showlegend: false,
  };
  const config = {
    responsive: true,
    displayModeBar: 'hover',
    modeBarButtons: [['resetScale2d']],
    scrollZoom: false,
    doubleClick: 'reset',
  };

  let traces = [];

  if (spec.type === 'big_number') {
    const val = data[0][spec.y] ?? Object.values(data[0])[0];
    const formatted = typeof val === 'number'
      ? val.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : val;
    const kpiEl = document.createElement('div');
    kpiEl.className = 'chart-wrap';
    kpiEl.style.cssText = 'display:flex;flex-direction:column;align-items:center;justify-content:center;padding:18px 12px;';
    kpiEl.innerHTML = `<div style="font-size:11px;color:#888;margin-bottom:4px">${spec.title || spec.y || columns[0]}</div><div style="font-size:22px;font-weight:700;color:#7262ff">${formatted}</div>`;
    return kpiEl;
  }

  if (spec.type === 'grouped_line') {
    const groups = [...new Set(data.map(r => r[spec.group]))].slice(0, 7);
    traces = groups.map((g, i) => {
      const rows = data.filter(r => r[spec.group] === g).sort((a, b) => String(a[spec.x]) > String(b[spec.x]) ? 1 : -1);
      return {
        x: rows.map(r => r[spec.x]),
        y: rows.map(r => r[spec.y]),
        type: 'scatter', mode: 'lines+markers',
        name: String(g),
        line: { color: CHART_COLORS[i % CHART_COLORS.length], width: 2 },
        marker: { color: CHART_COLORS[i % CHART_COLORS.length], size: 4 },
      };
    });
    layout.showlegend = true;
    layout.legend = { font: { size: 9 }, orientation: 'h', x: 0, y: 1.05, xanchor: 'left', yanchor: 'bottom' };
    layout.margin.t = 32;
  }

  if (spec.type === 'line') {
    const yCols = spec.y_cols || (spec.y ? [spec.y] : []);
    traces = yCols.map((yCol, i) => ({
      x: data.map(r => r[spec.x]),
      y: data.map(r => r[yCol]),
      type: 'scatter', mode: 'lines+markers',
      name: yCol,
      line: { color: CHART_COLORS[i], width: 2 },
      marker: { color: CHART_COLORS[i], size: 5 },
    }));
    if (traces.length > 1) { layout.showlegend = true; layout.legend = { font: { size: 9 }, orientation: 'h', x: 0, y: 1.05, xanchor: 'left', yanchor: 'bottom' }; layout.margin.t = 32; }
  }

  if (spec.type === 'area') {
    const yCols = spec.y_cols || (spec.y ? [spec.y] : []);
    traces = yCols.map((yCol, i) => ({
      x: data.map(r => r[spec.x]),
      y: data.map(r => r[yCol]),
      type: 'scatter', mode: 'lines',
      fill: i === 0 ? 'tozeroy' : 'tonexty',
      name: yCol,
      line: { color: CHART_COLORS[i], width: 2 },
      fillcolor: CHART_COLORS[i] + '30',
    }));
    if (traces.length > 1) { layout.showlegend = true; layout.legend = { font: { size: 9 }, orientation: 'h', x: 0, y: 1.05, xanchor: 'left', yanchor: 'bottom' }; layout.margin.t = 32; }
  }

  if (spec.type === 'grouped_bar') {
    const groups = [...new Set(data.map(r => r[spec.group]))].slice(0, 5);
    traces = groups.map((g, i) => {
      const rows = data.filter(r => r[spec.group] === g);
      return {
        x: rows.map(r => r[spec.x]),
        y: rows.map(r => r[spec.y]),
        name: g, type: 'bar',
        marker: { color: CHART_COLORS[i % CHART_COLORS.length] },
      };
    });
    layout.barmode = 'group';
    layout.showlegend = true;
    layout.legend = { font: { size: 9 }, orientation: 'h', x: 0, y: 1.05, xanchor: 'left', yanchor: 'bottom' };
    layout.margin.t = 32;
  }

  if (spec.type === 'bar') {
    traces = [{
      x: data.map(r => r[spec.x]),
      y: data.map(r => r[spec.y]),
      type: 'bar',
      marker: { color: CHART_COLORS[0] },
    }];
  }

  if (spec.type === 'scatter') {
    traces = [{
      x: data.map(r => r[spec.x]),
      y: data.map(r => r[spec.y]),
      type: 'scatter', mode: 'markers',
      marker: { color: CHART_COLORS[0], size: 6, opacity: 0.75 },
    }];
    layout.xaxis.title = { text: spec.x, font: { size: 10 } };
    layout.yaxis.title = { text: spec.y, font: { size: 10 } };
  }

  if (spec.type === 'pie') {
    traces = [{
      labels: data.map(r => r[spec.x]),
      values: data.map(r => r[spec.y]),
      type: 'pie',
      marker: { colors: CHART_COLORS },
      textinfo: 'label+percent',
      textfont: { size: 9 },
      hole: 0.3,
    }];
    layout.margin = { t: 10, r: 10, b: 10, l: 10 };
  }

  if (spec.type === 'heatmap') {
    const xVals = [...new Set(data.map(r => r[spec.x]))];
    const yVals = [...new Set(data.map(r => r[spec.group]))];
    const z = yVals.map(yv => xVals.map(xv => {
      const row = data.find(r => r[spec.x] === xv && r[spec.group] === yv);
      return row ? row[spec.y] : null;
    }));
    traces = [{ type: 'heatmap', x: xVals, y: yVals, z, colorscale: 'Purples' }];
    layout.margin.b = 70;
  }

  Plotly.newPlot(el, traces, layout, config);
  return el;
}

// ── Result renderer ───────────────────────────────────

function renderResult(result) {
  const wrap = document.createElement('div');

  // Chart (explore only)
  if (result.data && result.data.length >= 1 && result.columns && result.chart_spec) {
    const chartEl = renderChart(result.columns, result.data, result.chart_spec);
    if (chartEl) wrap.appendChild(chartEl);
  }

  // Narrative text
  const textEl = document.createElement('div');
  textEl.innerHTML = md(stripCodeBlocks(result.text));
  wrap.appendChild(textEl);

  // SQL collapsible
  if (result.sql) {
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'sql-toggle';
    toggleBtn.textContent = '▶ view sql';
    const codeEl = document.createElement('pre');
    codeEl.className = 'sql-code';
    codeEl.textContent = result.sql;
    toggleBtn.onclick = () => {
      const open = codeEl.style.display === 'block';
      codeEl.style.display = open ? 'none' : 'block';
      toggleBtn.textContent = open ? '▶ view sql' : '▼ hide sql';
    };
    wrap.appendChild(toggleBtn);
    wrap.appendChild(codeEl);
  }

  // Data table
  if (result.data && result.data.length > 0 && result.columns) {
    const tableWrap = document.createElement('div');
    tableWrap.className = 'data-table-wrap';

    const table = document.createElement('table');
    table.className = 'data-table';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    result.columns.forEach(col => {
      const th = document.createElement('th');
      th.textContent = col;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    result.data.forEach(row => {
      const tr = document.createElement('tr');
      result.columns.forEach(col => {
        const td = document.createElement('td');
        const val = row[col];
        if (val === null || val === undefined) {
          td.textContent = '—';
        } else if (typeof val === 'number') {
          td.textContent = Number.isInteger(val) ? val.toLocaleString()
            : val.toLocaleString(undefined, { maximumFractionDigits: 2 });
        } else if (typeof val === 'string' && /^\w{3}, \d{2} \w{3} \d{4} 00:00:00 GMT$/.test(val)) {
          // Date-only string like "Sun, 01 Feb 2026 00:00:00 GMT" → "2026-02-01"
          const d = new Date(val);
          td.textContent = d.toISOString().slice(0, 10);
        } else {
          td.textContent = val;
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    wrap.appendChild(tableWrap);

    const countEl = document.createElement('div');
    countEl.className = 'row-count';
    countEl.textContent = `${result.row_count} row${result.row_count !== 1 ? 's' : ''}`;
    wrap.appendChild(countEl);

    const csvBtn = document.createElement('button');
    csvBtn.className = 'csv-btn';
    csvBtn.textContent = '⬇ Export CSV';
    csvBtn.onclick = () => exportCSV(result.columns, result.data);
    wrap.appendChild(csvBtn);
  }

  // Feedback buttons
  if (result.intent === 'explore' && result.sql) {
    const fbWrap = document.createElement('div');
    fbWrap.className = 'feedback-wrap';

    const label = document.createElement('span');
    label.className = 'feedback-label';
    label.textContent = 'Was this helpful?';

    const upBtn = document.createElement('button');
    upBtn.className = 'feedback-btn';
    upBtn.textContent = '👍';
    upBtn.title = 'Good answer — save as training example';

    const downBtn = document.createElement('button');
    downBtn.className = 'feedback-btn';
    downBtn.textContent = '👎';
    downBtn.title = 'Wrong answer — flag for review';

    const userBubbles = document.querySelectorAll('.msg.user');
    const question = userBubbles.length ? userBubbles[userBubbles.length - 1].textContent : '';

    upBtn.onclick   = () => sendFeedback(question, result.sql, 'up',   fbWrap);
    downBtn.onclick = () => sendFeedback(question, result.sql, 'down', fbWrap);

    fbWrap.appendChild(label);
    fbWrap.appendChild(upBtn);
    fbWrap.appendChild(downBtn);
    wrap.appendChild(fbWrap);
  }

  return wrap;
}

// ── Dashboard builder ─────────────────────────────────

async function startDashboard(btn) {
  btn.disabled = true;
  btn.textContent = 'Starting…';
  try {
    const resp = await fetch('dashboard/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    dpmSessionId = data.dpm_session_id;
    isDashboardMode = true;
    document.getElementById('user-input').placeholder = 'Answer to build your dashboard PRD…';
    btn.style.display = 'none';
    appendDPMMessage(data.message);
    if (data.status === 'complete' && data.prd) { showPRD(data.prd); exitDashboardMode(); }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Save as Dashboard';
    appendMessage('assistant', 'Could not start dashboard builder. Please try again.');
  }
}

async function sendDashboardMessage(message) {
  const resp = await fetch('dashboard/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dpm_session_id: dpmSessionId, message }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  appendDPMMessage(data.message);
  if (data.status === 'complete' && data.prd) { showPRD(data.prd); exitDashboardMode(); }
}

function appendDPMMessage(text) {
  const bubble = document.createElement('div');
  bubble.className = 'msg assistant';
  bubble.innerHTML = `<div style="font-size:10px;color:#7262ff;font-weight:600;margin-bottom:4px">&#10022; Dashboard Builder</div>${md(text)}`;
  document.getElementById('messages').appendChild(bubble);
  scrollToBottom();
}

function showPRD(prd) {
  const capturedSessionId = dpmSessionId;  // capture before exitDashboardMode() nulls it
  const card = document.createElement('div');
  card.className = 'prd-card';
  card.innerHTML =
    `<div class="prd-title">Dashboard PRD: ${prd.title}</div>` +
    `<div class="prd-section"><span class="prd-label">Problem Statement</span><p>${prd.problem_statement}</p></div>` +
    `<div class="prd-section"><span class="prd-label">Objective</span><p>${prd.objective}</p></div>` +
    `<div class="prd-section"><span class="prd-label">Audience</span><p>${prd.audience}</p></div>` +
    `<div class="prd-section"><span class="prd-label">Key Metrics</span><ul>${prd.metrics.map(m => `<li>${m}</li>`).join('')}</ul></div>` +
    `<div class="prd-section"><span class="prd-label">Actions</span><ul>${prd.action_items.map(a => `<li>${a}</li>`).join('')}</ul></div>` +
    `<button class="build-dashboard-btn">Build Dashboard</button>`;
  const buildBtn = card.querySelector('.build-dashboard-btn');
  buildBtn.onclick = () => buildDashboard(buildBtn, capturedSessionId);
  document.getElementById('messages').appendChild(card);
  scrollToBottom();
}

async function buildDashboard(btn, sessionId) {
  btn.disabled = true;
  btn.textContent = 'Finding model…';
  try {
    const resp = await fetch('dashboard/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dpm_session_id: sessionId }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.error) {
      btn.textContent = `Error: ${data.error}`;
      return;
    }
    if (data.needs_new_model) {
      btn.textContent = 'New dbt model needed (coming soon)';
      return;
    }

    const infoEl = btn.parentElement.querySelector('.model-info') || document.createElement('div');
    infoEl.className = 'model-info';
    let html = `<span class="prd-label">Model</span><p><code>${data.db_schema}.${data.model_name}</code></p>`;

    // Housekeeper: advisory suggestions (never blocks)
    if (data.housekeeper) {
      const icon = data.housekeeper === 'full' ? '⚠' : '💡';
      html += `<p style="color:#e67e00;margin-top:4px">${icon} ${data.suggestion}`;
      if (data.existing_url) {
        html += ` <a href="${data.existing_url}" target="_blank" class="dashboard-link">View: ${data.existing_name} &rarr;</a>`;
      }
      html += `</p>`;
    }

    html += data.url ? `<p style="margin-top:8px"><a href="${data.url}" target="_blank" class="dashboard-link">Open Dashboard &rarr;</a></p>` : '';

    // Instructor guide
    if (data.guide && data.guide.overview) {
      const g = data.guide;
      const useCases = (g.use_cases || []).map(u => `<li>${u}</li>`).join('');
      const tips = (g.tips || []).map(t => `<li>${t}</li>`).join('');
      html += `
        <details style="margin-top:10px">
          <summary style="cursor:pointer;font-weight:600;color:#a78bfa">Dashboard Guide</summary>
          <div style="margin-top:6px;font-size:12px;line-height:1.6">
            <p>${g.overview}</p>
            ${useCases ? `<p style="font-weight:600;margin-top:6px">Questions this answers:</p><ul style="margin:4px 0 0 16px">${useCases}</ul>` : ''}
            ${tips ? `<p style="font-weight:600;margin-top:6px">Tips:</p><ul style="margin:4px 0 0 16px">${tips}</ul>` : ''}
          </div>
        </details>`;
    }

    infoEl.innerHTML = html;
    btn.insertAdjacentElement('beforebegin', infoEl);
    btn.style.display = 'none';
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Build Dashboard';
    appendMessage('assistant', 'Build failed. Please try again.');
  }
}

function exitDashboardMode() {
  isDashboardMode = false;
  dpmSessionId = null;
  document.getElementById('user-input').placeholder = 'Ask a question…';
}

// ── Feedback ──────────────────────────────────────────

async function sendFeedback(question, sql, rating, wrap) {
  const btns = wrap.querySelectorAll('.feedback-btn');
  btns.forEach(b => b.disabled = true);
  try {
    await fetch('feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, sql, rating }),
    });
    const label = wrap.querySelector('.feedback-label');
    label.textContent = rating === 'up' ? 'Thanks! Saved as a good example.' : 'Noted. We\'ll review it.';
    label.style.color = rating === 'up' ? '#6bcb77' : '#e67e00';
  } catch (e) {
    console.error('Feedback error', e);
  }
}

// ── CSV export ────────────────────────────────────────

function exportCSV(columns, data) {
  const escape = v => {
    const s = (v === null || v === undefined) ? '' : String(v);
    return s.includes(',') || s.includes('"') || s.includes('\n')
      ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = [columns.map(escape).join(',')];
  data.forEach(row => rows.push(columns.map(col => escape(row[col])).join(',')));
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'export.csv'; a.click();
  URL.revokeObjectURL(url);
}

// ── Session banner ────────────────────────────────────

function showSessionBanner(msg, color) {
  const banner = document.createElement('div');
  banner.style.cssText = `font-size:11px;color:${color};background:${color}18;border:1px solid ${color}55;border-radius:6px;padding:5px 8px;text-align:center;margin:2px 0`;
  banner.textContent = msg;
  document.getElementById('messages').appendChild(banner);
  scrollToBottom();
}

// ── Send message ──────────────────────────────────────

async function sendMessage() {
  const input = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  const question = input.value.trim();
  if (!question || exchangeCount >= MAX_EXCHANGES) return;

  if (isDashboardMode) {
    input.value = '';
    appendMessage('user', question);
    input.disabled = true;
    sendBtn.disabled = true;
    document.getElementById('send-btn').style.display = 'none';
    document.getElementById('stop-btn').style.display = 'block';
    try {
      await sendDashboardMessage(question);
    } catch (e) {
      appendMessage('assistant', 'Dashboard builder error. Please try again.');
    } finally {
      document.getElementById('stop-btn').style.display = 'none';
      document.getElementById('send-btn').style.display = '';
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
    return;
  }

  input.value = '';
  input.disabled = true;
  sendBtn.disabled = true;
  document.getElementById('send-btn').style.display = 'none';
  document.getElementById('stop-btn').style.display = 'block';

  appendMessage('user', question);
  const typingBubble = showTyping();

  currentAbort = new AbortController();
  let streamingText = '';
  let streamingTextEl = null;
  let atLimit = false;

  try {
    const resp = await fetch('chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: question, session_id: sessionId }),
      signal: currentAbort.signal,
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === 'text') {
          // First text chunk: replace typing dots with streaming text
          if (!streamingTextEl) {
            typingBubble.innerHTML = '';
            streamingTextEl = document.createElement('div');
            typingBubble.appendChild(streamingTextEl);
          }
          streamingText += event.content;
          streamingTextEl.innerHTML = md(streamingText);
          scrollToBottom();

        } else if (event.type === 'result') {
          typingBubble.remove();
          sessionId = event.session_id;
          const bubble = appendMessage('assistant', renderResult(event));
          requestAnimationFrame(() => {
            bubble.querySelectorAll('.chart-wrap').forEach(el => Plotly.Plots.resize(el));
          });

          const saveBar = document.getElementById('save-bar');
          const saveDashBtn = document.getElementById('save-dashboard-btn');
          if (!isDashboardMode && event.intent === 'explore' && event.data && event.data.length > 0) {
            saveBar.classList.add('visible');
            saveDashBtn.disabled = false;
            saveDashBtn.textContent = 'Save as Dashboard';
          }

          exchangeCount++;
          if (exchangeCount >= MAX_EXCHANGES) {
            showSessionBanner('Session limit reached. Please refresh the page to start a new session.', '#cc3333');
            atLimit = true;
          } else if (exchangeCount === MAX_EXCHANGES - 1) {
            showSessionBanner('⚠ 1 message remaining in this session. Refresh to start a new session.', '#e67e00');
          }

        } else if (event.type === 'error') {
          typingBubble.remove();
          appendMessage('assistant', `Something went wrong: ${event.message}`);
        }
      }
    }
  } catch (err) {
    typingBubble.remove();
    if (err.name === 'AbortError') {
      input.value = question;
    } else {
      appendMessage('assistant', 'Something went wrong. Please try again.');
      console.error(err);
    }
  } finally {
    currentAbort = null;
    document.getElementById('stop-btn').style.display = 'none';
    document.getElementById('send-btn').style.display = '';
  }

  if (atLimit) {
    input.disabled = true;
    sendBtn.disabled = true;
  } else {
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

function stopRequest() {
  if (currentAbort) currentAbort.abort();
}
