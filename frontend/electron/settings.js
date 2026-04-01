/**
 * DukaPOS Settings Window
 * Opened from the POS via the gear icon.
 * Lets the manager configure: API URL, terminal ID, printer, kiosk mode.
 */

const { BrowserWindow, ipcMain } = require("electron");
const path = require("path");

let settingsWindow = null;

function openSettings(parentWindow, store) {
  if (settingsWindow) { settingsWindow.focus(); return; }

  settingsWindow = new BrowserWindow({
    width:  560,
    height: 620,
    parent: parentWindow,
    modal:  true,
    title:  "DukaPOS Settings",
    backgroundColor: "#f5f1e8",
    webPreferences: {
      preload:          path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  });

  // Load settings HTML inline via data URL
  settingsWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(getSettingsHTML())}`);
  settingsWindow.on("closed", () => { settingsWindow = null; });
}

function getSettingsHTML() {
  return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>DukaPOS Settings</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'DM Mono', monospace; background: #f5f1e8; color: #1a1a1a; padding: 32px; font-size: 13px; }
    h2 { font-family: 'Syne', sans-serif; font-weight: 800; font-size: 18px; margin-bottom: 24px; }
    .group { margin-bottom: 20px; }
    label { display: block; font-size: 10px; color: #999; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 6px; }
    input, select { width: 100%; padding: 10px 14px; border: 1px solid #e8e3d8; border-radius: 6px; font-family: inherit; font-size: 13px; color: #1a1a1a; background: #fff; outline: none; }
    input:focus { border-color: #f5a623; }
    .row { display: flex; gap: 12px; }
    .row .group { flex: 1; }
    button { width: 100%; padding: 14px; background: #f5a623; border: none; border-radius: 8px; font-family: 'Syne', sans-serif; font-weight: 700; font-size: 14px; color: #fff; cursor: pointer; margin-top: 8px; }
    .saved { text-align: center; color: #16a34a; font-size: 12px; margin-top: 12px; display: none; }
    .toggle { display: flex; align-items: center; gap: 10px; }
    .toggle input { width: auto; }
  </style>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400&family=Syne:wght@700;800&display=swap" rel="stylesheet">
</head>
<body>
  <h2>⚙️ DukaPOS Settings</h2>

  <div class="row">
    <div class="group">
      <label>Terminal ID</label>
      <input id="terminalId" placeholder="T01" />
    </div>
    <div class="group">
      <label>Store Name</label>
      <input id="storeName" placeholder="My Duka Store" />
    </div>
  </div>

  <div class="group">
    <label>Backend API URL</label>
    <input id="apiBase" placeholder="http://localhost:8000/api/v1" />
  </div>

  <div class="group">
    <label>Receipt Printer</label>
    <select id="printerName"><option value="">-- Select Printer --</option></select>
  </div>

  <div class="group">
    <label>Display Mode</label>
    <div class="toggle">
      <input type="checkbox" id="kioskMode" />
      <span>Kiosk mode (fullscreen, no title bar) — use in production</span>
    </div>
  </div>

  <button onclick="save()">SAVE SETTINGS</button>
  <div class="saved" id="saved">✓ Settings saved. Restart the app to apply display changes.</div>

  <script>
    async function load() {
      if (!window.electron) return;
      const cfg = await window.electron.config.getAll();
      document.getElementById("terminalId").value = cfg.terminalId || "";
      document.getElementById("storeName").value   = cfg.storeName  || "";
      document.getElementById("apiBase").value     = cfg.apiBase    || "";
      document.getElementById("kioskMode").checked = cfg.kioskMode  || false;

      const printers = await window.electron.printer.getList();
      const sel = document.getElementById("printerName");
      printers.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.text  = p.name + (p.isDefault ? " (Default)" : "");
        if (p.name === cfg.printerName) opt.selected = true;
        sel.appendChild(opt);
      });
    }

    async function save() {
      if (!window.electron) return;
      await window.electron.config.set("terminalId",  document.getElementById("terminalId").value);
      await window.electron.config.set("storeName",   document.getElementById("storeName").value);
      await window.electron.config.set("apiBase",     document.getElementById("apiBase").value);
      await window.electron.config.set("kioskMode",   document.getElementById("kioskMode").checked);
      await window.electron.config.set("printerName", document.getElementById("printerName").value);
      document.getElementById("saved").style.display = "block";
    }

    load();
  </script>
</body>
</html>`;
}

module.exports = { openSettings };
