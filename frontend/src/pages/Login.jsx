import { useState } from "react";
import { authAPI, sessionHelpers } from "../api/client";

const isElectron = typeof window !== "undefined" && !!window.electron?.app?.isElectron;

// Demo mode: only active when explicitly enabled in the build environment.
// Set VITE_DEMO_MODE=true in .env.development or .env.staging only.
// Never set this in .env.production.
const DEMO_MODE     = import.meta.env.VITE_DEMO_MODE === "true";
const DEMO_EMAIL    = DEMO_MODE ? (import.meta.env.VITE_DEMO_EMAIL    || "admin@dukapos.ke") : "";
const DEMO_PASSWORD = DEMO_MODE ? (import.meta.env.VITE_DEMO_PASSWORD || "admin1234")        : "";

export default function Login({ onLogin }) {
  const [email,    setEmail]    = useState(DEMO_EMAIL);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error,    setError]    = useState("");
  const [loading,  setLoading]  = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(""); setLoading(true);
    try {
      const data = await authAPI.login(email, password);

      // v4.0: persist BOTH tokens (access + refresh)
      await sessionHelpers.saveTokens({
        accessToken:  data.access_token,
        refreshToken: data.refresh_token,
      });

      // v4.1: always write a sessionStorage mirror so getSession() is readable
      // synchronously at app boot — before any Electron IPC await completes.
      const session = {
        id:          data.employee_id,
        name:        data.full_name,
        role:        data.role,
        terminal_id: data.terminal_id,
      };
      sessionStorage.setItem("dukapos_session", JSON.stringify(session));
      if (isElectron) {
        // Also persist to config store for cross-session restore
        await window.electron.config.set("session", session);
      }

      onLogin(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight:"100vh", background:"#0a0c0f", display:"flex", alignItems:"center", justifyContent:"center", fontFamily:"'DM Mono',monospace" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');`}</style>
      <div style={{ width:380, background:"#111316", border:"1px solid #1e2128", borderRadius:12, padding:40 }}>
        <div style={{ textAlign:"center", marginBottom:32 }}>
          <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:28, color:"#e8e4dc" }}>
            DUKA<span style={{ color:"#f5a623" }}>POS</span>
          </div>
          <div style={{ fontSize:11, color:"#555", marginTop:6, letterSpacing:"0.08em" }}>STAFF LOGIN</div>
        </div>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom:16 }}>
            <label style={{ fontSize:10, color:"#666", letterSpacing:"0.08em", display:"block", marginBottom:6 }}>EMAIL</label>
            <input value={email} onChange={e=>setEmail(e.target.value)} type="email" required
              style={{ width:"100%", background:"#161921", border:"1px solid #2a2d35", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:13, padding:"10px 14px", outline:"none", boxSizing:"border-box" }} />
          </div>
          <div style={{ marginBottom:24 }}>
            <label style={{ fontSize:10, color:"#666", letterSpacing:"0.08em", display:"block", marginBottom:6 }}>PASSWORD</label>
            <input value={password} onChange={e=>setPassword(e.target.value)} type="password" required
              style={{ width:"100%", background:"#161921", border:"1px solid #2a2d35", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:13, padding:"10px 14px", outline:"none", boxSizing:"border-box" }} />
          </div>
          {error && <div style={{ background:"#2a0f0f", border:"1px solid #ef4444", borderRadius:6, padding:"8px 12px", fontSize:12, color:"#ef4444", marginBottom:16 }}>{error}</div>}
          <button type="submit" disabled={loading}
            style={{ width:"100%", padding:14, background:loading?"#1e2128":"#f5a623", border:"none", borderRadius:8, color:loading?"#555":"#0a0c0f", fontFamily:"'Syne',sans-serif", fontWeight:700, fontSize:14, letterSpacing:"0.08em", cursor:loading?"not-allowed":"pointer" }}>
            {loading ? "SIGNING IN..." : "SIGN IN"}
          </button>
        </form>
        <div style={{ marginTop:24, padding:16, background:"#0d0f13", borderRadius:8, fontSize:10, color:"#444", lineHeight:1.8 }}>
          {DEMO_MODE ? (
            <>
              <div style={{ color:"#666", marginBottom:4 }}>DEMO CREDENTIALS</div>
              <div>admin@dukapos.ke / admin1234</div>
              <div>james@dukapos.ke / cashier1234</div>
            </>
          ) : (
            <div style={{ color:"#333" }}>Contact your store administrator for login credentials.</div>
          )}
        </div>
      </div>
    </div>
  );
}
