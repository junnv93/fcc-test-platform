const CONTRACT_ROUTES = Object.freeze({
  health: '/health',
  contract: '/headless/api-contract',
  capabilities: '/headless/capabilities',
  jobs: '/headless/jobs',
  sessionResults: '/headless/sessions/{session_id}/results',
  sessionArtifacts: '/headless/sessions/{session_id}/artifacts',
  reportRequest: '/headless/reports/{request_id}',
  reportOutputs: '/headless/reports/{request_id}/outputs'
});

const state = {
  baseUrl: 'http://127.0.0.1:8000',
  currentView: 'overview',
  bearerToken: '',
  oidcConfig: null
};

const el = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', async () => {
  state.bearerToken = sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
  el('bearer-token').value = state.bearerToken;
  wireNavigation();
  wireActions();
  await initializeOidc();
  connectProvider();
});

function wireNavigation() {
  document.querySelectorAll('.nav-tab').forEach((button) => {
    button.addEventListener('click', () => {
      state.currentView = button.dataset.view;
      document.querySelectorAll('.nav-tab').forEach((item) => item.classList.remove('active'));
      document.querySelectorAll('.view').forEach((view) => view.classList.remove('active'));
      button.classList.add('active');
      el(state.currentView).classList.add('active');
    });
  });
}

function wireActions() {
  el('provider-form').addEventListener('submit', (event) => {
    event.preventDefault();
    state.baseUrl = normalizeBaseUrl(el('provider-base-url').value);
    connectProvider();
  });
  el('auth-form').addEventListener('submit', (event) => {
    event.preventDefault();
    state.bearerToken = el('bearer-token').value.trim();
    if (state.bearerToken) {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, state.bearerToken);
    } else {
      sessionStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    }
    connectProvider();
  });
  el('oidc-sign-in').addEventListener('click', async () => {
    if (state.oidcConfig) {
      await window.platformAuth.startOidcLogin(state.oidcConfig);
    }
  });
  el('oidc-sign-out').addEventListener('click', () => {
    window.platformAuth.signOut();
    state.bearerToken = '';
    el('bearer-token').value = '';
    setStatus('Signed out', 'muted');
  });
  el('refresh-current').addEventListener('click', refreshCurrentView);
  el('load-capabilities').addEventListener('click', loadCapabilities);
  el('load-jobs').addEventListener('click', loadJobs);
  el('load-session-results').addEventListener('click', () => loadSessionTable('results'));
  el('load-session-artifacts').addEventListener('click', () => loadSessionTable('artifacts'));
  el('load-report-request').addEventListener('click', () => loadReport('request'));
  el('load-report-outputs').addEventListener('click', () => loadReport('outputs'));
}

async function initializeOidc() {
  if (!window.platformAuth) {
    return;
  }
  try {
    state.oidcConfig = await window.platformAuth.loadOidcConfig();
    el('oidc-sign-in').disabled = !state.oidcConfig;
    if (!state.oidcConfig) {
      return;
    }
    const completed = await window.platformAuth.completeOidcRedirect(state.oidcConfig);
    if (completed) {
      state.bearerToken = sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
      el('bearer-token').value = state.bearerToken;
    }
  } catch (error) {
    setStatus('Sign in failed', 'error');
    el('capabilities-output').textContent = JSON.stringify({error: String(error.message || error)}, null, 2);
  }
}

async function connectProvider() {
  setStatus('Connecting', 'muted');
  try {
    const [health, contract] = await Promise.all([
      apiGet(CONTRACT_ROUTES.health),
      apiGet(CONTRACT_ROUTES.contract)
    ]);
    el('health-value').textContent = health.status || 'ok';
    el('provider-title').textContent = contract.provider?.provider_id || 'Provider connected';
    el('provider-value').textContent = contract.provider?.provider_id || '-';
    el('product-line-value').textContent = contract.provider?.product_line || '-';
    el('contract-value').textContent = contract.version || '-';
    setStatus('Connected', '');
    await loadCapabilities();
  } catch (error) {
    setStatus('Connection failed', 'error');
    el('provider-title').textContent = 'Not connected';
    el('capabilities-output').textContent = JSON.stringify({error: String(error.message || error)}, null, 2);
  }
}

async function refreshCurrentView() {
  if (state.currentView === 'overview') {
    await connectProvider();
  } else if (state.currentView === 'jobs') {
    await loadJobs();
  } else if (state.currentView === 'session') {
    await loadSessionTable('results');
  } else if (state.currentView === 'reports') {
    await loadReport('request');
  }
}

async function loadCapabilities() {
  const capabilities = await apiGet(CONTRACT_ROUTES.capabilities);
  el('capabilities-output').textContent = JSON.stringify(capabilities, null, 2);
}

async function loadJobs() {
  const status = el('job-status-filter').value.trim();
  const route = status
    ? `${CONTRACT_ROUTES.jobs}?status=${encodeURIComponent(status)}`
    : CONTRACT_ROUTES.jobs;
  const jobs = await apiGet(route);
  renderTable(el('jobs-table'), jobs, ['id', 'status', 'excel_path', 'session_id', 'requested_by', 'created_at']);
}

async function loadSessionTable(kind) {
  const sessionId = requiredNumber('session-id-input', 'session id');
  const route = kind === 'artifacts'
    ? CONTRACT_ROUTES.sessionArtifacts.replace('{session_id}', sessionId)
    : CONTRACT_ROUTES.sessionResults.replace('{session_id}', sessionId);
  const rows = await apiGet(route);
  const columns = kind === 'artifacts'
    ? ['artifact_type', 'relative_path', 'original_filename', 'byte_size', 'storage_backend', 'artifact_resolution_status', 'exists']
    : ['result_id', 'technology', 'test_name', 'verdict', 'condition', 'result'];
  renderTable(el('session-table'), rows, columns);
}

async function loadReport(kind) {
  const requestId = requiredNumber('report-request-id-input', 'request id');
  const route = kind === 'outputs'
    ? CONTRACT_ROUTES.reportOutputs.replace('{request_id}', requestId)
    : CONTRACT_ROUTES.reportRequest.replace('{request_id}', requestId);
  const payload = await apiGet(route);
  el('report-output').textContent = JSON.stringify(payload, null, 2);
  if (kind === 'outputs') {
    renderReportOutputs(payload);
  } else {
    el('report-output-summary').textContent = '';
    el('report-output-table').innerHTML = '';
  }
}

async function apiGet(path) {
  const response = await fetch(`${state.baseUrl}${path}`, {
    method: 'GET',
    headers: apiHeaders()
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function apiHeaders() {
  const headers = {'Accept': 'application/json'};
  if (state.bearerToken) {
    headers.Authorization = `Bearer ${state.bearerToken}`;
  }
  return headers;
}

function renderTable(container, rows, columns) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) {
    container.innerHTML = '<div class="json-output">No rows.</div>';
    return;
  }
  const header = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('');
  const body = data.map((row) => {
    const rowClass = rowStatusClass(row);
    const cells = columns.map((column) => `<td>${formatTableCell(row[column], column)}</td>`).join('');
    return `<tr class="${rowClass}">${cells}</tr>`;
  }).join('');
  container.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderReportOutputs(payload) {
  const rows = Array.isArray(payload) ? payload : [];
  const missing = rows.filter((row) => row.exists === false);
  const summary = rows.length
    ? `${rows.length} outputs, ${missing.length} missing`
    : 'No report outputs.';
  el('report-output-summary').textContent = summary;
  renderTable(
    el('report-output-table'),
    rows,
    ['file_name', 'relative_path', 'exists', 'byte_size', 'storage_backend']
  );
}

function requiredNumber(id, label) {
  const value = el(id).value.trim();
  if (!value) {
    throw new Error(`${label} is required`);
  }
  return encodeURIComponent(value);
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '') || 'http://127.0.0.1:8000';
}

function setStatus(text, mode) {
  const chip = el('connection-status');
  chip.textContent = text;
  chip.classList.toggle('muted', mode === 'muted');
  chip.classList.toggle('error', mode === 'error');
}

function formatCell(value) {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatTableCell(value, column) {
  if (column === 'exists') {
    return value === false
      ? '<span class="status-pill missing">Missing</span>'
      : '<span class="status-pill available">Available</span>';
  }
  if (column === 'artifact_resolution_status' && value) {
    const mode = value === 'missing' ? 'missing' : 'available';
    return `<span class="status-pill ${mode}">${escapeHtml(formatCell(value))}</span>`;
  }
  return escapeHtml(formatCell(value));
}

function rowStatusClass(row) {
  if (row.exists === false || row.artifact_resolution_status === 'missing') {
    return 'row-missing';
  }
  return '';
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
