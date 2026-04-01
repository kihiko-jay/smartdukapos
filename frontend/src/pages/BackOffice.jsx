import { useState, useEffect } from "react";
import {
  transactionsAPI, productsAPI, reportsAPI, etimsAPI,
  auditAPI, getSession, clearSession, fmtKES, parseMoney
} from "../api/client";
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { useSubscription } from "../hooks/useSubscription";
import UpgradeWall from "../components/UpgradeWall";
import TrialBanner from "../components/TrialBanner";
import ProcurementTab from "./ProcurementTab";

const TABS = ["Overview","Inventory","Transactions","Reports","Procurement","Sync Monitor","Audit Trail"];

function KPICard({ label, value, sub, accent, delta }) {
  return (
    <div style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:10, padding:"20px 22px", borderLeft:`4px solid ${accent}`, position:"relative", overflow:"hidden" }}>
      <div style={{ fontSize:10, color:"#999", letterSpacing:"0.08em", textTransform:"uppercase", marginBottom:8, fontFamily:"'DM Mono',monospace" }}>{label}</div>
      <div style={{ fontSize:22, fontFamily:"'Syne',sans-serif", fontWeight:800, color:"#1a1a1a", lineHeight:1 }}>{value}</div>
      {sub&&<div style={{ fontSize:11, color:"#aaa", marginTop:6, fontFamily:"'DM Mono',monospace" }}>{sub}</div>}
      {delta&&<div style={{ position:"absolute", top:16, right:16, fontSize:11, fontWeight:600, color:delta.startsWith("+")?"#16a34a":"#dc2626", background:delta.startsWith("+")?"#f0fdf4":"#fef2f2", padding:"2px 8px", borderRadius:20, fontFamily:"'DM Mono',monospace" }}>{delta}</div>}
    </div>
  );
}

function OverviewTab() {
  const [summary,   setSummary]   = useState(null);
  const [weekly,    setWeekly]    = useState(null);
  const [topProds,  setTopProds]  = useState([]);
  const [txns,      setTxns]      = useState([]);
  const [lowStock,  setLowStock]  = useState([]);
  const [loading,   setLoading]   = useState(true);

  useEffect(() => {
    Promise.all([
      transactionsAPI.todaySummary(),
      reportsAPI.weekly(),
      reportsAPI.topProducts(),
      transactionsAPI.list({ limit:8 }),
      reportsAPI.lowStock(),
    ]).then(([s,w,tp,t,ls]) => {
      setSummary(s); setWeekly(w);
      setTopProds(tp.products||[]); setTxns(t); setLowStock(ls.items||[]);
    }).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ textAlign:"center", padding:60, color:"#aaa", fontFamily:"'DM Mono',monospace" }}>Loading dashboard...</div>;

  const days = weekly?.daily_breakdown || [];
  const methodColor = { mpesa:"#16a34a", cash:"#d97706", card:"#2563eb" };
  const byMethod    = summary?.by_payment_method || {};
  const totalByMethod = Object.values(byMethod).reduce((s,v)=>s+v,0) || 1;

  // Compute real vs-yesterday deltas from the weekly breakdown.
  // days[6] = today, days[5] = yesterday (both may have 0 if no sales yet).
  const todaySales     = days[6]?.total_sales       || 0;
  const yesterdaySales = days[5]?.total_sales       || 0;
  const todayTxns      = days[6]?.transaction_count || 0;
  const yesterdayTxns  = days[5]?.transaction_count || 0;

  function calcDelta(today, yesterday) {
    if (yesterday === 0) return null;           // can't compute — hide badge
    const pct = Math.round(((today - yesterday) / yesterday) * 100);
    return (pct >= 0 ? "+" : "") + pct + "% vs yday";
  }

  const salesDelta = calcDelta(todaySales, yesterdaySales);
  const txnsDelta  = calcDelta(todayTxns,  yesterdayTxns);

  return (
    <div style={{ display:"flex", flexDirection:"column", gap:24 }}>
      <div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:14 }}>
        <KPICard label="Today's Sales"   value={fmtKES(summary?.total_sales||0)}     sub="all tills"            accent="#f5a623" delta={salesDelta} />
        <KPICard label="Transactions"    value={summary?.transaction_count||0}        sub="completed today"      accent="#2563eb" delta={txnsDelta} />
        <KPICard label="Avg. Basket"     value={fmtKES(summary?.transaction_count ? Math.round((summary?.total_sales||0)/summary.transaction_count) : 0)} sub="per transaction" accent="#16a34a" />
        <KPICard label="Low Stock Items" value={lowStock.length}                      sub="require reorder"      accent="#dc2626" />
      </div>

      {summary?.unsynced_count > 0 && (
        <div style={{ background:"#fef3c7", border:"1px solid #fbbf24", borderRadius:8, padding:"12px 18px", display:"flex", alignItems:"center", gap:10, fontFamily:"'DM Mono',monospace", fontSize:12 }}>
          <span>⚠️</span>
          <span style={{ color:"#92400e" }}><strong>{summary.unsynced_count} transactions</strong> pending cloud sync. Check Sync Monitor tab.</span>
        </div>
      )}

      <div style={{ display:"grid", gridTemplateColumns:"1.6fr 1fr", gap:16 }}>
        <div style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:10, padding:20 }}>
          <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:15, marginBottom:14 }}>Weekly Sales</div>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={days} barSize={28}>
              <XAxis dataKey="day" tick={{ fontSize:10, fontFamily:"DM Mono", fill:"#aaa" }} axisLine={false} tickLine={false}/>
              <YAxis hide/>
              <Tooltip formatter={v=>[fmtKES(v),"Sales"]} contentStyle={{ background:"#1a1a1a", border:"none", borderRadius:6, fontFamily:"DM Mono", fontSize:11, color:"#fff" }} cursor={{ fill:"#fdf8f0" }}/>
              <Bar dataKey="total_sales" radius={[4,4,0,0]}>
                {days.map((_,i)=><Cell key={i} fill={i===days.length-1?"#f5a623":"#e8e3d8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:10, padding:20 }}>
          <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:15, marginBottom:14 }}>Payment Mix</div>
          {Object.entries(byMethod).map(([method, amount]) => {
            const pct = Math.round((amount/totalByMethod)*100);
            return (
              <div key={method} style={{ marginBottom:16 }}>
                <div style={{ display:"flex", justifyContent:"space-between", marginBottom:5 }}>
                  <span style={{ fontSize:12, fontWeight:600, color:"#333", fontFamily:"'DM Mono',monospace", textTransform:"uppercase" }}>{method}</span>
                  <span style={{ fontSize:11, color:"#888", fontFamily:"'DM Mono',monospace" }}>{pct}% · {fmtKES(amount)}</span>
                </div>
                <div style={{ height:6, background:"#f0ebe0", borderRadius:3 }}>
                  <div style={{ height:"100%", width:`${pct}%`, background:methodColor[method]||"#999", borderRadius:3 }}/>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Top Products */}
      <div style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:10, padding:20 }}>
        <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:15, marginBottom:14 }}>Top Products Today</div>
        <table style={{ width:"100%", borderCollapse:"collapse" }}>
          <thead><tr>{["Product","SKU","Units Sold","Revenue"].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"6px 10px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.08em", borderBottom:"1px solid #f0ebe0" }}>{h}</th>)}</tr></thead>
          <tbody>
            {topProds.slice(0,8).map(p=>(
              <tr key={p.product_id}>
                <td style={{ padding:"8px 10px", fontFamily:"'DM Mono',monospace", fontSize:12 }}>{p.product_name}</td>
                <td style={{ padding:"8px 10px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#aaa" }}>{p.sku}</td>
                <td style={{ padding:"8px 10px", fontFamily:"'DM Mono',monospace", fontSize:12 }}>{p.units_sold}</td>
                <td style={{ padding:"8px 10px", fontFamily:"'DM Mono',monospace", fontSize:12, color:"#c2820a", fontWeight:600 }}>{fmtKES(p.revenue)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Low stock */}
      {lowStock.length > 0 && (
        <div style={{ background:"#fff", border:"1px solid #fca5a5", borderRadius:10, padding:20 }}>
          <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:15, color:"#dc2626", marginBottom:14 }}>⚠ Low Stock Alert ({lowStock.length} items)</div>
          <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))", gap:10 }}>
            {lowStock.slice(0,8).map(p=>(
              <div key={p.product_id} style={{ background:p.status==="CRITICAL"?"#fef2f2":"#fffbeb", border:`1px solid ${p.status==="CRITICAL"?"#fca5a5":"#fde68a"}`, borderRadius:8, padding:12 }}>
                <div style={{ fontSize:12, fontWeight:600, fontFamily:"'DM Mono',monospace", marginBottom:4 }}>{p.name}</div>
                <div style={{ fontSize:11, color:"#aaa", fontFamily:"'DM Mono',monospace" }}>{p.sku}</div>
                <div style={{ marginTop:6, display:"flex", justifyContent:"space-between", fontSize:11, fontFamily:"'DM Mono',monospace" }}>
                  <span style={{ color:p.status==="CRITICAL"?"#dc2626":"#d97706", fontWeight:600 }}>
                    {p.status==="CRITICAL" ? "OUT OF STOCK" : `${p.current_stock} left`}
                  </span>
                  <span style={{ color:"#aaa" }}>min: {p.reorder_level}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InventoryTab() {
  const [products,   setProducts]   = useState([]);
  const [search,     setSearch]     = useState("");
  const [lowStock,   setLowStock]   = useState(false);
  const [loading,    setLoading]    = useState(false);
  const [editId,     setEditId]     = useState(null);
  const [editPrice,  setEditPrice]  = useState("");
  const [saving,     setSaving]     = useState(false);
  const [histProduct,setHistProduct]= useState(null);
  const [history,    setHistory]    = useState([]);

  useEffect(() => { loadProducts(); }, [search, lowStock]);

  const loadProducts = async () => {
    setLoading(true);
    try {
      const params = { limit:100 };
      if (search)   params.search    = search;
      if (lowStock) params.low_stock = true;
      setProducts(await productsAPI.list(params));
    } catch {} finally { setLoading(false); }
  };

  const savePrice = async (id) => {
    setSaving(true);
    try {
      await productsAPI.update(id, { selling_price: editPrice });
      setEditId(null);
      loadProducts();
    } catch(e) { alert(e.message); } finally { setSaving(false); }
  };

  const loadHistory = async (p) => {
    setHistProduct(p);
    try { setHistory(await productsAPI.stockHistory(p.id)); } catch {}
  };

  const movColor = { sale:"#dc2626", purchase:"#16a34a", adjustment:"#d97706", write_off:"#6b7280", void_restore:"#2563eb", sync:"#8b5cf6" };

  return (
    <div>
      <div style={{ display:"flex", gap:12, marginBottom:20, alignItems:"center" }}>
        <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search products..." style={{ flex:1, padding:"10px 14px", border:"1px solid #e8e3d8", borderRadius:6, fontFamily:"'DM Mono',monospace", fontSize:13 }}/>
        <label style={{ display:"flex", alignItems:"center", gap:6, fontFamily:"'DM Mono',monospace", fontSize:12, cursor:"pointer" }}>
          <input type="checkbox" checked={lowStock} onChange={e=>setLowStock(e.target.checked)}/> Low stock only
        </label>
      </div>

      {histProduct && (
        <div style={{ marginBottom:20, background:"#f9f5f0", border:"1px solid #e8e3d8", borderRadius:10, padding:20 }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:14 }}>
            <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:14 }}>Stock History: {histProduct.name}</div>
            <button onClick={()=>setHistProduct(null)} style={{ background:"none", border:"none", cursor:"pointer", color:"#aaa", fontSize:18 }}>✕</button>
          </div>
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead><tr>{["Date","Type","Delta","Before","After","Ref"].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"6px 10px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.06em", borderBottom:"1px solid #e8e3d8" }}>{h}</th>)}</tr></thead>
            <tbody>
              {history.map(m=>(
                <tr key={m.id}>
                  <td style={{ padding:"7px 10px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{new Date(m.created_at).toLocaleString("en-KE",{dateStyle:"short",timeStyle:"short"})}</td>
                  <td style={{ padding:"7px 10px" }}><span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:`${movColor[m.movement_type]||"#888"}18`, color:movColor[m.movement_type]||"#888", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>{m.movement_type}</span></td>
                  <td style={{ padding:"7px 10px", fontFamily:"'DM Mono',monospace", fontSize:12, color:m.qty_delta>0?"#16a34a":"#dc2626", fontWeight:600 }}>{m.qty_delta>0?"+":""}{m.qty_delta}</td>
                  <td style={{ padding:"7px 10px", fontFamily:"'DM Mono',monospace", fontSize:12, color:"#888" }}>{m.qty_before}</td>
                  <td style={{ padding:"7px 10px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:600 }}>{m.qty_after}</td>
                  <td style={{ padding:"7px 10px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#aaa" }}>{m.ref_id||"—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <table style={{ width:"100%", borderCollapse:"collapse", background:"#fff", borderRadius:10, overflow:"hidden", border:"1px solid #e8e3d8" }}>
        <thead><tr style={{ background:"#f9f5f0" }}>{["SKU","Product","Category","Price","Cost","Stock","Status",""].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"10px 14px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.08em" }}>{h}</th>)}</tr></thead>
        <tbody>
          {products.map(p=>(
            <tr key={p.id} style={{ borderTop:"1px solid #f0ebe0" }}>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{p.sku}</td>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:500 }}>{p.name}</td>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{p.category?.name||"—"}</td>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:12 }}>
                {editId===p.id
                  ? <div style={{ display:"flex", gap:6 }}>
                      <input value={editPrice} onChange={e=>setEditPrice(e.target.value)} style={{ width:80, padding:"4px 8px", border:"1px solid #f5a623", borderRadius:4, fontFamily:"inherit", fontSize:12 }}/>
                      <button onClick={()=>savePrice(p.id)} disabled={saving} style={{ background:"#f5a623", border:"none", borderRadius:4, padding:"4px 10px", cursor:"pointer", fontSize:11, fontFamily:"'Syne',sans-serif", fontWeight:700 }}>✓</button>
                      <button onClick={()=>setEditId(null)} style={{ background:"none", border:"1px solid #e8e3d8", borderRadius:4, padding:"4px 8px", cursor:"pointer", fontSize:11 }}>✕</button>
                    </div>
                  : <span onClick={()=>{setEditId(p.id);setEditPrice(parseMoney(p.selling_price).toFixed(2));}} style={{ cursor:"pointer", color:"#c2820a" }} title="Click to edit">{fmtKES(p.selling_price)}</span>
                }
              </td>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#aaa" }}>{p.cost_price ? fmtKES(p.cost_price) : "—"}</td>
              <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:12, color:p.is_low_stock?"#dc2626":"#1a1a1a", fontWeight:p.is_low_stock?600:400 }}>{p.stock_quantity}</td>
              <td style={{ padding:"10px 14px" }}>
                <span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:p.is_low_stock?"#fef2f2":"#f0fdf4", color:p.is_low_stock?"#dc2626":"#16a34a", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>
                  {p.stock_quantity===0?"OUT":p.is_low_stock?"LOW":"OK"}
                </span>
              </td>
              <td style={{ padding:"10px 14px" }}>
                <button onClick={()=>loadHistory(p)} style={{ background:"none", border:"1px solid #e8e3d8", borderRadius:4, padding:"3px 8px", cursor:"pointer", fontSize:10, fontFamily:"'DM Mono',monospace", color:"#888" }}>HISTORY</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TransactionsTab() {
  const [txns,    setTxns]    = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    transactionsAPI.list({ limit:50 })
      .then(setTxns).catch(console.error).finally(()=>setLoading(false));
  }, []);

  const statusColor = { completed:"#16a34a", pending:"#d97706", voided:"#6b7280", refunded:"#2563eb" };
  const syncColor   = { synced:"#16a34a", pending:"#d97706", failed:"#dc2626", local:"#8b5cf6" };

  return (
    <div>
      {loading ? <div style={{ textAlign:"center", padding:40, color:"#aaa" }}>Loading...</div> : (
        <table style={{ width:"100%", borderCollapse:"collapse", background:"#fff", borderRadius:10, overflow:"hidden", border:"1px solid #e8e3d8" }}>
          <thead><tr style={{ background:"#f9f5f0" }}>{["TXN Number","Total","Payment","Status","Cloud Sync","Date"].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"10px 14px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.08em" }}>{h}</th>)}</tr></thead>
          <tbody>
            {txns.map(t=>(
              <tr key={t.id} style={{ borderTop:"1px solid #f0ebe0" }}>
                <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:500, color:"#c2820a" }}>{t.txn_number}</td>
                <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:600 }}>{fmtKES(t.total)}</td>
                <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888", textTransform:"uppercase" }}>{t.payment_method}</td>
                <td style={{ padding:"10px 14px" }}>
                  <span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:`${statusColor[t.status]||"#888"}18`, color:statusColor[t.status]||"#888", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>{t.status}</span>
                </td>
                <td style={{ padding:"10px 14px" }}>
                  <span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:`${syncColor[t.sync_status]||"#888"}18`, color:syncColor[t.sync_status]||"#888", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>{t.sync_status||"pending"}</span>
                </td>
                <td style={{ padding:"10px 14px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{new Date(t.created_at).toLocaleString("en-KE",{dateStyle:"short",timeStyle:"short"})}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SyncMonitorTab() {
  const [syncLog, setSyncLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter,  setFilter]  = useState("all");

  useEffect(() => {
    auditAPI.syncLog({ limit:100 })
      .then(d=>setSyncLog(d?.entries||[]))
      .catch(console.error)
      .finally(()=>setLoading(false));
  }, []);

  const statusColor = { success:"#16a34a", error:"#dc2626", conflict:"#d97706", retry:"#d97706", skipped:"#6b7280" };
  const filtered = filter === "all" ? syncLog : syncLog.filter(e=>e.status===filter);

  return (
    <div>
      <div style={{ display:"flex", gap:16, marginBottom:20, alignItems:"center" }}>
        <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:16 }}>Sync Monitor</div>
        <div style={{ marginLeft:"auto", display:"flex", gap:6 }}>
          {["all","success","error","conflict"].map(f=>(
            <button key={f} onClick={()=>setFilter(f)}
              style={{ padding:"5px 12px", borderRadius:20, border:"1px solid #e8e3d8", background:filter===f?"#f5a623":"#fff", color:filter===f?"#fff":"#888", fontFamily:"'DM Mono',monospace", fontSize:11, cursor:"pointer" }}>
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Status cards */}
      <div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:12, marginBottom:20 }}>
        {["success","error","conflict","skipped"].map(s => {
          const count = syncLog.filter(e=>e.status===s).length;
          return (
            <div key={s} style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:8, padding:"14px 18px", borderLeft:`3px solid ${statusColor[s]}` }}>
              <div style={{ fontSize:10, color:"#aaa", fontFamily:"'DM Mono',monospace", letterSpacing:"0.08em", textTransform:"uppercase" }}>{s}</div>
              <div style={{ fontSize:24, fontFamily:"'Syne',sans-serif", fontWeight:800, color:statusColor[s], marginTop:4 }}>{count}</div>
            </div>
          );
        })}
      </div>

      {loading ? <div style={{ textAlign:"center", padding:40, color:"#aaa" }}>Loading sync log...</div> : (
        <table style={{ width:"100%", borderCollapse:"collapse", background:"#fff", borderRadius:10, overflow:"hidden", border:"1px solid #e8e3d8" }}>
          <thead><tr style={{ background:"#f9f5f0" }}>{["Entity","Direction","Status","Records In","Records Out","Duration","Checkpoint","Time"].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"10px 12px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.06em" }}>{h}</th>)}</tr></thead>
          <tbody>
            {filtered.map(e=>(
              <tr key={e.id} style={{ borderTop:"1px solid #f0ebe0" }}>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:500, color:"#c2820a" }}>{e.entity}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{e.direction}</td>
                <td style={{ padding:"9px 12px" }}>
                  <span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:`${statusColor[e.status]||"#888"}18`, color:statusColor[e.status]||"#888", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>{e.status}</span>
                </td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:12 }}>{e.records_in}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:12, color:"#16a34a" }}>{e.records_out}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{e.duration_ms ? `${e.duration_ms}ms` : "—"}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:10, color:"#aaa" }}>{e.checkpoint ? new Date(e.checkpoint).toLocaleString("en-KE",{dateStyle:"short",timeStyle:"short"}) : "—"}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888" }}>{new Date(e.synced_at).toLocaleString("en-KE",{dateStyle:"short",timeStyle:"short"})}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AuditTrailTab() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [entity,  setEntity]  = useState("all");

  useEffect(() => {
    const params = entity !== "all" ? { entity } : {};
    auditAPI.trail({ limit:100, ...params })
      .then(d=>setEntries(d?.entries||[]))
      .catch(console.error)
      .finally(()=>setLoading(false));
  }, [entity]);

  const actionColor = { create:"#16a34a", update:"#2563eb", void:"#dc2626", refund:"#d97706", stock_adj:"#8b5cf6", login:"#6b7280" };

  return (
    <div>
      <div style={{ display:"flex", gap:16, marginBottom:20, alignItems:"center" }}>
        <div style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:16 }}>Audit Trail</div>
        <div style={{ fontSize:12, color:"#aaa", fontFamily:"'DM Mono',monospace" }}>Append-only compliance log — KRA requirement</div>
        <div style={{ marginLeft:"auto", display:"flex", gap:6 }}>
          {["all","transaction","product","employee"].map(e=>(
            <button key={e} onClick={()=>setEntity(e)}
              style={{ padding:"5px 12px", borderRadius:20, border:"1px solid #e8e3d8", background:entity===e?"#1a1a1a":"#fff", color:entity===e?"#fff":"#888", fontFamily:"'DM Mono',monospace", fontSize:11, cursor:"pointer" }}>
              {e}
            </button>
          ))}
        </div>
      </div>

      {loading ? <div style={{ textAlign:"center", padding:40, color:"#aaa" }}>Loading audit trail...</div> : (
        <table style={{ width:"100%", borderCollapse:"collapse", background:"#fff", borderRadius:10, overflow:"hidden", border:"1px solid #e8e3d8" }}>
          <thead><tr style={{ background:"#f9f5f0" }}>{["Actor","Action","Entity","Entity ID","Before → After","Time"].map(h=><th key={h} style={{ textAlign:"left", fontSize:10, color:"#aaa", padding:"10px 12px", fontFamily:"'DM Mono',monospace", letterSpacing:"0.06em" }}>{h}</th>)}</tr></thead>
          <tbody>
            {entries.map(e=>(
              <tr key={e.id} style={{ borderTop:"1px solid #f0ebe0" }}>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:12, fontWeight:500 }}>{e.actor||"system"}</td>
                <td style={{ padding:"9px 12px" }}>
                  <span style={{ fontSize:10, padding:"2px 8px", borderRadius:20, background:`${actionColor[e.action]||"#888"}18`, color:actionColor[e.action]||"#888", fontFamily:"'DM Mono',monospace", fontWeight:600 }}>{e.action}</span>
                </td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888", textTransform:"uppercase" }}>{e.entity}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#c2820a" }}>{e.entity_id}</td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:10, color:"#888", maxWidth:280 }}>
                  {e.before && <span style={{ color:"#dc2626" }}>{JSON.stringify(e.before).slice(0,60)}</span>}
                  {e.before && e.after && <span style={{ color:"#aaa" }}> → </span>}
                  {e.after  && <span style={{ color:"#16a34a" }}>{JSON.stringify(e.after).slice(0,60)}</span>}
                  {e.notes  && <span style={{ color:"#aaa", display:"block", fontSize:10, marginTop:2 }}>{e.notes}</span>}
                </td>
                <td style={{ padding:"9px 12px", fontFamily:"'DM Mono',monospace", fontSize:11, color:"#888", whiteSpace:"nowrap" }}>{new Date(e.created_at).toLocaleString("en-KE",{dateStyle:"short",timeStyle:"short"})}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ReportsTab() {
  const [activeReport, setActiveReport] = useState("ztape");
  const [report,       setReport]       = useState(null);
  const [loading,      setLoading]      = useState(false);
  const [etimsPending, setEtimsPending] = useState(null);

  const loadReport = async (type) => {
    setLoading(true); setReport(null);
    try {
      switch(type) {
        case "ztape":  setReport(await reportsAPI.zTape()); break;
        case "weekly": setReport(await reportsAPI.weekly()); break;
        case "vat":    setReport(await reportsAPI.vat(new Date().getMonth()+1, new Date().getFullYear())); break;
      }
    } catch(e) { setReport({ error: e.message }); }
    finally { setLoading(false); }
  };

  const retryEtims = async () => {
    try { const r = await etimsAPI.retryAll(); alert(`eTIMS retry: ${r.synced} synced, ${r.failed} failed`); }
    catch(e) { alert(e.message); }
  };

  useEffect(() => {
    loadReport(activeReport);
    etimsAPI.pending().then(d=>setEtimsPending(d?.unsynced_count||0)).catch(()=>{});
  }, [activeReport]);

  return (
    <div>
      <div style={{ display:"flex", gap:8, marginBottom:20, flexWrap:"wrap" }}>
        {[["ztape","Z-Tape (End of Day)"],["weekly","Weekly Summary"],["vat","VAT Report (KRA)"]].map(([k,label])=>(
          <button key={k} onClick={()=>setActiveReport(k)}
            style={{ padding:"8px 18px", borderRadius:6, border:"1px solid", borderColor:activeReport===k?"#f5a623":"#e8e3d8", background:activeReport===k?"#f5a623":"#fff", color:activeReport===k?"#fff":"#888", fontFamily:"'DM Mono',monospace", fontSize:12, cursor:"pointer" }}>
            {label}
          </button>
        ))}
        {etimsPending > 0 && (
          <button onClick={retryEtims} style={{ marginLeft:"auto", padding:"8px 18px", borderRadius:6, border:"1px solid #f5a623", background:"#fffbeb", color:"#c2820a", fontFamily:"'DM Mono',monospace", fontSize:12, cursor:"pointer" }}>
            ⏳ Retry {etimsPending} eTIMS
          </button>
        )}
      </div>
      {loading && <div style={{ textAlign:"center", padding:40, color:"#aaa" }}>Loading report...</div>}
      {report && !report.error && (
        <div style={{ background:"#fff", border:"1px solid #e8e3d8", borderRadius:10, padding:24 }}>
          <pre style={{ fontFamily:"'DM Mono',monospace", fontSize:12, color:"#333", whiteSpace:"pre-wrap", lineHeight:1.8 }}>
            {JSON.stringify(report, null, 2)}
          </pre>
        </div>
      )}
      {report?.error && <div style={{ color:"#dc2626", fontFamily:"'DM Mono',monospace", fontSize:12 }}>{report.error}</div>}
    </div>
  );
}

export default function BackOffice({ onNavigate }) {
  const session = getSession();
  const { isPremium, isTrialing, daysLeft, planLabel } = useSubscription();
  const [activeTab, setActiveTab] = useState("Overview");
  const [showUpgrade, setShowUpgrade] = useState(false);

  const PREMIUM_TABS = ["Inventory","Reports","Procurement","Sync Monitor","Audit Trail"];
  const isLocked = (tab) => PREMIUM_TABS.includes(tab) && !isPremium;

  const handleTabClick = (tab) => {
    if (isLocked(tab)) { setShowUpgrade(true); return; }
    setActiveTab(tab);
  };

  const handleLogout = () => { clearSession(); onNavigate?.("login"); window.location.reload(); };

  return (
    <div style={{ minHeight:"100vh", background:"#f5f1e8", fontFamily:"'DM Mono',monospace" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');*{box-sizing:border-box}`}</style>
      {showUpgrade && <UpgradeWall feature={activeTab} isTrialing={isTrialing} daysLeft={daysLeft} onDismiss={()=>setShowUpgrade(false)}/>}

      {isTrialing && daysLeft !== null && (
        <TrialBanner daysLeft={daysLeft} onUpgrade={()=>setShowUpgrade(true)}/>
      )}

      {/* Top nav */}
      <div style={{ background:"#fff", borderBottom:"1px solid #e8e3d8", padding:"0 32px", display:"flex", alignItems:"center", height:60, gap:24 }}>
        <span style={{ fontFamily:"'Syne',sans-serif", fontWeight:800, fontSize:18 }}>DUKA<span style={{ color:"#f5a623" }}>POS</span></span>
        <span style={{ fontSize:11, color:"#aaa", padding:"3px 10px", background:"#f9f5f0", borderRadius:20 }}>{planLabel}</span>
        <nav style={{ display:"flex", gap:4, marginLeft:8 }}>
          {TABS.map(tab=>(
            <button key={tab} onClick={()=>handleTabClick(tab)}
              style={{ padding:"6px 14px", borderRadius:6, border:"none", background:activeTab===tab?"#1a1a1a":"transparent", color:activeTab===tab?"#fff":isLocked(tab)?"#ccc":"#555", fontFamily:"'DM Mono',monospace", fontSize:12, cursor:"pointer", position:"relative" }}>
              {tab}
              {isLocked(tab) && <span style={{ position:"absolute", top:-4, right:-4, fontSize:9 }}>🔒</span>}
            </button>
          ))}
        </nav>
        <div style={{ marginLeft:"auto", display:"flex", gap:12, alignItems:"center" }}>
          <button onClick={()=>onNavigate?.("pos")} style={{ padding:"6px 14px", borderRadius:6, border:"1px solid #e8e3d8", background:"#fff", color:"#555", fontFamily:"'DM Mono',monospace", fontSize:11, cursor:"pointer" }}>← POS Terminal</button>
          <span style={{ fontSize:12, color:"#888" }}>{session?.name}</span>
          <button onClick={handleLogout} style={{ padding:"6px 14px", borderRadius:6, border:"1px solid #e8e3d8", background:"#fff", color:"#888", fontFamily:"'DM Mono',monospace", fontSize:11, cursor:"pointer" }}>Logout</button>
        </div>
      </div>

      {/* Content */}
      <div style={{ maxWidth:1200, margin:"0 auto", padding:"28px 32px" }}>
        {activeTab === "Overview"      && <OverviewTab />}
        {activeTab === "Inventory"     && <InventoryTab />}
        {activeTab === "Transactions"  && <TransactionsTab />}
        {activeTab === "Reports"       && <ReportsTab />}
        {activeTab === "Procurement"   && <ProcurementTab />}
        {activeTab === "Sync Monitor"  && <SyncMonitorTab />}
        {activeTab === "Audit Trail"   && <AuditTrailTab />}
      </div>
    </div>
  );
}
