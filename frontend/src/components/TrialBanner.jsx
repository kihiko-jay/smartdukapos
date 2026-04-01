/**
 * TrialBanner — sticky top bar shown during the free trial.
 * Disappears once paid. Urgent styling when < 3 days left.
 */
export default function TrialBanner({ daysLeft, onUpgrade }) {
  if (daysLeft === null || daysLeft === undefined) return null;

  const urgent  = daysLeft <= 3;
  const expired = daysLeft <= 0;

  return (
    <div style={{
      background:  expired ? "#dc2626" : urgent ? "#d97706" : "#1a1a1a",
      color:       "#fff",
      padding:     "10px 32px",
      display:     "flex",
      alignItems:  "center",
      justifyContent: "space-between",
      fontSize:    12,
      fontFamily:  "'DM Mono', monospace",
      flexShrink:  0,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span>{expired ? "🔒" : urgent ? "⚠️" : "⏳"}</span>
        <span>
          {expired
            ? "Your free trial has ended. Upgrade to continue using the back office."
            : `Free trial — ${daysLeft} day${daysLeft !== 1 ? "s" : ""} remaining. The POS terminal stays free forever.`
          }
        </span>
      </div>
      <button onClick={onUpgrade} style={{
        background:    "#f5a623",
        border:        "none",
        borderRadius:  6,
        padding:       "6px 18px",
        color:         "#0a0c0f",
        fontFamily:    "'Syne', sans-serif",
        fontWeight:    700,
        fontSize:      11,
        cursor:        "pointer",
        letterSpacing: "0.06em",
        whiteSpace:    "nowrap",
      }}>
        UPGRADE NOW
      </button>
    </div>
  );
}
