/**
 * DukaPOS — Electron Preload Script (v4.0)
 *
 * Changes:
 *  - Added auth:saveTokens / auth:getTokens / auth:clearTokens for refresh token support
 *  - Added offline:markFailed and offline:stats for queue observability
 *  - idempotency_key surface exposed to renderer
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {

  app: {
    getVersion: ()     => ipcRenderer.invoke("app:getVersion"),
    getPath:    (name) => ipcRenderer.invoke("app:getPath", name),
    isElectron: true,
  },

  config: {
    get:    (key)        => ipcRenderer.invoke("config:get", key),
    set:    (key, value) => ipcRenderer.invoke("config:set", key, value),
    getAll: ()           => ipcRenderer.invoke("config:getAll"),
  },

  // NEW: token persistence (access + refresh)
  auth: {
    saveTokens:  (tokens) => ipcRenderer.invoke("auth:saveTokens",  tokens),
    getTokens:   ()       => ipcRenderer.invoke("auth:getTokens"),
    clearTokens: ()       => ipcRenderer.invoke("auth:clearTokens"),
  },

  window: {
    minimize:   () => ipcRenderer.invoke("window:minimize"),
    maximize:   () => ipcRenderer.invoke("window:maximize"),
    close:      () => ipcRenderer.invoke("window:close"),
    fullscreen: () => ipcRenderer.invoke("window:fullscreen"),
    reload:     () => ipcRenderer.invoke("window:reload"),
  },

  drawer: {
    open: () => ipcRenderer.invoke("drawer:open"),
  },

  printer: {
    printReceipt: (data) => ipcRenderer.invoke("printer:printReceipt", data),
    getList:      ()     => ipcRenderer.invoke("printer:getList"),
  },

  // UPGRADED: SQLite-backed, idempotent, with failure tracking
  offline: {
    enqueue:    (txn)             => ipcRenderer.invoke("offline:enqueue",    txn),
    getQueue:   ()                => ipcRenderer.invoke("offline:getQueue"),
    clearItem:  (idempotencyKey)  => ipcRenderer.invoke("offline:clearItem",  idempotencyKey),
    markFailed: (idempotencyKey, error) => ipcRenderer.invoke("offline:markFailed", idempotencyKey, error),
    stats:      ()                => ipcRenderer.invoke("offline:stats"),
    clearAll:   ()                => ipcRenderer.invoke("offline:clearAll"),
  },

  dialog: {
    confirm: (title, message) => ipcRenderer.invoke("dialog:confirm", { title, message }),
    error:   (title, message) => ipcRenderer.invoke("dialog:error",   { title, message }),
  },

  on: (channel, callback) => {
    const allowed = ["sync:status", "update:available", "update:downloaded", "offline:restored"];
    if (allowed.includes(channel)) {
      ipcRenderer.on(channel, (_, ...args) => callback(...args));
    }
  },
  off: (channel, callback) => {
    ipcRenderer.removeListener(channel, callback);
  },
});
