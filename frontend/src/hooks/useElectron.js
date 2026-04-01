const noop = () => Promise.resolve(null);
const browserFallback = {
  app:     { isElectron: false, getVersion: () => Promise.resolve("web") },
  config:  { get: noop, set: noop, getAll: () => Promise.resolve({}) },
  window:  { minimize: noop, maximize: noop, close: noop, fullscreen: noop, reload: () => window.location.reload() },
  drawer:  { open: () => { console.log("Cash drawer: browser no-op"); return Promise.resolve({ success: true }); } },
  printer: { printReceipt: () => { window.print(); return Promise.resolve({ success: true }); }, getList: () => Promise.resolve([]) },
  offline: { enqueue: noop, getQueue: () => Promise.resolve([]), clearItem: noop, clearAll: noop },
  dialog:  { confirm: (title, msg) => Promise.resolve(window.confirm(msg)), error: (_, msg) => { alert(msg); return Promise.resolve(); } },
  on: () => {}, off: () => {},
};

export function useElectron() {
  const el = (typeof window !== "undefined" && window.electron) ? window.electron : browserFallback;
  return { isElectron: el.app?.isElectron || false, ...el };
}
