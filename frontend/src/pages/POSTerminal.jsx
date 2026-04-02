/**
 * POSTerminal (v4.2)
 *
 * Layout redesign:
 *  - LEFT:  search bar + sale items (cart) — this is the main working area
 *  - RIGHT: totals + payment + checkout only
 *  - Search shows a dropdown of results — cashier clicks to add, no auto-add
 *  - No full-screen empty state — left panel always shows the sale in progress
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { productsAPI, transactionsAPI, mpesaAPI, etimsAPI, fmtKES, parseMoney } from "../api/client";
import { useOfflineQueue } from "../hooks/useOfflineQueue";
import { useMpesaSocket } from "../hooks/useMpesaSocket";
import TitleBar from "../components/TitleBar";

const VAT = 0.16;

async function readSession() {
  if (typeof window !== "undefined" && window.electron?.config) {
    return window.electron.config.get("session");
  }
  try { return JSON.parse(sessionStorage.getItem("dukapos_session")); }
  catch { return null; }
}

function generateTxnNumber() {
  const ts  = Date.now().toString(36).toUpperCase();
  const rnd = Math.random().toString(36).slice(2, 6).toUpperCase();
  return `TXN-${ts}-${rnd}`;
}

export default function POSTerminal({ onNavigate }) {
  const [session, setSession]         = useState(null);
  const { isOnline, queueLength, stats, enqueue, syncQueue } = useOfflineQueue();

  const [search,       setSearch]       = useState("");
  const [results,      setResults]      = useState([]);
  const [searching,    setSearching]    = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  const [cart,         setCart]         = useState([]);
  const [paymentMode,  setPaymentMode]  = useState(null);
  const [cashInput,    setCashInput]    = useState("");
  const [mpesaPhone,   setMpesaPhone]   = useState("07");
  const [mpesaStatus,  setMpesaStatus]  = useState(null);
  const [mpesaFailMsg, setMpesaFailMsg] = useState("");
  const [receipt,      setReceipt]      = useState(null);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState("");

  const txnKeyRef   = useRef(null);
  const searchRef   = useRef(null);
  const dropdownRef = useRef(null);

  useEffect(() => { readSession().then(setSession); }, []);
  useEffect(() => { if (isOnline && queueLength > 0) syncQueue(); }, [isOnline]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target) &&
          searchRef.current && !searchRef.current.contains(e.target)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Search with debounce — shows dropdown, never auto-adds
  useEffect(() => {
    if (!search.trim()) {
      setResults([]);
      setShowDropdown(false);
      return;
    }
    const debounce = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await productsAPI.list({ is_active: true, search: search.trim(), limit: 10 });
        setResults(res);
        setShowDropdown(res.length > 0);
      } catch (e) {
        setError(e.message);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(debounce);
  }, [search]);

  const handlePaymentConfirmed = useCallback((confirmedTxnNumber) => {
    if (txnKeyRef.current === confirmedTxnNumber || receipt?.txn_number === confirmedTxnNumber) {
      setMpesaStatus("confirmed");
    }
  }, [receipt]);

  const handlePaymentFailed = useCallback((txnNumber, resultCode, message) => {
    if (txnKeyRef.current === txnNumber || receipt?.txn_number === txnNumber) {
      setMpesaStatus("failed");
      setMpesaFailMsg(message || `Payment failed (code ${resultCode})`);
    }
  }, [receipt]);

  const { connected: wsConnected } = useMpesaSocket(
    session?.terminal_id,
    handlePaymentConfirmed,
    handlePaymentFailed,
  );

  const addToCart = (p) => {
    setCart(prev => {
      const ex = prev.find(i => i.id === p.id);
      return ex
        ? prev.map(i => i.id === p.id ? { ...i, qty: i.qty + 1 } : i)
        : [...prev, { ...p, qty: 1 }];
    });
    setSearch("");
    setResults([]);
    setShowDropdown(false);
    searchRef.current?.focus();
  };

  const updateQty  = (id, d) => setCart(prev => prev.map(i => i.id === id ? { ...i, qty: Math.max(1, i.qty + d) } : i));
  const removeItem = (id)    => setCart(prev => prev.filter(i => i.id !== id));

  const clearCart = () => {
    setCart([]); setPaymentMode(null); setCashInput(""); setMpesaPhone("07");
    setMpesaStatus(null); setMpesaFailMsg(""); setReceipt(null); setError("");
    setSearch(""); setResults([]); setShowDropdown(false);
    txnKeyRef.current = null;
    setTimeout(() => searchRef.current?.focus(), 50);
  };

  const subtotal  = cart.reduce((s, i) => s + parseMoney(i.selling_price) * i.qty, 0);
  const vatAmount = subtotal * VAT;
  const total     = subtotal + vatAmount;
  const cashGiven = parseFloat(cashInput) || 0;
  const change    = cashGiven - total;

  const handleMpesaPush = async () => {
    if (!receipt?.id) return;
    setMpesaStatus("waiting"); setError(""); setMpesaFailMsg("");
    try {
      await mpesaAPI.stkPush(mpesaPhone, total, receipt.txn_number);
    } catch (e) {
      setError(e.message);
      setMpesaStatus(null);
    }
  };

  const handleCompleteSale = async () => {
    if (loading) return;
    setLoading(true); setError("");
    if (!txnKeyRef.current) txnKeyRef.current = generateTxnNumber();
    const idempotencyKey = txnKeyRef.current;

    const payload = {
      idempotency_key: idempotencyKey,
      txn_number:      idempotencyKey,
      terminal_id:     session?.terminal_id || "T01",
      payment_method:  paymentMode,
      discount_amount: "0",
      cash_tendered:   paymentMode === "cash" ? cashGiven.toFixed(2) : undefined,
      mpesa_phone:     paymentMode === "mpesa" ? mpesaPhone : undefined,
      items: cart.map(i => ({
        product_id: i.id,
        qty:        i.qty,
        unit_price: parseMoney(i.selling_price).toFixed(2),
        discount:   "0",
      })),
    };

    try {
      if (!isOnline) {
        await enqueue(payload);
        setReceipt({
          txn_number:   idempotencyKey,
          total:        total.toFixed(2),
          items:        cart.map(i => ({ ...i, line_total: parseMoney(i.selling_price) * i.qty })),
          etims_synced: false,
          sync_status:  "local",
        });
      } else {
        const txn = await transactionsAPI.create(payload, {
          headers: { "Idempotency-Key": idempotencyKey },
        });
        etimsAPI.submit(txn.id).catch(err =>
          console.warn("eTIMS submit failed (will retry):", err.message)
        );
        setReceipt(txn);
      }
    } catch (e) {
      setError(e.message?.includes("Too many")
        ? "Too many requests. Please wait a moment and try again."
        : e.message);
    } finally {
      setLoading(false);
    }
  };

  const canComplete =
    cart.length > 0 && !loading && (
      (paymentMode === "cash"  && cashGiven >= total) ||
       paymentMode === "card"  ||
      (paymentMode === "mpesa" && mpesaStatus === "confirmed")
    );

  const emojis = { Dairy:"🥛", Bakery:"🍞", Beverages:"🥤", Household:"🧴", Grocery:"🛒" };

  return (
    <div style={{ fontFamily:"'DM Mono','Courier New',monospace", background:"#0a0c0f", color:"#e8e4dc", height:"100vh", display:"flex", flexDirection:"column", fontSize:13, overflow:"hidden" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Syne:wght@700;800&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: #2a2d35; border-radius: 2px; }
        .pb { flex:1; padding:12px 8px; border-radius:8px; border:2px solid #1e2128; background:#111316; color:#e8e4dc; font-family:inherit; font-size:11px; cursor:pointer; transition:all 0.15s; letter-spacing:0.08em; text-transform:uppercase; }
        .pb:hover { border-color:#f5a623; color:#f5a623; }
        .pb.sel { background:#f5a623; border-color:#f5a623; color:#0a0c0f; font-weight:500; }
        .cob { width:100%; padding:16px; background:#22c55e; border:none; border-radius:8px; color:#fff; font-family:'Syne',sans-serif; font-size:15px; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; cursor:pointer; }
        .cob:disabled { background:#1e2128; color:#444; cursor:not-allowed; }
        .ifield { width:100%; background:#161921; border:1px solid #2a2d35; border-radius:6px; color:#e8e4dc; font-family:inherit; font-size:14px; padding:10px 14px; outline:none; }
        .ifield:focus { border-color:#f5a623; }
        .drop-item { padding:10px 14px; cursor:pointer; border-bottom:1px solid #1e2128; display:flex; justify-content:space-between; align-items:center; transition:background 0.1s; }
        .drop-item:last-child { border-bottom: none; }
        .drop-item:hover { background:#161921; }
        .qty-btn { width:26px; height:26px; background:#1a1d24; border:1px solid #2a2d35; border-radius:4px; color:#e8e4dc; cursor:pointer; font-size:15px; display:flex; align-items:center; justify-content:center; transition: border-color 0.1s; }
        .qty-btn:hover { border-color:#f5a623; color:#f5a623; }
      `}</style>

      <TitleBar session={session} isOnline={isOnline} queueLength={queueLength} wsConnected={wsConnected} />

      {/* Top bar */}
      <div style={{ background:"#0d0f13", borderBottom:"1px solid #1e2128", padding:"0 20px", display:"flex", alignItems:"center", justifyContent:"space-between", height:48, flexShrink:0 }}>
        <div style={{ display:"flex", alignItems:"center", gap:16 }}>
          <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:16, color:"#f5a623" }}>DUKA<span style={{ color:"#e8e4dc" }}>POS</span></span>
          <span style={{ color:"#555", fontSize:11, letterSpacing:"0.06em" }}>TERMINAL {session?.terminal_id || "T01"}</span>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:16 }}>
          <div style={{ display:"flex", alignItems:"center", gap:6 }}>
            <div style={{ width:6, height:6, borderRadius:"50%", background: isOnline ? "#22c55e" : "#ef4444" }}/>
            <span style={{ color: isOnline ? "#22c55e" : "#ef4444", fontSize:11 }}>{isOnline ? "ONLINE" : "OFFLINE"}</span>
          </div>
          {queueLength > 0 && (
            <span style={{ background:"#f5a623", color:"#0a0c0f", fontSize:10, padding:"2px 8px", borderRadius:20, fontWeight:600 }}>
              ⏳ {stats.pending} QUEUED{stats.failed > 0 ? ` · ${stats.failed} FAILED` : ""}
            </span>
          )}
          {(session?.role === "manager" || session?.role === "admin") && (
            <button onClick={() => onNavigate?.("backoffice")}
              style={{ background:"#1e2128", border:"1px solid #2a2d35", borderRadius:6, color:"#888", fontFamily:"inherit", fontSize:11, padding:"4px 12px", cursor:"pointer" }}>
              BACK OFFICE →
            </button>
          )}
          <span style={{ color:"#888", fontSize:11 }}>CASHIER: {session?.name?.toUpperCase()}</span>
        </div>
      </div>

      {/* Main body */}
      <div style={{ display:"flex", flex:1, overflow:"hidden" }}>

        {/* LEFT: Search + Sale items */}
        <div style={{ flex:1, display:"flex", flexDirection:"column", overflow:"hidden", borderRight:"1px solid #1e2128" }}>

          {/* Search bar */}
          <div style={{ padding:"12px 16px", borderBottom:"1px solid #1e2128", background:"#0d0f13", position:"relative", zIndex:10 }}>
            <input
              ref={searchRef}
              className="ifield"
              placeholder={searching ? "Searching..." : "🔍  Search by name, SKU or scan barcode..."}
              value={search}
              onChange={e => { setSearch(e.target.value); setError(""); }}
              onFocus={() => results.length > 0 && setShowDropdown(true)}
              autoFocus
            />
            {/* Results dropdown */}
            {showDropdown && (
              <div ref={dropdownRef} style={{ position:"absolute", top:"calc(100% - 2px)", left:16, right:16, background:"#111316", border:"1px solid #2a2d35", borderTop:"none", borderRadius:"0 0 8px 8px", zIndex:100, maxHeight:300, overflowY:"auto", boxShadow:"0 8px 24px rgba(0,0,0,0.6)" }}>
                {results.map(p => (
                  <div key={p.id} className="drop-item" onClick={() => addToCart(p)}>
                    <div>
                      <div style={{ fontSize:12, fontWeight:500, color:"#e8e4dc" }}>
                        {emojis[p.category?.name] || "🛒"} {p.name}
                      </div>
                      <div style={{ fontSize:10, color:"#555", marginTop:2 }}>{p.sku} · {p.category?.name || "—"}</div>
                    </div>
                    <div style={{ textAlign:"right", flexShrink:0 }}>
                      <div style={{ color:"#f5a623", fontSize:13, fontWeight:500 }}>KES {parseMoney(p.selling_price).toLocaleString()}</div>
                      <div style={{ fontSize:10, color: p.stock_quantity < 10 ? "#ef4444" : "#555" }}>Stock: {p.stock_quantity}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Column headers */}
          {cart.length > 0 && (
            <div style={{ display:"grid", gridTemplateColumns:"1fr 90px 100px 28px", gap:8, padding:"7px 16px", borderBottom:"1px solid #1e2128", fontSize:10, color:"#3a3d45", letterSpacing:"0.06em", flexShrink:0 }}>
              <span>ITEM</span>
              <span style={{ textAlign:"center" }}>QTY</span>
              <span style={{ textAlign:"right" }}>AMOUNT</span>
              <span/>
            </div>
          )}

          {/* Sale items list */}
          <div style={{ flex:1, overflowY:"auto" }}>
            {cart.length === 0 ? (
              <div style={{ display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", height:"100%", gap:10 }}>
                <div style={{ fontSize:36, opacity:0.3 }}>🧾</div>
                <div style={{ fontSize:11, letterSpacing:"0.08em", color:"#2a2d35" }}>NO ITEMS — SEARCH TO ADD</div>
              </div>
            ) : (
              cart.map(item => (
                <div key={item.id} style={{ display:"grid", gridTemplateColumns:"1fr 90px 100px 28px", gap:8, padding:"10px 16px", borderBottom:"1px solid #141720", alignItems:"center" }}>
                  <div>
                    <div style={{ fontSize:12, fontWeight:500, color:"#e8e4dc" }}>
                      {emojis[item.category?.name] || "🛒"} {item.name}
                    </div>
                    <div style={{ fontSize:10, color:"#555", marginTop:2 }}>
                      KES {parseMoney(item.selling_price).toLocaleString()} each
                    </div>
                  </div>
                  <div style={{ display:"flex", alignItems:"center", justifyContent:"center", gap:4 }}>
                    <button className="qty-btn" onClick={() => updateQty(item.id, -1)}>−</button>
                    <span style={{ width:22, textAlign:"center", fontSize:13 }}>{item.qty}</span>
                    <button className="qty-btn" onClick={() => updateQty(item.id, 1)}>+</button>
                  </div>
                  <div style={{ textAlign:"right", fontSize:12, fontWeight:500 }}>
                    KES {(parseMoney(item.selling_price) * item.qty).toLocaleString()}
                  </div>
                  <button onClick={() => removeItem(item.id)}
                    style={{ background:"none", border:"none", color:"#333", cursor:"pointer", fontSize:13, textAlign:"center", padding:0 }}>✕</button>
                </div>
              ))
            )}
          </div>

          {/* Item count footer */}
          {cart.length > 0 && (
            <div style={{ padding:"6px 16px", borderTop:"1px solid #1e2128", background:"#0d0f13", fontSize:10, color:"#3a3d45", letterSpacing:"0.06em", display:"flex", justifyContent:"space-between", flexShrink:0 }}>
              <span>{cart.length} LINE ITEM{cart.length !== 1 ? "S" : ""}</span>
              <span>{cart.reduce((s, i) => s + i.qty, 0)} TOTAL UNITS</span>
            </div>
          )}
        </div>

        {/* RIGHT: Totals + Payment */}
        <div style={{ width:"40%", display:"flex", flexDirection:"column", overflow:"hidden", background:"#0d0f13" }}>

          {/* Totals block */}
          <div style={{ padding:"20px 16px", borderBottom:"1px solid #1e2128", flexShrink:0 }}>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:8, color:"#555", fontSize:11 }}>
              <span>SUBTOTAL</span><span>{fmtKES(subtotal)}</span>
            </div>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:14, color:"#555", fontSize:11 }}>
              <span>VAT (16%)</span><span>{fmtKES(vatAmount)}</span>
            </div>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", paddingTop:12, borderTop:"1px solid #1e2128" }}>
              <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:24, color:"#e8e4dc" }}>TOTAL</span>
              <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:24, color:"#f5a623" }}>{fmtKES(total)}</span>
            </div>
          </div>

          {/* Payment + actions */}
          <div style={{ flex:1, overflowY:"auto", padding:"14px 16px" }}>
            {error && (
              <div style={{ background:"#2a0f0f", border:"1px solid #ef4444", borderRadius:6, padding:"8px 12px", fontSize:11, color:"#ef4444", marginBottom:12 }}>{error}</div>
            )}

            {!receipt && cart.length > 0 && (
              <>
                <div style={{ fontSize:10, color:"#444", letterSpacing:"0.06em", marginBottom:8 }}>PAYMENT METHOD</div>
                <div style={{ display:"flex", gap:6, marginBottom:16 }}>
                  {["cash","mpesa","card"].map(m => (
                    <button key={m} className={`pb${paymentMode === m ? " sel" : ""}`}
                      onClick={() => { setPaymentMode(m); setMpesaStatus(null); setMpesaFailMsg(""); }}>
                      {m === "cash" ? "💵 Cash" : m === "mpesa" ? "📱 M-PESA" : "💳 Card"}
                    </button>
                  ))}
                </div>

                {paymentMode === "cash" && (
                  <div style={{ marginBottom:16 }}>
                    <div style={{ fontSize:10, color:"#666", marginBottom:8, letterSpacing:"0.06em" }}>CASH TENDERED (KES)</div>
                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:4, marginBottom:8 }}>
                      {[50,100,200,500,1000,"Exact"].map(v => (
                        <button key={v} onClick={() => setCashInput(v === "Exact" ? total.toFixed(2) : String(v))}
                          style={{ background:"#161921", border:"1px solid #1e2128", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:12, padding:10, cursor:"pointer" }}>{v}</button>
                      ))}
                    </div>
                    <input className="ifield" type="number" placeholder="Enter amount..." value={cashInput} onChange={e => setCashInput(e.target.value)} />
                    {cashGiven > 0 && (
                      <div style={{ marginTop:8, padding:"10px 12px", background: change >= 0 ? "#0f2a1a" : "#2a0f0f", borderRadius:6, display:"flex", justifyContent:"space-between" }}>
                        <span style={{ fontSize:11, color:"#666" }}>CHANGE</span>
                        <span style={{ fontSize:14, color: change >= 0 ? "#22c55e" : "#ef4444", fontWeight:600 }}>{fmtKES(change)}</span>
                      </div>
                    )}
                  </div>
                )}

                {paymentMode === "mpesa" && (
                  <div style={{ marginBottom:16 }}>
                    <div style={{ fontSize:10, color:"#666", marginBottom:8, letterSpacing:"0.06em" }}>CUSTOMER PHONE</div>
                    <input className="ifield" placeholder="07XXXXXXXX" value={mpesaPhone} onChange={e => setMpesaPhone(e.target.value)} style={{ marginBottom:10 }} />
                    {mpesaStatus === null && receipt && (
                      <button onClick={handleMpesaPush} style={{ width:"100%", padding:12, background:"#16a34a", border:"none", borderRadius:6, color:"#fff", fontFamily:"inherit", fontSize:12, cursor:"pointer" }}>
                        SEND STK PUSH → {fmtKES(total)}
                      </button>
                    )}
                    {mpesaStatus === "waiting" && (
                      <div style={{ textAlign:"center", padding:14, background:"#0f1a2a", borderRadius:6 }}>
                        <div style={{ color:"#60a5fa", fontSize:12, marginBottom:4 }}>⏳ WAITING FOR PAYMENT...</div>
                        <div style={{ color:"#444", fontSize:10 }}>Customer should see M-PESA prompt</div>
                      </div>
                    )}
                    {mpesaStatus === "confirmed" && (
                      <div style={{ textAlign:"center", padding:12, background:"#0f2a1a", borderRadius:6, color:"#22c55e", fontSize:12 }}>✅ PAYMENT CONFIRMED</div>
                    )}
                    {mpesaStatus === "failed" && (
                      <div style={{ background:"#2a0f0f", border:"1px solid #ef4444", borderRadius:6, padding:"10px 12px" }}>
                        <div style={{ color:"#ef4444", fontSize:12, marginBottom:8 }}>❌ {mpesaFailMsg || "Payment failed"}</div>
                        <button onClick={() => { setMpesaStatus(null); setMpesaFailMsg(""); }}
                          style={{ background:"#1e2128", border:"1px solid #2a2d35", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:11, padding:"6px 12px", cursor:"pointer" }}>
                          Try again
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {paymentMode === "card" && (
                  <div style={{ marginBottom:16, padding:14, background:"#111316", borderRadius:6, textAlign:"center", color:"#666", fontSize:11 }}>
                    💳 SWIPE / TAP CARD ON READER
                  </div>
                )}

                <button className="cob" disabled={!canComplete} onClick={handleCompleteSale}>
                  {loading ? "PROCESSING..." : "COMPLETE SALE"}
                </button>
                <button onClick={clearCart}
                  style={{ width:"100%", marginTop:8, padding:10, background:"none", border:"1px solid #1e2128", borderRadius:6, color:"#444", fontFamily:"inherit", fontSize:11, cursor:"pointer", letterSpacing:"0.06em" }}>
                  VOID TRANSACTION
                </button>
              </>
            )}

            {/* Receipt */}
            {receipt && (
              <div style={{ background:"#111316", borderRadius:8, padding:16, border:"1px solid #1e2128" }}>
                <div style={{ textAlign:"center", marginBottom:12 }}>
                  <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:14, color:"#22c55e" }}>✓ SALE COMPLETE</div>
                  <div style={{ fontSize:10, color:"#555", marginTop:4 }}>{receipt.txn_number}</div>
                </div>
                {receipt.items?.map((i, idx) => (
                  <div key={i.id || idx} style={{ display:"flex", justifyContent:"space-between", padding:"4px 0", borderBottom:"1px dotted #2a2d35", fontSize:11 }}>
                    <span style={{ color:"#888" }}>{i.product_name || i.name} ×{i.qty}</span>
                    <span>{fmtKES(i.line_total || parseMoney(i.selling_price) * i.qty)}</span>
                  </div>
                ))}
                <div style={{ marginTop:10, display:"flex", justifyContent:"space-between", fontWeight:600, fontSize:13 }}>
                  <span>TOTAL</span><span style={{ color:"#f5a623" }}>{fmtKES(receipt.total)}</span>
                </div>
                <div style={{ marginTop:10, textAlign:"center", fontSize:10, color: receipt.etims_synced ? "#22c55e" : "#f5a623" }}>
                  {receipt.etims_synced ? "✓ KRA eTIMS VERIFIED" : "⏳ eTIMS PENDING SYNC"}
                </div>
                {receipt.sync_status === "local" && (
                  <div style={{ marginTop:6, textAlign:"center", fontSize:10, color:"#f5a623" }}>📶 SAVED OFFLINE — will sync when online</div>
                )}
                <button onClick={clearCart}
                  style={{ width:"100%", marginTop:14, padding:14, background:"#f5a623", border:"none", borderRadius:8, color:"#0a0c0f", fontFamily:"'Syne',sans-serif", fontWeight:700, fontSize:14, cursor:"pointer" }}>
                  NEW TRANSACTION
                </button>
              </div>
            )}

            {!receipt && cart.length === 0 && (
              <div style={{ textAlign:"center", paddingTop:40, color:"#2a2d35" }}>
                <div style={{ fontSize:32, marginBottom:8 }}>💳</div>
                <div style={{ fontSize:10, letterSpacing:"0.08em" }}>ADD ITEMS TO BEGIN</div>
              </div>
            )}
          </div>

          {/* Status bar */}
          <div style={{ padding:"5px 16px", borderTop:"1px solid #1e2128", fontSize:10, color:"#3a3d45", letterSpacing:"0.06em", display:"flex", justifyContent:"space-between", flexShrink:0 }}>
            <span>DUKAPOS v4.1.0</span>
            <span>{stats.failed > 0 ? `⚠ ${stats.failed} FAILED` : stats.pending > 0 ? `${stats.pending} QUEUED` : "eTIMS ACTIVE"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}