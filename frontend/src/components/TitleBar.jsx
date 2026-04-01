/**
 * Custom title bar — shown in Electron (frameless window).
 * Hidden in browser mode. Has window controls + status indicators.
 */
import { useElectron } from "../hooks/useElectron";

export default function TitleBar({ session, isOnline, queueLength }) {
  const { isElectron, window: win } = useElectron();
  if (!isElectron) return null;

  return (
    <div style={{
      height: 36,
      background: "#060809",
      borderBottom: "1px solid #1e2128",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "0 16px",
      WebkitAppRegion: "drag",  // makes this area draggable
      flexShrink: 0,
      userSelect: "none",
    }}>
      {/* Left: branding */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 11, color: "#555" }}>
        <span style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 12, color: "#f5a623" }}>
          DUKA<span style={{ color: "#e8e4dc" }}>POS</span>
        </span>
        <span>v{window.electron?.appVersion || "4.0.0"}</span>
      </div>

      {/* Center: status */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 10, color: "#555", WebkitAppRegion: "no-drag" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 5, height: 5, borderRadius: "50%", background: isOnline ? "#22c55e" : "#ef4444" }}/>
          <span style={{ color: isOnline ? "#22c55e" : "#ef4444" }}>{isOnline ? "ONLINE" : "OFFLINE"}</span>
        </div>
        {queueLength > 0 && (
          <span style={{ color: "#f5a623" }}>⏳ {queueLength} QUEUED</span>
        )}
        {session && <span>{session.name?.toUpperCase()} · {session.terminal_id || "T01"}</span>}
      </div>

      {/* Right: window controls */}
      <div style={{ display: "flex", gap: 6, WebkitAppRegion: "no-drag" }}>
        {[
          { label: "—", action: win.minimize,   color: "#f5a623" },
          { label: "□", action: win.maximize,    color: "#22c55e" },
          { label: "✕", action: win.close,       color: "#ef4444" },
        ].map(({ label, action, color }) => (
          <button key={label} onClick={action} style={{
            width: 20, height: 20, borderRadius: "50%", border: "none",
            background: "#1e2128", color: color, fontSize: 10,
            cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
            transition: "background 0.1s",
          }}
          onMouseEnter={e => e.target.style.background = color}
          onMouseLeave={e => e.target.style.background = "#1e2128"}
          >{label}</button>
        ))}
      </div>
    </div>
  );
}
