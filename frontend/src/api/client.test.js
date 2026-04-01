/**
 * DukaPOS v4.1 — Frontend session helper tests
 *
 * Runs in a jsdom-like environment via Vitest.
 * Tests confirm:
 *   1. getSession and clearSession ARE exported from client.js
 *   2. getSession returns null when no session is stored
 *   3. getSession returns parsed data after a session is written
 *   4. clearSession removes the session and clears tokens
 *   5. App boot cannot fail on missing exports (import smoke test)
 *
 * Run:  cd frontend && npx vitest run src/api/client.test.js
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// ── Mock sessionStorage ───────────────────────────────────────────────────────
// jsdom provides sessionStorage but we reset it between tests manually.
function clearStorage() {
  sessionStorage.clear();
}

// ── Mock window.electron (absent in browser/test environment) ─────────────────
// isElectron = false in tests because window.electron is undefined.

// ── Import under test ─────────────────────────────────────────────────────────
// This import is itself the smoke test: if getSession or clearSession are not
// exported, Vitest will throw here and the test suite fails with a clear message.
import {
  getSession,
  clearSession,
  sessionHelpers,
  parseMoney,
  fmtKES,
  authAPI,
  productsAPI,
  transactionsAPI,
  reportsAPI,
} from "./client.js";

// ── 1: Export smoke tests ──────────────────────────────────────────────────────

describe("client.js exports", () => {
  it("exports getSession as a function", () => {
    expect(typeof getSession).toBe("function");
  });

  it("exports clearSession as a function", () => {
    expect(typeof clearSession).toBe("function");
  });

  it("exports sessionHelpers with saveTokens/getTokens/clearTokens", () => {
    expect(typeof sessionHelpers.saveTokens).toBe("function");
    expect(typeof sessionHelpers.getTokens).toBe("function");
    expect(typeof sessionHelpers.clearTokens).toBe("function");
  });

  it("exports all required API namespaces", () => {
    expect(authAPI).toBeDefined();
    expect(productsAPI).toBeDefined();
    expect(transactionsAPI).toBeDefined();
    expect(reportsAPI).toBeDefined();
  });

  it("exports money helpers", () => {
    expect(typeof parseMoney).toBe("function");
    expect(typeof fmtKES).toBe("function");
  });
});

// ── 2: getSession behaviour ───────────────────────────────────────────────────

describe("getSession", () => {
  beforeEach(clearStorage);
  afterEach(clearStorage);

  it("returns null when no session is stored", () => {
    expect(getSession()).toBeNull();
  });

  it("returns null when sessionStorage contains invalid JSON", () => {
    sessionStorage.setItem("dukapos_session", "not-json{{{");
    expect(getSession()).toBeNull();
  });

  it("returns the session object when valid JSON is stored", () => {
    const session = { id: 42, name: "James Kihiko", role: "admin", terminal_id: "T01" };
    sessionStorage.setItem("dukapos_session", JSON.stringify(session));
    expect(getSession()).toEqual(session);
  });

  it("returns the role field so App.jsx can route on it", () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({ role: "cashier" }));
    expect(getSession().role).toBe("cashier");
  });

  it("returns null for empty string stored value", () => {
    sessionStorage.setItem("dukapos_session", "");
    expect(getSession()).toBeNull();
  });
});

// ── 3: clearSession behaviour ─────────────────────────────────────────────────

describe("clearSession", () => {
  beforeEach(clearStorage);
  afterEach(clearStorage);

  it("removes dukapos_session from sessionStorage", async () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({ role: "admin" }));
    await clearSession();
    expect(sessionStorage.getItem("dukapos_session")).toBeNull();
  });

  it("getSession returns null after clearSession", async () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({ role: "manager" }));
    await clearSession();
    expect(getSession()).toBeNull();
  });

  it("also removes dukapos_access and dukapos_refresh tokens", async () => {
    sessionStorage.setItem("dukapos_access",  "fake_access_token");
    sessionStorage.setItem("dukapos_refresh", "fake_refresh_token");
    await clearSession();
    expect(sessionStorage.getItem("dukapos_access")).toBeNull();
    expect(sessionStorage.getItem("dukapos_refresh")).toBeNull();
  });

  it("is safe to call when no session exists (no throw)", async () => {
    await expect(clearSession()).resolves.not.toThrow();
  });
});

// ── 4: App boot simulation ────────────────────────────────────────────────────
// Reproduces the sequence App.jsx performs on mount.

describe("App boot session restore", () => {
  beforeEach(clearStorage);
  afterEach(clearStorage);

  it("reads null session at cold boot (no stored session)", () => {
    const session = getSession();
    // App.jsx: if (session) { setPage(...) } — this must not throw
    expect(session).toBeNull();
  });

  it("reads correct role at warm boot (session persisted from previous login)", () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({
      id: 1, name: "Test Admin", role: "admin", terminal_id: "T01",
    }));
    const session = getSession();
    expect(session).not.toBeNull();
    expect(["manager", "admin"].includes(session.role)).toBe(true);
  });

  it("routes cashier to POS (not backoffice) at boot", () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({ role: "cashier" }));
    const session = getSession();
    const page = ["manager", "admin"].includes(session.role) ? "backoffice" : "pos";
    expect(page).toBe("pos");
  });

  it("routes admin to backoffice at boot", () => {
    sessionStorage.setItem("dukapos_session", JSON.stringify({ role: "admin" }));
    const session = getSession();
    const page = ["manager", "admin"].includes(session.role) ? "backoffice" : "pos";
    expect(page).toBe("backoffice");
  });
});

// ── 5: Money helpers ──────────────────────────────────────────────────────────

describe("money helpers", () => {
  it("parseMoney handles numeric string", () => {
    expect(parseMoney("1500.50")).toBe(1500.50);
  });

  it("parseMoney handles null/undefined gracefully", () => {
    expect(parseMoney(null)).toBe(0);
    expect(parseMoney(undefined)).toBe(0);
  });

  it("fmtKES formats with KES prefix", () => {
    expect(fmtKES(1500)).toMatch(/^KES/);
  });
});
