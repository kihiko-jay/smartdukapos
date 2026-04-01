/**
 * UpgradeWall — shown when a free/expired user tries to access premium features.
 * Displays pricing plans with M-PESA payment trigger.
 */
import { useState } from "react";
import { getToken } from "../api/client";

const PLANS = [
  {
    key:      "starter",
    name:     "Starter",
    price:    1500,
    period:   "month",
    color:    "#f5a623",
    features: ["1 store", "Full inventory management", "Reports & analytics", "Employee management", "KRA eTIMS sync", "Email support"],
    popular:  false,
  },
  {
    key:      "growth",
    name:     "Growth",
    price:    3500,
    period:   "month",
    color:    "#22c55e",
    features: ["Up to 3 stores", "Everything in Starter", "Multi-store reports", "Cross-store inventory", "Priority support"],
    popular:  true,
  },
  {
    key:      "pro",
    name:     "Pro",
    price:    7500,
    period:   "month",
    color:    "#2563eb",
    features: ["Unlimited stores", "Everything in Growth", "API access", "Dedicated account manager", "SLA guarantee"],
    popular:  false,
  },
];

export default function UpgradeWall({ feature = "this feature", daysLeft = 0, isTrialing = false, onDismiss }) {
  const [selectedPlan, setSelectedPlan] = useState("starter");
  const [phone,   setPhone]   = useState("07");
  const [months,  setMonths]  = useState(1);
  const [loading, setLoading] = useState(false);
  const [sent,    setSent]    = useState(false);
  const [error,   setError]   = useState("");

  const plan    = PLANS.find(p => p.key === selectedPlan);
  const total   = plan.price * months;

  const handleUpgrade = async () => {
    setLoading(true); setError("");
    try {
      const token = getToken();
      const res   = await fetch("http://localhost:8000/api/v1/subscription/upgrade", {
        method:  "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body:    JSON.stringify({ plan: selectedPlan, months, mpesa_phone: phone }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail?.message || data.detail || "Failed");
      setSent(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0,
      background: "rgba(0,0,0,0.7)",
      backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 1000, padding: 24,
      fontFamily: "'DM Mono', monospace",
    }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');`}</style>

      <div style={{ background: "#fff", borderRadius: 16, maxWidth: 860, width: "100%", overflow: "hidden", boxShadow: "0 32px 80px rgba(0,0,0,0.4)" }}>

        {/* Header */}
        <div style={{ background: "linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%)", padding: "32px 40px", position: "relative" }}>
          {onDismiss && (
            <button onClick={onDismiss} style={{ position: "absolute", top: 16, right: 16, background: "none", border: "none", color: "#666", fontSize: 20, cursor: "pointer" }}>✕</button>
          )}
          <div style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 13, color: "#f5a623", letterSpacing: "0.1em", marginBottom: 8 }}>DUKAPOS PREMIUM</div>
          <h2 style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 26, color: "#fff", marginBottom: 8 }}>
            Unlock {feature}
          </h2>
          {isTrialing && daysLeft > 0
            ? <p style={{ color: "#f5a623", fontSize: 13 }}>⏳ Your free trial ends in <strong>{daysLeft} days</strong>. Upgrade now to keep access.</p>
            : <p style={{ color: "#888", fontSize: 13 }}>The POS terminal is always free. Upgrade for full back-office power.</p>
          }

          {/* Free vs Premium comparison */}
          <div style={{ display: "flex", gap: 32, marginTop: 20 }}>
            {[
              { label: "Free Forever",  items: ["✓ POS terminal", "✓ Cash & M-PESA sales", "✓ KRA eTIMS receipts", "✓ Unlimited transactions"], color: "#555" },
              { label: "Premium Plans", items: ["✓ Inventory management", "✓ Reports & analytics", "✓ Employee management", "✓ Low stock alerts", "✓ VAT reports"], color: "#f5a623" },
            ].map(col => (
              <div key={col.label} style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: col.color, letterSpacing: "0.08em", marginBottom: 8 }}>{col.label}</div>
                {col.items.map(item => (
                  <div key={item} style={{ fontSize: 12, color: col.color === "#555" ? "#888" : "#e8e4dc", marginBottom: 4 }}>{item}</div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Plans */}
        <div style={{ padding: "28px 40px", borderBottom: "1px solid #f0ebe0" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            {PLANS.map(p => (
              <div key={p.key}
                onClick={() => setSelectedPlan(p.key)}
                style={{
                  border:       `2px solid ${selectedPlan === p.key ? p.color : "#e8e3d8"}`,
                  borderRadius: 10,
                  padding:      "18px 16px",
                  cursor:       "pointer",
                  position:     "relative",
                  background:   selectedPlan === p.key ? "#fdfaf4" : "#fff",
                  transition:   "all 0.15s",
                }}>
                {p.popular && (
                  <div style={{ position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)", background: p.color, color: "#fff", fontSize: 9, padding: "3px 10px", borderRadius: 20, fontWeight: 600, letterSpacing: "0.06em", whiteSpace: "nowrap" }}>
                    MOST POPULAR
                  </div>
                )}
                <div style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 15, color: "#1a1a1a", marginBottom: 4 }}>{p.name}</div>
                <div style={{ marginBottom: 12 }}>
                  <span style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 22, color: p.color }}>KES {p.price.toLocaleString()}</span>
                  <span style={{ fontSize: 11, color: "#aaa" }}>/mo</span>
                </div>
                {p.features.map(f => (
                  <div key={f} style={{ fontSize: 11, color: "#666", marginBottom: 3 }}>✓ {f}</div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Payment */}
        {!sent ? (
          <div style={{ padding: "24px 40px", background: "#fdfaf4" }}>
            <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: "#999", letterSpacing: "0.08em", marginBottom: 6 }}>M-PESA PHONE NUMBER</div>
                <input value={phone} onChange={e => setPhone(e.target.value)} placeholder="07XXXXXXXX"
                  style={{ width: "100%", padding: "10px 14px", border: "1px solid #e8e3d8", borderRadius: 6, fontFamily: "inherit", fontSize: 13, outline: "none" }} />
              </div>
              <div style={{ width: 120 }}>
                <div style={{ fontSize: 10, color: "#999", letterSpacing: "0.08em", marginBottom: 6 }}>MONTHS</div>
                <select value={months} onChange={e => setMonths(Number(e.target.value))}
                  style={{ width: "100%", padding: "10px 14px", border: "1px solid #e8e3d8", borderRadius: 6, fontFamily: "inherit", fontSize: 13, outline: "none", background: "#fff" }}>
                  {[1,3,6,12].map(m => <option key={m} value={m}>{m} mo{m > 1 ? "s" : ""}</option>)}
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: "#999", letterSpacing: "0.08em", marginBottom: 6 }}>TOTAL</div>
                <div style={{ padding: "10px 14px", background: "#fff", border: "1px solid #e8e3d8", borderRadius: 6, fontSize: 14, fontFamily: "'Syne',sans-serif", fontWeight: 700, color: plan.color }}>
                  KES {total.toLocaleString()}
                </div>
              </div>
              <button onClick={handleUpgrade} disabled={loading || phone.length < 10}
                style={{
                  padding: "10px 28px",
                  background: loading || phone.length < 10 ? "#ccc" : plan.color,
                  border: "none", borderRadius: 6, color: "#fff",
                  fontFamily: "'Syne',sans-serif", fontWeight: 700, fontSize: 13,
                  cursor: loading || phone.length < 10 ? "not-allowed" : "pointer",
                  whiteSpace: "nowrap",
                }}>
                {loading ? "SENDING..." : `PAY VIA M-PESA`}
              </button>
            </div>
            {months >= 6 && (
              <div style={{ marginTop: 10, fontSize: 11, color: "#16a34a" }}>
                🎁 {months >= 12 ? "2 months free — best value!" : "1 month free — great deal!"}
              </div>
            )}
            {error && <div style={{ marginTop: 10, fontSize: 12, color: "#ef4444" }}>{error}</div>}
          </div>
        ) : (
          <div style={{ padding: "32px 40px", textAlign: "center" }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>📱</div>
            <div style={{ fontFamily: "'Syne',sans-serif", fontWeight: 800, fontSize: 18, color: "#1a1a1a", marginBottom: 8 }}>Check your phone</div>
            <div style={{ fontSize: 13, color: "#888", maxWidth: 360, margin: "0 auto" }}>
              An M-PESA prompt has been sent to <strong>{phone}</strong>. Enter your PIN to activate your <strong>{plan.name}</strong> plan.
            </div>
            <div style={{ marginTop: 20, fontSize: 12, color: "#aaa" }}>Your account will be upgraded automatically once payment is confirmed.</div>
          </div>
        )}
      </div>
    </div>
  );
}
