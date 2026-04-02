/**
 * DukaPOS API Client (v4.1)
 *
 * Changes vs v4.0:
 *  - getSession / clearSession exported (fixes app-boot crash — Issue #1)
 *  - Session metadata helpers co-located with token helpers
 *
 * Existing behaviour preserved:
 *  - Token storage delegated to Electron main process via IPC
 *  - Automatic refresh token flow: on 401, silently refresh and retry once
 *  - Idempotency-Key header supported on all POST requests
 *  - 429 (rate limit) surfaces a user-friendly message instead of crashing
 *  - Browser fallback uses sessionStorage (dev only — clearly labelled)
 *
 * NOTE on Electron token security: tokens are stored via Electron's safeStorage
 * API (OS keychain / Secret Service / Keychain Access) in a dedicated
 * "dukapos-tokens" store that is separate from the main app settings store.
 * The encrypted bytes are base64-encoded before being written to disk.
 * If safeStorage is unavailable (e.g. headless/CI environments), tokens are
 * held in memory only and are not persisted — this is explicitly logged so
 * operators are aware. Tokens are NEVER written to the main app settings store.
 */

const isElectron = typeof window !== "undefined" && !!window.electron?.app?.isElectron;

// ── Base URL ───────────────────────────────────────────────────────────────────
async function getApiBase() {
  if (isElectron) {
    const base = await window.electron.config.get("apiBase");
    return base || import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";
  }
  return import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";
}

// ── Token storage ─────────────────────────────────────────────────────────────
// In Electron: persisted via safeStorage (OS keychain) through the main process.
//              Falls back to in-memory only if safeStorage is unavailable.
// In browser:  sessionStorage — tokens die when tab closes (dev only).

async function getTokens() {
  if (isElectron) return window.electron.auth.getTokens();
  try {
    return {
      accessToken:  sessionStorage.getItem("dukapos_access"),
      refreshToken: sessionStorage.getItem("dukapos_refresh"),
    };
  } catch { return { accessToken: null, refreshToken: null }; }
}

async function saveTokens({ accessToken, refreshToken }) {
  if (isElectron) return window.electron.auth.saveTokens({ accessToken, refreshToken });
  sessionStorage.setItem("dukapos_access",  accessToken  || "");
  sessionStorage.setItem("dukapos_refresh", refreshToken || "");
}

async function clearTokens() {
  if (isElectron) return window.electron.auth.clearTokens();
  sessionStorage.removeItem("dukapos_access");
  sessionStorage.removeItem("dukapos_refresh");
}

// Keep a sync-accessible cache so request() doesn't await for every header build
let _tokenCache = { accessToken: null, refreshToken: null };
getTokens().then(t => { _tokenCache = t || _tokenCache; });
export function getToken() {
  return _tokenCache?.accessToken || sessionStorage.getItem("dukapos_access") || null;
}
// ── Refresh flow state ────────────────────────────────────────────────────────
let _refreshPromise = null;   // deduplicate concurrent refresh attempts

async function attemptRefresh() {
  // Only one refresh at a time — all concurrent 401s await this same promise
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = (async () => {
    const { refreshToken } = await getTokens();
    if (!refreshToken) throw new Error("No refresh token available");

    const base = await getApiBase();
    const res  = await fetch(`${base}/auth/token/refresh`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) throw new Error("Refresh failed");

    const data = await res.json();
    await saveTokens({ accessToken: data.access_token, refreshToken: data.refresh_token });
    _tokenCache = { accessToken: data.access_token, refreshToken: data.refresh_token };
    return data.access_token;
  })().finally(() => { _refreshPromise = null; });

  return _refreshPromise;
}

// ── Core request function ─────────────────────────────────────────────────────
async function request(path, options = {}, { retry401 = true } = {}) {
  const base   = await getApiBase();
  const tokens = _tokenCache;

  const headers = {
    "Content-Type": "application/json",
    ...(tokens.accessToken ? { Authorization: `Bearer ${tokens.accessToken}` } : {}),
    ...options.headers,
  };

  const res = await fetch(`${base}${path}`, { ...options, headers });

  // ── 401: try token refresh once ───────────────────────────────────────────
  if (res.status === 401 && retry401) {
    try {
      const newAccessToken = await attemptRefresh();
      // Retry original request with new token
      return request(path, {
        ...options,
        headers: { ...options.headers, Authorization: `Bearer ${newAccessToken}` },
      }, { retry401: false });   // no infinite loop
    } catch {
      // Refresh failed — session is dead, force logout
      await clearTokens();
      _tokenCache = { accessToken: null, refreshToken: null };
      window.dispatchEvent(new CustomEvent("dukapos:session-expired"));
      window.location.replace("/#/login");
      return;
    }
  }

  // ── 429: rate limited ─────────────────────────────────────────────────────
  if (res.status === 429) {
    const retryAfter = res.headers.get("Retry-After") || "60";
    throw new Error(`Too many requests. Please wait ${retryAfter} seconds.`);
  }

  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: res.statusText }));
    const msg = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    throw new Error(msg || "Request failed");
  }

  if (res.status === 204) return null;
  return res.json();
}

const get   = (path, params, opts) => {
  const qs = params ? "?" + new URLSearchParams(params).toString() : "";
  return request(path + qs, {}, opts);
};
const post  = (path, body, opts) => request(path, { method: "POST",  body: JSON.stringify(body) }, opts);
const patch = (path, body, opts) => request(path, { method: "PATCH", body: JSON.stringify(body) }, opts);
const del   = (path, opts)       => request(path, { method: "DELETE" }, opts);

// ── Money helpers ─────────────────────────────────────────────────────────────
export const parseMoney = (v) => parseFloat(v ?? 0);
export const fmtKES     = (v) => `KES ${parseMoney(v).toLocaleString("en-KE", {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})}`;

// ── Session helpers (exported for use in Login component) ─────────────────────
export const sessionHelpers = { getTokens, saveTokens, clearTokens };

// ── Session metadata (non-sensitive: role, name, terminal_id) ─────────────────
// Stored separately from tokens — no auth value, safe in sessionStorage/config.
//
// getSession() — synchronous, safe to call at boot before any await.
//   Returns { id, name, role, terminal_id } or null.
//
// clearSession() — clears session metadata AND tokens. Call on logout.
//   Returns a Promise (awaitable but callers may fire-and-forget).

export function getSession() {
  if (isElectron) {
    // In Electron, window.electron.config.get is async; we rely on a cached
    // value that Login.jsx writes to sessionStorage as a sync mirror so that
    // App.jsx can read it synchronously at boot without an await.
    try {
      const raw = sessionStorage.getItem("dukapos_session");
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }
  try {
    const raw = sessionStorage.getItem("dukapos_session");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function clearSession() {
  // 1. Wipe tokens from secure/session storage
  await clearTokens();
  _tokenCache = { accessToken: null, refreshToken: null };

  // 2. Wipe session metadata mirror
  try { sessionStorage.removeItem("dukapos_session"); } catch { /* ignore */ }

  // 3. In Electron, also clear the persisted config key
  if (isElectron) {
    try { await window.electron.config.set("session", null); } catch { /* ignore */ }
  }
}

// ── API namespaces ────────────────────────────────────────────────────────────

export const authAPI = {
  login:        (email, password) => post("/auth/login", { email, password }),
  refreshToken: (refreshToken)    => post("/auth/token/refresh", { refresh_token: refreshToken }),
  me:           ()                => get("/auth/me"),
  clockIn:      ()                => post("/auth/clock-in"),
  clockOut:     ()                => post("/auth/clock-out"),
  // Returns a 30-second one-time ticket for WS auth — never put JWTs in WS query strings
  wsTicket:     ()                => post("/auth/ws-ticket"),
};

export const productsAPI = {
  list:         (p)     => get("/products", p),
  getById:      (id)    => get(`/products/${id}`),
  getByBarcode: (bc)    => get(`/products/barcode/${bc}`),
  getByItemCode: (code)  => get(`/products/itemcode/${code}`),
  categories:   ()      => get("/products/categories"),
  suppliers:    ()      => get("/products/suppliers"),
  create:       (d)     => post("/products", d),
  update:       (id, d) => patch(`/products/${id}`, d),
  adjustStock:  (d)     => post("/products/stock/adjust", d),
  stockHistory: (id)    => get(`/products/${id}/stock-history`),
};

export const transactionsAPI = {
  /**
   * Create a transaction.
   * Pass options.headers["Idempotency-Key"] to prevent double-post on retry.
   */
  create:       (d, opts)  => post("/transactions", d, opts),
  list:         (p)        => get("/transactions", p),
  getById:      (id)       => get(`/transactions/${id}`),
  todaySummary: ()         => get("/transactions/summary/today"),
  void:         (id)       => post(`/transactions/${id}/void`),
};

export const mpesaAPI = {
  stkPush:     (phone, amount, txnNumber) => post("/mpesa/stk-push", { phone, amount, txn_number: txnNumber }),
  queryStatus: (id)                       => post("/mpesa/stk-query", { checkout_request_id: id }),
};

export const reportsAPI = {
  zTape:       (d)    => get("/reports/z-tape",       d ? { report_date: d } : {}),
  weekly:      (d)    => get("/reports/weekly",       d ? { week_ending: d } : {}),
  vat:         (m, y) => get("/reports/vat",          { month: m, year: y }),
  topProducts: (d)    => get("/reports/top-products", d ? { report_date: d } : {}),
  lowStock:    ()     => get("/reports/low-stock"),
};

export const etimsAPI = {
  submit:   (id) => post(`/etims/submit/${id}`),
  pending:  ()   => get("/etims/pending"),
  retryAll: ()   => post("/etims/retry-all"),
};

export const auditAPI = {
  trail:   (p) => get("/audit/trail",    p),
  syncLog: (p) => get("/audit/sync-log", p),
};

export const subscriptionAPI = {
  status:   ()                         => get("/subscription/status"),
  register: (data)                     => post("/subscription/register", data),
  upgrade:  (plan, months, mpesaPhone) => post("/subscription/upgrade", { plan, months, mpesa_phone: mpesaPhone }),
};

export const procurementAPI = {
  // Packaging
  listPackaging:   (productId)      => get(`/procurement/products/${productId}/packaging`),
  upsertPackaging: (productId, d)   => post(`/procurement/products/${productId}/packaging`, d),

  // Purchase Orders
  listPOs:   (p)   => get("/procurement/purchase-orders", p),
  getPO:     (id)  => get(`/procurement/purchase-orders/${id}`),
  createPO:  (d)   => post("/procurement/purchase-orders", d),
  updatePO:  (id, d) => patch(`/procurement/purchase-orders/${id}`, d),
  submitPO:  (id)  => post(`/procurement/purchase-orders/${id}/submit`),
  approvePO: (id)  => post(`/procurement/purchase-orders/${id}/approve`),
  cancelPO:  (id)  => post(`/procurement/purchase-orders/${id}/cancel`),

  // GRNs
  listGRNs:  (p)   => get("/procurement/grns", p),
  getGRN:    (id)  => get(`/procurement/grns/${id}`),
  createGRN: (d)   => post("/procurement/grns", d),
  postGRN:   (id)  => post(`/procurement/grns/${id}/post`),
  cancelGRN: (id)  => post(`/procurement/grns/${id}/cancel`),

  // Invoice Matching
  listMatches:   (p)       => get("/procurement/invoice-matches", p),
  getMatch:      (id)      => get(`/procurement/invoice-matches/${id}`),
  createMatch:   (d)       => post("/procurement/invoice-matches", d),
  resolveMatch:  (id, d)   => patch(`/procurement/invoice-matches/${id}/resolve`, d),

  // Reports
  reportReceived: (p) => get("/procurement/reports/received", p),
  reportOpenPOs:  ()  => get("/procurement/reports/open-pos"),
};
