const OIDC_CONFIG_PATH = './oidc-config.json';
const OIDC_SESSION_PREFIX = 'fcc-platform-shell-oidc';
const AUTH_TOKEN_STORAGE_KEY = 'fcc-platform-shell-bearer-token';

async function loadOidcConfig() {
  try {
    const response = await fetch(OIDC_CONFIG_PATH, {headers: {'Accept': 'application/json'}});
    if (!response.ok) {
      return null;
    }
    const config = await response.json();
    return isOidcConfigUsable(config) ? config : null;
  } catch (_error) {
    return null;
  }
}

function isOidcConfigUsable(config) {
  return Boolean(
    config
    && config.enabled === true
    && config.issuer
    && config.client_id
    && config.authorization_endpoint
    && config.token_endpoint
    && config.redirect_uri
    && Array.isArray(config.scopes)
    && !config.client_secret
  );
}

async function completeOidcRedirect(config) {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const state = params.get('state');
  if (!code && !params.get('error')) {
    return false;
  }
  const expectedState = sessionStorage.getItem(`${OIDC_SESSION_PREFIX}:state`) || '';
  const verifier = sessionStorage.getItem(`${OIDC_SESSION_PREFIX}:verifier`) || '';
  if (!code || !state || state !== expectedState || !verifier) {
    throw new Error('OIDC redirect state is invalid');
  }
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.client_id,
    code,
    redirect_uri: config.redirect_uri,
    code_verifier: verifier
  });
  const response = await fetch(config.token_endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
    body
  });
  if (!response.ok) {
    throw new Error(`OIDC token exchange failed: ${response.status}`);
  }
  const token = await response.json();
  if (!token.access_token) {
    throw new Error('OIDC token response did not include access_token');
  }
  sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token.access_token);
  clearOidcTransaction();
  window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  return true;
}

async function startOidcLogin(config) {
  const verifier = base64Url(randomBytes(32));
  const challenge = await sha256Base64Url(verifier);
  const state = base64Url(randomBytes(24));
  sessionStorage.setItem(`${OIDC_SESSION_PREFIX}:state`, state);
  sessionStorage.setItem(`${OIDC_SESSION_PREFIX}:verifier`, verifier);
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.client_id,
    redirect_uri: config.redirect_uri,
    scope: config.scopes.join(' '),
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256'
  });
  window.location.assign(`${config.authorization_endpoint}?${params.toString()}`);
}

function signOut() {
  sessionStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  clearOidcTransaction();
}

function clearOidcTransaction() {
  sessionStorage.removeItem(`${OIDC_SESSION_PREFIX}:state`);
  sessionStorage.removeItem(`${OIDC_SESSION_PREFIX}:verifier`);
}

function randomBytes(length) {
  const bytes = new Uint8Array(length);
  window.crypto.getRandomValues(bytes);
  return bytes;
}

async function sha256Base64Url(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await window.crypto.subtle.digest('SHA-256', bytes);
  return base64Url(new Uint8Array(digest));
}

function base64Url(bytes) {
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join('');
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

window.platformAuth = Object.freeze({
  loadOidcConfig,
  completeOidcRedirect,
  startOidcLogin,
  signOut
});
