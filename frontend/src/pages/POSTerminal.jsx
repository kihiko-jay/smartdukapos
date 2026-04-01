/**
 * POSTerminal (v4.0)
 *
 * Fixes applied:
 *  1. IDEMPOTENCY KEY on every transaction — txn_number generated client-side
 *     and passed as Idempotency-Key header. Retrying a sale never creates a duplicate.
 *  2. MPESA FAILED events handled — cashier sees reason, can retry or switch method.
 *  3. OFFLINE QUEUE uses idempotency_key — same sale cannot be queued twice.
 *  4. getSession() replaced with proper async token-aware session read.
 *  5. RATE LIMIT (429) surfaces a user-friendly "please wait" message.
 *  6. eTIMS submit errors are caught per-transaction, never crash the receipt flow.
 *  7. Queue stats (pending/failed) shown in status bar via useOfflineQueue.stats.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { productsAPI, transactionsAPI, mpesaAPI, etimsAPI, fmtKES, parseMoney } from "../api/client";
import { useOfflineQueue } from "../hooks/useOfflineQueue";
import { useMpesaSocket } from "../hooks/useMpesaSocket";
import TitleBar from "../components/TitleBar";

const VAT = 0.16;

// Session reader — works in Electron and browser
async function readSession() {
  if (typeof window !== "undefined" && window.electron?.config) {
    return window.electron.config.get("session");
  }
  try { return JSON.parse(sessionStorage.getItem("dukapos_session")); }
  catch { return null; }
}

// Client-side txn number — stable across retries within the same sale
function generateTxnNumber() {
  const ts  = Date.now().toString(36).toUpperCase();
  const rnd = Math.random().toString(36).slice(2, 6).toUpperCase();
  return `TXN-${ts}-${rnd}`;
}

export default function POSTerminal({ onNavigate }) {
  const [session, setSession]       = useState(null);
  const { isOnline, queueLength, stats, enqueue, syncQueue } = useOfflineQueue();

  const [products,    setProducts]    = useState([]);
  const [categories,  setCategories]  = useState(["All"]);
  const [category,    setCategory]    = useState("All");
  const [search,      setSearch]      = useState("");
  const [cart,        setCart]        = useState([]);
  const [paymentMode, setPaymentMode] = useState(null);
  const [cashInput,   setCashInput]   = useState("");
  const [mpesaPhone,  setMpesaPhone]  = useState("07");
  const [mpesaStatus, setMpesaStatus] = useState(null);   // null | "waiting" | "confirmed" | "failed"
  const [mpesaFailMsg, setMpesaFailMsg] = useState("");
  const [receipt,     setReceipt]     = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState("");
  const txnKeyRef = useRef(null);  // idempotency key for the current sale attempt
  const searchRef = useRef(null);

  // Load session async
  useEffect(() => { readSession().then(setSession); }, []);

  // Real-time M-PESA via WebSocket
  const handlePaymentConfirmed = useCallback((confirmedTxnNumber, mpesaRef) => {
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

  useEffect(() => { loadProducts(); loadCategories(); searchRef.current?.focus(); }, []);
  useEffect(() => { if (isOnline && queueLength > 0) syncQueue(); }, [isOnline]);

  useEffect(() => {
    const params = {};
    if (search) params.search = search;
    loadProducts(params);
  }, [search]);

  const loadProducts = async (params = {}) => {
    try { setProducts(await productsAPI.list({ is_active: true, limit: 200, ...params })); }
    catch (e) { setError(e.message); }
  };

  const loadCategories = async () => {
    try {
      const cats = await productsAPI.categories();
      setCategories(["All", ...cats.map(c => c.name)]);
    } catch {}
  };

  const filtered = products.filter(p =>
    category === "All" || (p.category && p.category.name === category)
  );

  const subtotal  = cart.reduce((s, i) => s + parseMoney(i.selling_price) * i.qty, 0);
  const vatAmount = subtotal * VAT;
  const total     = subtotal + vatAmount;
  const cashGiven = parseFloat(cashInput) || 0;
  const change    = cashGiven - total;

  const addToCart  = (p) => setCart(prev => {
    const ex = prev.find(i => i.id === p.id);
    return ex ? prev.map(i => i.id === p.id ? { ...i, qty: i.qty + 1 } : i)
              : [...prev, { ...p, qty: 1 }];
  });
  const updateQty  = (id, d) => setCart(prev => prev.map(i => i.id === id ? { ...i, qty: Math.max(1, i.qty + d) } : i));
  const removeItem = (id)    => setCart(prev => prev.filter(i => i.id !== id));
  const clearCart  = ()      => {
    setCart([]); setPaymentMode(null); setCashInput(""); setMpesaPhone("07");
    setMpesaStatus(null); setMpesaFailMsg(""); setReceipt(null); setError("");
    txnKeyRef.current = null;
  };

  const handleMpesaPush = async () => {
    if (!receipt?.id) return;
    setMpesaStatus("waiting"); setError(""); setMpesaFailMsg("");
    try {
      await mpesaAPI.stkPush(mpesaPhone, total, receipt.txn_number);
      // confirmation arrives via WebSocket (handlePaymentConfirmed above)
    } catch (e) {
      setError(e.message);
      setMpesaStatus(null);
    }
  };

  const handleCompleteSale = async () => {
    if (loading) return;
    setLoading(true); setError("");

    // Generate stable idempotency key once per sale — NOT per retry
    if (!txnKeyRef.current) {
      txnKeyRef.current = generateTxnNumber();
    }
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
        // OFFLINE: queue with idempotency key — cannot be double-queued
        await enqueue(payload);
        setReceipt({
          txn_number:   idempotencyKey,
          total:        total.toFixed(2),
          items:        cart.map(i => ({ ...i, line_total: parseMoney(i.selling_price) * i.qty })),
          etims_synced: false,
          sync_status:  "local",
        });
      } else {
        // ONLINE: pass idempotency key as header — safe to retry on network error
        const txn = await transactionsAPI.create(payload, {
          headers: { "Idempotency-Key": idempotencyKey },
        });

        // eTIMS submit — fire and forget, never blocks receipt
        etimsAPI.submit(txn.id).catch(err => {
          console.warn("eTIMS submit failed (will retry in background):", err.message);
        });

        setReceipt(txn);
      }
    } catch (e) {
      // 429 = rate limited — surface human-readable message
      if (e.message?.includes("Too many")) {
        setError("Too many requests. Please wait a moment and try again.");
      } else {
        setError(e.message);
      }
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
    <div style={{ fontFamily:"'DM Mono','Courier New',monospace", background:"#0a0c0f", color:"#e8e4dc", minHeight:"100vh", display:"flex", flexDirection:"column", fontSize:13 }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Syne:wght@700;800&display=swap');*{box-sizing:border-box;margin:0;padding:0}::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#2a2d35;border-radius:2px}.pc{background:#111316;border:1px solid #1e2128;border-radius:6px;padding:12px;cursor:pointer;transition:all 0.12s}.pc:hover{background:#161921;border-color:#f5a623;transform:translateY(-1px)}.cb{padding:5px 14px;border-radius:20px;border:1px solid #2a2d35;background:transparent;color:#888;cursor:pointer;font-family:inherit;font-size:11px;letter-spacing:0.05em;transition:all 0.12s}.cb.active{background:#f5a623;border-color:#f5a623;color:#0a0c0f;font-weight:500}.pb{flex:1;padding:14px 8px;border-radius:8px;border:2px solid #1e2128;background:#111316;color:#e8e4dc;font-family:inherit;font-size:12px;cursor:pointer;transition:all 0.15s;letter-spacing:0.08em;text-transform:uppercase}.pb:hover{border-color:#f5a623;color:#f5a623}.pb.sel{background:#f5a623;border-color:#f5a623;color:#0a0c0f;font-weight:500}.cob{width:100%;padding:16px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-family:'Syne',sans-serif;font-size:15px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;cursor:pointer}.cob:disabled{background:#1e2128;color:#444;cursor:not-allowed}.ifield{width:100%;background:#161921;border:1px solid #2a2d35;border-radius:6px;color:#e8e4dc;font-family:inherit;font-size:14px;padding:10px 14px;outline:none}.ifield:focus{border-color:#f5a623}`}</style>

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
            <span style={{ color: isOnline ? "#22c55e" : "#ef4444", fontSize:11 }}>
              {isOnline ? "ONLINE" : "OFFLINE"}
            </span>
          </div>
          {queueLength > 0 && (
            <span style={{ background:"#f5a623", color:"#0a0c0f", fontSize:10, padding:"2px 8px", borderRadius:20, fontWeight:600 }}>
              ⏳ {stats.pending} QUEUED{stats.failed > 0 ? ` · ${stats.failed} FAILED` : ""}
            </span>
          )}
          {session?.role === "manager" || session?.role === "admin" ? (
            <button onClick={() => onNavigate?.("backoffice")}
              style={{ background:"#1e2128", border:"1px solid #2a2d35", borderRadius:6, color:"#888", fontFamily:"inherit", fontSize:11, padding:"4px 12px", cursor:"pointer" }}>
              BACK OFFICE →
            </button>
          ) : null}
          <span style={{ color:"#888", fontSize:11 }}>CASHIER: {session?.name?.toUpperCase()}</span>
        </div>
      </div>

      <div style={{ display:"flex", flex:1, overflow:"hidden" }}>
        {/* LEFT: Products */}
        <div style={{ flex:1, display:"flex", flexDirection:"column", overflow:"hidden", borderRight:"1px solid #1e2128" }}>
          <div style={{ padding:"14px 16px", borderBottom:"1px solid #1e2128", background:"#0d0f13" }}>
            <input ref={searchRef} className="ifield" placeholder="Search by name, SKU or barcode..." value={search} onChange={e=>setSearch(e.target.value)} style={{ marginBottom:10 }}/>
            <div style={{ display:"flex", gap:6, flexWrap:"wrap" }}>
              {categories.map(c=><button key={c} className={`cb${category===c?" active":""}`} onClick={()=>setCategory(c)}>{c}</button>)}
            </div>
          </div>
          <div style={{ flex:1, overflowY:"auto", padding:"12px 16px", display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))", gap:8, alignContent:"start" }}>
            {filtered.map(p=>(
              <div key={p.id} className="pc" onClick={()=>addToCart(p)}>
                <div style={{ width:"100%", height:48, background:"#1a1d24", borderRadius:4, marginBottom:8, display:"flex", alignItems:"center", justifyContent:"center", fontSize:20 }}>
                  {emojis[p.category?.name] || "🛒"}
                </div>
                <div style={{ fontSize:11, color:"#e8e4dc", lineHeight:1.4, marginBottom:4, fontWeight:500 }}>{p.name}</div>
                <div style={{ fontSize:10, color:"#666", marginBottom:6 }}>{p.sku}</div>
                <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
                  <span style={{ color:"#f5a623", fontSize:13, fontWeight:500 }}>KES {parseMoney(p.selling_price).toLocaleString()}</span>
                  <span style={{ fontSize:10, color:p.stock_quantity<20?"#ef4444":"#555" }}>×{p.stock_quantity}</span>
                </div>
              </div>
            ))}
            {filtered.length===0&&<div style={{ gridColumn:"1/-1", textAlign:"center", padding:40, color:"#444" }}>No products found</div>}
          </div>
        </div>

        {/* RIGHT: Cart + Checkout */}
        <div style={{ width:360, display:"flex", flexDirection:"column", overflow:"hidden", background:"#0d0f13" }}>
          <div style={{ padding:"12px 16px", borderBottom:"1px solid #1e2128", display:"flex", justifyContent:"space-between", alignItems:"center" }}>
            <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:700, fontSize:13, letterSpacing:"0.08em" }}>
              CART {cart.length>0&&<span style={{ color:"#f5a623" }}>({cart.length})</span>}
            </span>
            {!isOnline && <span style={{ fontSize:10, color:"#f5a623", background:"rgba(245,166,35,.1)", padding:"2px 8px", borderRadius:20 }}>OFFLINE MODE</span>}
          </div>
          <div style={{ flex:1, overflowY:"auto", padding:"8px 0" }}>
            {cart.length===0
              ? <div style={{ textAlign:"center", padding:"40px 20px", color:"#333" }}><div style={{ fontSize:28, marginBottom:8 }}>🛒</div><div style={{ fontSize:11, letterSpacing:"0.06em" }}>TAP ITEMS TO ADD</div></div>
              : cart.map(item=>(
                <div key={item.id} style={{ padding:"10px 16px", borderBottom:"1px solid #141720", display:"flex", alignItems:"center", gap:8 }}>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ fontSize:12, fontWeight:500, whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{item.name}</div>
                    <div style={{ fontSize:10, color:"#666", marginTop:2 }}>KES {parseMoney(item.selling_price).toLocaleString()} each</div>
                  </div>
                  <div style={{ display:"flex", alignItems:"center", gap:6 }}>
                    <button onClick={()=>updateQty(item.id,-1)} style={{ width:22, height:22, background:"#1a1d24", border:"1px solid #2a2d35", borderRadius:4, color:"#e8e4dc", cursor:"pointer", fontSize:14 }}>−</button>
                    <span style={{ width:20, textAlign:"center" }}>{item.qty}</span>
                    <button onClick={()=>updateQty(item.id,1)}  style={{ width:22, height:22, background:"#1a1d24", border:"1px solid #2a2d35", borderRadius:4, color:"#f5a623", cursor:"pointer", fontSize:14 }}>+</button>
                  </div>
                  <div style={{ width:70, textAlign:"right", fontSize:12, fontWeight:500 }}>KES {(parseMoney(item.selling_price)*item.qty).toLocaleString()}</div>
                  <button onClick={()=>removeItem(item.id)} style={{ background:"none", border:"none", color:"#444", cursor:"pointer", fontSize:14 }}>✕</button>
                </div>
              ))
            }
          </div>
          <div style={{ borderTop:"1px solid #1e2128", padding:"12px 16px", background:"#0a0c0f" }}>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:4, color:"#666", fontSize:11 }}><span>SUBTOTAL</span><span>{fmtKES(subtotal)}</span></div>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:10, color:"#666", fontSize:11 }}><span>VAT (16%)</span><span>{fmtKES(vatAmount)}</span></div>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:14 }}>
              <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:18 }}>TOTAL</span>
              <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:18, color:"#f5a623" }}>{fmtKES(total)}</span>
            </div>
            {error&&<div style={{ background:"#2a0f0f", border:"1px solid #ef4444", borderRadius:6, padding:"8px 12px", fontSize:11, color:"#ef4444", marginBottom:12 }}>{error}</div>}
            {cart.length>0&&!receipt&&(
              <>
                <div style={{ display:"flex", gap:6, marginBottom:12 }}>
                  {["cash","mpesa","card"].map(m=>(
                    <button key={m} className={`pb${paymentMode===m?" sel":""}`} onClick={()=>{setPaymentMode(m);setMpesaStatus(null);setMpesaFailMsg("");}}>
                      {m==="cash"?"💵 Cash":m==="mpesa"?"📱 M-PESA":"💳 Card"}
                    </button>
                  ))}
                </div>
                {paymentMode==="cash"&&(
                  <div style={{ marginBottom:12 }}>
                    <div style={{ fontSize:10, color:"#666", marginBottom:6, letterSpacing:"0.06em" }}>CASH TENDERED (KES)</div>
                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:4, marginBottom:8 }}>
                      {[50,100,200,500,1000,"Exact"].map(v=>(
                        <button key={v} onClick={()=>setCashInput(v==="Exact"?total.toFixed(2):String(v))}
                          style={{ background:"#161921", border:"1px solid #1e2128", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:12, padding:10, cursor:"pointer" }}>{v}</button>
                      ))}
                    </div>
                    <input className="ifield" type="number" placeholder="Enter amount..." value={cashInput} onChange={e=>setCashInput(e.target.value)}/>
                    {cashGiven>0&&<div style={{ marginTop:8, padding:"8px 10px", background:change>=0?"#0f2a1a":"#2a0f0f", borderRadius:6, display:"flex", justifyContent:"space-between" }}>
                      <span style={{ fontSize:11, color:"#666" }}>CHANGE</span>
                      <span style={{ fontSize:13, color:change>=0?"#22c55e":"#ef4444", fontWeight:500 }}>{fmtKES(change)}</span>
                    </div>}
                  </div>
                )}
                {paymentMode==="mpesa"&&(
                  <div style={{ marginBottom:12 }}>
                    <div style={{ fontSize:10, color:"#666", marginBottom:6, letterSpacing:"0.06em" }}>CUSTOMER PHONE</div>
                    <input className="ifield" placeholder="07XXXXXXXX" value={mpesaPhone} onChange={e=>setMpesaPhone(e.target.value)} style={{ marginBottom:8 }}/>
                    {mpesaStatus===null&&receipt&&<button onClick={handleMpesaPush} style={{ width:"100%", padding:12, background:"#16a34a", border:"none", borderRadius:6, color:"#fff", fontFamily:"inherit", fontSize:12, cursor:"pointer" }}>SEND STK PUSH → {fmtKES(total)}</button>}
                    {mpesaStatus==="waiting"&&(
                      <div style={{ textAlign:"center", padding:12, background:"#0f1a2a", borderRadius:6 }}>
                        <div style={{ color:"#60a5fa", fontSize:12, marginBottom:4 }}>⏳ WAITING FOR PAYMENT...</div>
                        <div style={{ color:"#444", fontSize:10 }}>Customer should see M-PESA prompt on their phone</div>
                      </div>
                    )}
                    {mpesaStatus==="confirmed"&&<div style={{ textAlign:"center", padding:12, background:"#0f2a1a", borderRadius:6, color:"#22c55e", fontSize:12 }}>✅ PAYMENT CONFIRMED</div>}
                    {mpesaStatus==="failed"&&(
                      <div style={{ background:"#2a0f0f", border:"1px solid #ef4444", borderRadius:6, padding:"10px 12px" }}>
                        <div style={{ color:"#ef4444", fontSize:12, marginBottom:6 }}>❌ {mpesaFailMsg || "Payment failed"}</div>
                        <button onClick={()=>{setMpesaStatus(null);setMpesaFailMsg("");}} style={{ background:"#1e2128", border:"1px solid #2a2d35", borderRadius:6, color:"#e8e4dc", fontFamily:"inherit", fontSize:11, padding:"6px 12px", cursor:"pointer" }}>
                          Try again
                        </button>
                      </div>
                    )}
                  </div>
                )}
                {paymentMode==="card"&&<div style={{ marginBottom:12, padding:12, background:"#111316", borderRadius:6, textAlign:"center", color:"#666", fontSize:11 }}>💳 SWIPE / TAP CARD ON READER</div>}
                <button className="cob" disabled={!canComplete} onClick={handleCompleteSale}>{loading?"PROCESSING...":"COMPLETE SALE"}</button>
                <button onClick={clearCart} style={{ width:"100%", marginTop:6, padding:10, background:"none", border:"1px solid #2a2d35", borderRadius:6, color:"#666", fontFamily:"inherit", fontSize:11, cursor:"pointer" }}>VOID TRANSACTION</button>
              </>
            )}
            {receipt&&(
              <div style={{ background:"#111316", borderRadius:8, padding:16, border:"1px solid #1e2128" }}>
                <div style={{ textAlign:"center", marginBottom:12 }}>
                  <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:14, color:"#f5a623" }}>RECEIPT</div>
                  <div style={{ fontSize:10, color:"#555", marginTop:2 }}>{receipt.txn_number}</div>
                </div>
                {receipt.items?.map((i,idx)=>(
                  <div key={i.id||idx} style={{ display:"flex", justifyContent:"space-between", padding:"3px 0", borderBottom:"1px dotted #2a2d35", fontSize:11 }}>
                    <span style={{ color:"#888" }}>{i.product_name||i.name} ×{i.qty}</span>
                    <span>{fmtKES(i.line_total||parseMoney(i.selling_price)*i.qty)}</span>
                  </div>
                ))}
                <div style={{ marginTop:8, display:"flex", justifyContent:"space-between", fontWeight:500 }}>
                  <span>TOTAL</span><span style={{ color:"#f5a623" }}>{fmtKES(receipt.total)}</span>
                </div>
                <div style={{ marginTop:10, display:"flex", flexDirection:"column", gap:4 }}>
                  <div style={{ textAlign:"center", fontSize:10, color: receipt.etims_synced?"#22c55e":"#f5a623" }}>
                    {receipt.etims_synced ? "✓ KRA eTIMS VERIFIED" : "⏳ eTIMS PENDING SYNC"}
                  </div>
                  {receipt.sync_status === "local" && (
                    <div style={{ textAlign:"center", fontSize:10, color:"#f5a623", background:"rgba(245,166,35,.08)", padding:"4px 8px", borderRadius:20 }}>
                      📶 SAVED OFFLINE — will sync when online
                    </div>
                  )}
                  {receipt.sync_status === "synced" && (
                    <div style={{ textAlign:"center", fontSize:10, color:"#22c55e" }}>☁ SYNCED TO CLOUD</div>
                  )}
                </div>
                <button onClick={clearCart} style={{ width:"100%", marginTop:12, padding:14, background:"#f5a623", border:"none", borderRadius:8, color:"#0a0c0f", fontFamily:"'Syne',sans-serif", fontWeight:700, fontSize:14, cursor:"pointer" }}>NEW TRANSACTION</button>
              </div>
            )}
          </div>
        </div>
      </div>
      <div style={{ background:"#0d0f13", borderTop:"1px solid #1e2128", padding:"5px 20px", display:"flex", justifyContent:"space-between", fontSize:10, color:"#444", letterSpacing:"0.06em" }}>
        <span>DUKAPOS v4.0.0</span>
        <span>{cart.length} ITEM(S) IN CART</span>
        <span>
          KRA eTIMS: ACTIVE
          {stats.failed > 0 ? ` · ⚠ ${stats.failed} FAILED SYNC` : ""}
          {stats.pending > 0 ? ` · ${stats.pending} QUEUED` : ""}
        </span>
      </div>
    </div>
  );
}
