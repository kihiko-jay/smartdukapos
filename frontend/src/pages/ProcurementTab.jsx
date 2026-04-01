/**
 * ProcurementTab — inbound inventory management
 *
 * Sub-screens:
 *   "pos"      — Purchase Orders list
 *   "po-new"   — Create / Edit PO
 *   "po-view"  — PO detail + status actions
 *   "grns"     — GRN list
 *   "grn-new"  — Receive Inventory (create GRN)
 *   "grn-view" — GRN detail (print-friendly)
 *   "invoices" — Invoice Matching
 */

import { useState, useEffect, useCallback } from "react";
import { procurementAPI, productsAPI } from "../api/client";

// ── Shared style tokens ────────────────────────────────────────────────────
const MONO  = "'DM Mono',monospace";
const SYNE  = "'Syne',sans-serif";
const SAND  = "#f5f1e8";
const BONE  = "#e8e3d8";
const INK   = "#1a1a1a";
const MUTED = "#888";
const AMBER = "#f5a623";
const GREEN = "#16a34a";
const RED   = "#dc2626";
const BLUE  = "#2563eb";

const STATUS_COLOR = {
  draft:              { bg:"#f9f5f0", fg:"#92400e" },
  submitted:          { bg:"#eff6ff", fg:BLUE },
  approved:           { bg:"#f0fdf4", fg:GREEN },
  partially_received: { bg:"#fefce8", fg:"#a16207" },
  fully_received:     { bg:"#f0fdf4", fg:GREEN },
  closed:             { bg:"#f1f5f9", fg:"#475569" },
  cancelled:          { bg:"#fef2f2", fg:RED },
  posted:             { bg:"#f0fdf4", fg:GREEN },
  unmatched:          { bg:"#fef2f2", fg:RED },
  partial:            { bg:"#fefce8", fg:"#a16207" },
  matched:            { bg:"#f0fdf4", fg:GREEN },
  disputed:           { bg:"#fef2f2", fg:RED },
};

function Badge({ status }) {
  const s = STATUS_COLOR[status] || { bg:"#f1f5f9", fg:"#555" };
  return (
    <span style={{ fontSize:10, fontWeight:600, padding:"2px 8px", borderRadius:20,
                   background:s.bg, color:s.fg, fontFamily:MONO, textTransform:"uppercase",
                   letterSpacing:"0.06em" }}>
      {status?.replace(/_/g," ")}
    </span>
  );
}

function Btn({ children, onClick, variant="primary", small, disabled, style={} }) {
  const base = { padding: small?"4px 12px":"8px 18px", borderRadius:6, fontFamily:MONO,
                 fontSize: small?11:12, cursor: disabled?"not-allowed":"pointer",
                 border:"none", fontWeight:500, opacity: disabled?0.5:1, ...style };
  const themes = {
    primary:  { background:INK, color:"#fff" },
    secondary:{ background:"#fff", color:INK, border:`1px solid ${BONE}` },
    danger:   { background:RED,   color:"#fff" },
    success:  { background:GREEN, color:"#fff" },
  };
  return <button onClick={disabled?undefined:onClick} style={{...base,...themes[variant]}}>{children}</button>;
}

function Input({ label, ...props }) {
  return (
    <div style={{ display:"flex", flexDirection:"column", gap:4 }}>
      {label && <label style={{ fontSize:11, color:MUTED, fontFamily:MONO }}>{label}</label>}
      <input style={{ padding:"7px 10px", borderRadius:6, border:`1px solid ${BONE}`,
                      fontFamily:MONO, fontSize:12, background:"#fff",
                      outline:"none", width:"100%" }} {...props}/>
    </div>
  );
}

function Select({ label, children, ...props }) {
  return (
    <div style={{ display:"flex", flexDirection:"column", gap:4 }}>
      {label && <label style={{ fontSize:11, color:MUTED, fontFamily:MONO }}>{label}</label>}
      <select style={{ padding:"7px 10px", borderRadius:6, border:`1px solid ${BONE}`,
                       fontFamily:MONO, fontSize:12, background:"#fff",
                       outline:"none", width:"100%" }} {...props}>
        {children}
      </select>
    </div>
  );
}

function Card({ children, style={} }) {
  return <div style={{ background:"#fff", border:`1px solid ${BONE}`, borderRadius:10,
                       padding:"20px 24px", ...style }}>{children}</div>;
}

function SectionHead({ title, sub, action }) {
  return (
    <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
                  marginBottom:20 }}>
      <div>
        <div style={{ fontFamily:SYNE, fontWeight:800, fontSize:20, color:INK }}>{title}</div>
        {sub && <div style={{ fontSize:11, color:MUTED, marginTop:3, fontFamily:MONO }}>{sub}</div>}
      </div>
      {action}
    </div>
  );
}

function Err({ msg }) {
  if (!msg) return null;
  return <div style={{ background:"#fef2f2", color:RED, border:`1px solid #fecaca`,
                       borderRadius:6, padding:"8px 12px", fontSize:12, fontFamily:MONO,
                       marginBottom:12 }}>{msg}</div>;
}

function Loading() {
  return <div style={{ textAlign:"center", padding:60, color:MUTED, fontFamily:MONO }}>Loading…</div>;
}

const UNIT_TYPES = ["unit","pack","box","carton","case","dozen","bale","sack","roll","other"];
const fmtKES = (v) => `KES ${parseFloat(v||0).toLocaleString("en-KE",{minimumFractionDigits:2})}`;
const today  = () => new Date().toISOString().slice(0,10);

// ── Purchase Orders List ───────────────────────────────────────────────────
function POList({ onNew, onView }) {
  const [pos, setPOs]   = useState([]);
  const [loading, setL] = useState(true);
  const [filter, setF]  = useState("all");

  const load = useCallback(() => {
    setL(true);
    const p = filter !== "all" ? { status: filter } : {};
    procurementAPI.listPOs(p)
      .then(setPOs).catch(console.error).finally(() => setL(false));
  }, [filter]);

  useEffect(load, [load]);

  if (loading) return <Loading/>;

  return (
    <div>
      <SectionHead title="Purchase Orders" sub={`${pos.length} orders`}
        action={<Btn onClick={onNew}>+ New PO</Btn>}/>

      <div style={{ display:"flex", gap:8, marginBottom:16 }}>
        {["all","draft","submitted","approved","partially_received","fully_received","cancelled"].map(s => (
          <button key={s} onClick={() => setF(s)}
            style={{ padding:"4px 12px", borderRadius:20, border:`1px solid ${BONE}`,
                     background: filter===s ? INK : "#fff",
                     color: filter===s ? "#fff" : INK,
                     fontFamily:MONO, fontSize:11, cursor:"pointer" }}>
            {s === "all" ? "All" : s.replace(/_/g," ")}
          </button>
        ))}
      </div>

      {pos.length === 0 ? (
        <Card><div style={{ textAlign:"center", color:MUTED, padding:40, fontFamily:MONO }}>No purchase orders yet. Create one to start ordering stock.</div></Card>
      ) : (
        <Card style={{ padding:0 }}>
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead>
              <tr style={{ background:SAND }}>
                {["PO Number","Supplier","Order Date","Expected","Items","Total","Status",""].map(h => (
                  <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10,
                                       fontFamily:MONO, color:MUTED, fontWeight:600,
                                       textTransform:"uppercase", letterSpacing:"0.06em",
                                       borderBottom:`1px solid ${BONE}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pos.map((po, i) => (
                <tr key={po.id} style={{ borderBottom: i<pos.length-1 ? `1px solid ${BONE}` : "none" }}>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12,
                               fontWeight:600, color:BLUE, cursor:"pointer" }}
                      onClick={() => onView(po.id)}>
                    {po.po_number}
                  </td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12 }}>{po.supplier_name}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{po.order_date}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{po.expected_date || "—"}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12 }}>{po.item_count}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{fmtKES(po.total_amount)}</td>
                  <td style={{ padding:"10px 14px" }}><Badge status={po.status}/></td>
                  <td style={{ padding:"10px 14px" }}>
                    <Btn small variant="secondary" onClick={() => onView(po.id)}>View</Btn>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ── PO Create / Edit ───────────────────────────────────────────────────────
function POForm({ poId, onBack, onSaved }) {
  const isEdit = !!poId;
  const [suppliers, setSuppliers] = useState([]);
  const [products,  setProducts]  = useState([]);
  const [form, setForm] = useState({
    supplier_id: "", expected_date: "", notes: "", currency: "KES", items: [],
  });
  const [saving, setSaving] = useState(false);
  const [err,    setErr]    = useState("");

  useEffect(() => {
    productsAPI.suppliers().then(r => setSuppliers(r.suppliers || r || [])).catch(console.error);
    productsAPI.list({ limit:200 }).then(r => setProducts(r.products || r || [])).catch(console.error);
    if (isEdit) {
      procurementAPI.getPO(poId).then(po => {
        setForm({
          supplier_id:   po.supplier_id,
          expected_date: po.expected_date || "",
          notes:         po.notes || "",
          currency:      po.currency,
          items: po.items.map(it => ({
            product_id:           it.product_id,
            ordered_qty_purchase: it.ordered_qty_purchase,
            purchase_unit_type:   it.purchase_unit_type,
            units_per_purchase:   it.units_per_purchase,
            unit_cost:            it.unit_cost,
            notes:                it.notes || "",
          })),
        });
      }).catch(console.error);
    }
  }, [isEdit, poId]);

  const addItem = () => setForm(f => ({
    ...f, items: [...f.items, {
      product_id:"", ordered_qty_purchase:"1", purchase_unit_type:"carton",
      units_per_purchase:"24", unit_cost:"0", notes:"",
    }],
  }));

  const removeItem = (i) => setForm(f => ({ ...f, items: f.items.filter((_,idx) => idx!==i) }));

  const updateItem = (i, field, val) => setForm(f => {
    const items = [...f.items];
    items[i] = { ...items[i], [field]: val };
    return { ...f, items };
  });

  const baseUnits = (item) => {
    const qty = parseFloat(item.ordered_qty_purchase) || 0;
    const upu = parseInt(item.units_per_purchase)     || 1;
    return Math.ceil(qty * upu);
  };

  const lineTotal = (item) => {
    return (baseUnits(item) * (parseFloat(item.unit_cost) || 0)).toFixed(2);
  };

  const grandTotal = () => form.items.reduce((s, it) => s + parseFloat(lineTotal(it)), 0).toFixed(2);

  const save = async () => {
    if (!form.supplier_id) { setErr("Select a supplier"); return; }
    if (form.items.length === 0) { setErr("Add at least one product"); return; }
    for (const it of form.items) {
      if (!it.product_id) { setErr("All items need a product"); return; }
      if (parseFloat(it.ordered_qty_purchase) <= 0) { setErr("Quantity must be > 0"); return; }
    }
    setSaving(true); setErr("");
    try {
      const payload = {
        ...form,
        supplier_id: parseInt(form.supplier_id),
        items: form.items.map(it => ({
          product_id:           parseInt(it.product_id),
          ordered_qty_purchase: parseFloat(it.ordered_qty_purchase),
          purchase_unit_type:   it.purchase_unit_type,
          units_per_purchase:   parseInt(it.units_per_purchase),
          unit_cost:            parseFloat(it.unit_cost),
          notes:                it.notes || undefined,
        })),
      };
      const result = isEdit
        ? await procurementAPI.updatePO(poId, payload)
        : await procurementAPI.createPO(payload);
      onSaved(result.id || poId);
    } catch(e) {
      setErr(e?.detail || e?.message || "Save failed");
    } finally { setSaving(false); }
  };

  return (
    <div>
      <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:24 }}>
        <Btn variant="secondary" onClick={onBack}>← Back</Btn>
        <span style={{ fontFamily:SYNE, fontWeight:800, fontSize:20 }}>
          {isEdit ? "Edit Purchase Order" : "New Purchase Order"}
        </span>
      </div>
      <Err msg={err}/>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:20 }}>
        <Card>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13, marginBottom:16 }}>Order Details</div>
          <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
            <Select label="Supplier *" value={form.supplier_id}
                    onChange={e => setForm(f=>({...f,supplier_id:e.target.value}))}>
              <option value="">— select supplier —</option>
              {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input label="Expected Delivery Date" type="date" value={form.expected_date}
                   onChange={e => setForm(f=>({...f,expected_date:e.target.value}))}/>
            <Input label="Notes" value={form.notes}
                   onChange={e => setForm(f=>({...f,notes:e.target.value}))}/>
          </div>
        </Card>
        <Card style={{ display:"flex", flexDirection:"column", justifyContent:"center", alignItems:"center", gap:8 }}>
          <div style={{ fontSize:11, color:MUTED, fontFamily:MONO }}>ORDER TOTAL</div>
          <div style={{ fontFamily:SYNE, fontWeight:800, fontSize:32, color:INK }}>{fmtKES(grandTotal())}</div>
          <div style={{ fontSize:11, color:MUTED, fontFamily:MONO }}>{form.items.length} line item{form.items.length!==1?"s":""}</div>
        </Card>
      </div>

      <Card>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:16 }}>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13 }}>Order Lines</div>
          <Btn small onClick={addItem}>+ Add Product</Btn>
        </div>

        {form.items.length === 0 ? (
          <div style={{ textAlign:"center", color:MUTED, padding:32, fontFamily:MONO, fontSize:12 }}>
            No items yet. Click "+ Add Product" to start.
          </div>
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead>
              <tr style={{ background:SAND }}>
                {["Product","Qty (Purchase Units)","Unit Type","Units/Purchase","Cost/Unit","Base Units","Line Total",""].map(h=>(
                  <th key={h} style={{ padding:"8px 10px", textAlign:"left", fontSize:10,
                                       fontFamily:MONO, color:MUTED, letterSpacing:"0.06em",
                                       textTransform:"uppercase", fontWeight:600,
                                       borderBottom:`1px solid ${BONE}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {form.items.map((item, i) => (
                <tr key={i} style={{ borderBottom:`1px solid ${BONE}` }}>
                  <td style={{ padding:"8px 10px", minWidth:160 }}>
                    <select value={item.product_id}
                            onChange={e => updateItem(i,"product_id",e.target.value)}
                            style={{ padding:"6px 8px", borderRadius:6, border:`1px solid ${BONE}`,
                                     fontFamily:MONO, fontSize:11, width:"100%" }}>
                      <option value="">— product —</option>
                      {products.map(p=><option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
                    </select>
                  </td>
                  <td style={{ padding:"8px 10px", width:90 }}>
                    <input type="number" min="0.001" step="0.001"
                           value={item.ordered_qty_purchase}
                           onChange={e => updateItem(i,"ordered_qty_purchase",e.target.value)}
                           style={{ padding:"6px 8px", borderRadius:6, border:`1px solid ${BONE}`,
                                    fontFamily:MONO, fontSize:11, width:"100%" }}/>
                  </td>
                  <td style={{ padding:"8px 10px", width:100 }}>
                    <select value={item.purchase_unit_type}
                            onChange={e => updateItem(i,"purchase_unit_type",e.target.value)}
                            style={{ padding:"6px 8px", borderRadius:6, border:`1px solid ${BONE}`,
                                     fontFamily:MONO, fontSize:11, width:"100%" }}>
                      {UNIT_TYPES.map(u=><option key={u} value={u}>{u}</option>)}
                    </select>
                  </td>
                  <td style={{ padding:"8px 10px", width:80 }}>
                    <input type="number" min="1" step="1"
                           value={item.units_per_purchase}
                           onChange={e => updateItem(i,"units_per_purchase",e.target.value)}
                           style={{ padding:"6px 8px", borderRadius:6, border:`1px solid ${BONE}`,
                                    fontFamily:MONO, fontSize:11, width:"100%" }}/>
                  </td>
                  <td style={{ padding:"8px 10px", width:100 }}>
                    <input type="number" min="0" step="0.01"
                           value={item.unit_cost}
                           onChange={e => updateItem(i,"unit_cost",e.target.value)}
                           style={{ padding:"6px 8px", borderRadius:6, border:`1px solid ${BONE}`,
                                    fontFamily:MONO, fontSize:11, width:"100%" }}/>
                  </td>
                  <td style={{ padding:"8px 10px", fontFamily:MONO, fontSize:12,
                               fontWeight:600, color:BLUE }}>
                    {baseUnits(item).toLocaleString()}
                  </td>
                  <td style={{ padding:"8px 10px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>
                    {fmtKES(lineTotal(item))}
                  </td>
                  <td style={{ padding:"8px 10px" }}>
                    <Btn small variant="danger" onClick={() => removeItem(i)}>✕</Btn>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div style={{ display:"flex", gap:12, marginTop:20, justifyContent:"flex-end" }}>
        <Btn variant="secondary" onClick={onBack}>Cancel</Btn>
        <Btn onClick={save} disabled={saving}>{saving ? "Saving…" : isEdit ? "Save Changes" : "Create PO"}</Btn>
      </div>
    </div>
  );
}

// ── PO Detail View ─────────────────────────────────────────────────────────
function POView({ poId, onBack, onCreateGRN }) {
  const [po,      setPO]   = useState(null);
  const [loading, setL]    = useState(true);
  const [acting,  setActing] = useState("");
  const [err,     setErr]  = useState("");

  useEffect(() => {
    setL(true);
    procurementAPI.getPO(poId).then(setPO).catch(console.error).finally(() => setL(false));
  }, [poId]);

  const action = async (fn, label) => {
    setActing(label); setErr("");
    try { const r = await fn(); setPO(r); }
    catch(e) { setErr(e?.detail || e?.message || `${label} failed`); }
    finally   { setActing(""); }
  };

  if (loading) return <Loading/>;
  if (!po)     return <div style={{ color:RED, fontFamily:MONO }}>PO not found</div>;

  const canSubmit  = po.status === "draft";
  const canApprove = po.status === "submitted";
  const canReceive = ["approved","partially_received"].includes(po.status);
  const canCancel  = !["fully_received","closed","cancelled"].includes(po.status);

  return (
    <div>
      <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:24 }}>
        <Btn variant="secondary" onClick={onBack}>← Back</Btn>
        <span style={{ fontFamily:SYNE, fontWeight:800, fontSize:20 }}>
          {po.po_number}
        </span>
        <Badge status={po.status}/>
      </div>
      <Err msg={err}/>

      <div style={{ display:"flex", gap:8, marginBottom:20, flexWrap:"wrap" }}>
        {canSubmit  && <Btn onClick={() => action(() => procurementAPI.submitPO(po.id),  "Submit")}  disabled={!!acting}>{acting==="Submit" ?"Submitting…" :"Submit PO"}</Btn>}
        {canApprove && <Btn variant="success" onClick={() => action(() => procurementAPI.approvePO(po.id), "Approve")} disabled={!!acting}>{acting==="Approve"?"Approving…" :"Approve PO"}</Btn>}
        {canReceive && <Btn variant="success" onClick={() => onCreateGRN(po.id)}>Receive Stock →</Btn>}
        {canCancel  && <Btn variant="danger"  onClick={() => action(() => procurementAPI.cancelPO(po.id),  "Cancel")}  disabled={!!acting}>{acting==="Cancel" ?"Cancelling…":"Cancel PO"}</Btn>}
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:16, marginBottom:20 }}>
        {[
          ["Supplier",  po.supplier_name],
          ["Order Date",po.order_date],
          ["Expected",  po.expected_date || "Not set"],
          ["Currency",  po.currency],
          ["Subtotal",  fmtKES(po.subtotal)],
          ["Total",     fmtKES(po.total_amount)],
        ].map(([k,v]) => (
          <Card key={k} style={{ padding:"14px 18px" }}>
            <div style={{ fontSize:10, color:MUTED, fontFamily:MONO, textTransform:"uppercase",
                          letterSpacing:"0.06em", marginBottom:6 }}>{k}</div>
            <div style={{ fontFamily:MONO, fontWeight:600, fontSize:14 }}>{v}</div>
          </Card>
        ))}
      </div>

      {po.notes && (
        <Card style={{ marginBottom:16, padding:"12px 18px" }}>
          <span style={{ fontSize:11, color:MUTED, fontFamily:MONO }}>Notes: </span>
          <span style={{ fontFamily:MONO, fontSize:12 }}>{po.notes}</span>
        </Card>
      )}

      <Card style={{ padding:0 }}>
        <table style={{ width:"100%", borderCollapse:"collapse" }}>
          <thead>
            <tr style={{ background:SAND }}>
              {["Product","SKU","Ordered","Unit Type","UPP","Ordered (base)","Received","Remaining","Unit Cost","Line Total"].map(h=>(
                <th key={h} style={{ padding:"10px 12px", textAlign:"left", fontSize:10,
                                     fontFamily:MONO, color:MUTED, fontWeight:600,
                                     textTransform:"uppercase", letterSpacing:"0.06em",
                                     borderBottom:`1px solid ${BONE}` }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {po.items.map((item, i) => {
              const pct = item.ordered_qty_base > 0
                ? Math.round((item.received_qty_base / item.ordered_qty_base) * 100) : 0;
              return (
                <tr key={item.id} style={{ borderBottom: i<po.items.length-1?`1px solid ${BONE}`:"none" }}>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{item.product_name}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11, color:MUTED }}>{item.product_sku}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12 }}>{item.ordered_qty_purchase}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11 }}>{item.purchase_unit_type}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11 }}>{item.units_per_purchase}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{item.ordered_qty_base.toLocaleString()}</td>
                  <td style={{ padding:"10px 12px" }}>
                    <div style={{ fontFamily:MONO, fontSize:12, fontWeight:600,
                                  color: pct>=100 ? GREEN : pct>0 ? AMBER : MUTED }}>
                      {item.received_qty_base.toLocaleString()}
                      <span style={{ fontSize:10, color:MUTED, marginLeft:4 }}>({pct}%)</span>
                    </div>
                  </td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12,
                               color: item.remaining_qty_base > 0 ? AMBER : GREEN }}>
                    {item.remaining_qty_base.toLocaleString()}
                  </td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12 }}>{fmtKES(item.unit_cost)}</td>
                  <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{fmtKES(item.line_total)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ── GRN List ───────────────────────────────────────────────────────────────
function GRNList({ onNew, onView }) {
  const [grns,    setGRNs] = useState([]);
  const [loading, setL]    = useState(true);

  useEffect(() => {
    procurementAPI.listGRNs({})
      .then(setGRNs).catch(console.error).finally(() => setL(false));
  }, []);

  if (loading) return <Loading/>;

  return (
    <div>
      <SectionHead title="Goods Received Notes" sub={`${grns.length} records`}
        action={<Btn onClick={() => onNew(null)}>+ Receive Stock</Btn>}/>

      {grns.length === 0 ? (
        <Card><div style={{ textAlign:"center", color:MUTED, padding:40, fontFamily:MONO }}>No GRNs yet. Receive stock against an approved PO or directly.</div></Card>
      ) : (
        <Card style={{ padding:0 }}>
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead>
              <tr style={{ background:SAND }}>
                {["GRN Number","Supplier","Linked PO","Received Date","Items","Status",""].map(h=>(
                  <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10,
                                       fontFamily:MONO, color:MUTED, fontWeight:600,
                                       textTransform:"uppercase", letterSpacing:"0.06em",
                                       borderBottom:`1px solid ${BONE}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grns.map((grn,i) => (
                <tr key={grn.id} style={{ borderBottom: i<grns.length-1?`1px solid ${BONE}`:"none" }}>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12,
                               fontWeight:600, color:BLUE, cursor:"pointer" }}
                      onClick={() => onView(grn.id)}>{grn.grn_number}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12 }}>{grn.supplier_name}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{grn.po_number || "—"}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{grn.received_date}</td>
                  <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12 }}>{grn.item_count}</td>
                  <td style={{ padding:"10px 14px" }}><Badge status={grn.status}/></td>
                  <td style={{ padding:"10px 14px" }}>
                    <Btn small variant="secondary" onClick={() => onView(grn.id)}>View</Btn>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ── Receive Inventory (GRN Create) ─────────────────────────────────────────
function GRNForm({ prefillPoId, onBack, onSaved }) {
  const [suppliers, setSuppliers] = useState([]);
  const [products,  setProducts]  = useState([]);
  const [po,        setPO]        = useState(null);
  const [form, setForm] = useState({
    supplier_id: "", purchase_order_id: prefillPoId || "",
    received_date: today(), supplier_invoice_number: "",
    supplier_delivery_note: "", notes: "", items: [],
  });
  const [saving, setSaving] = useState(false);
  const [err,    setErr]    = useState("");

  useEffect(() => {
    productsAPI.suppliers().then(r => setSuppliers(r.suppliers || r || [])).catch(console.error);
    productsAPI.list({ limit:200 }).then(r => setProducts(r.products || r || [])).catch(console.error);
  }, []);

  useEffect(() => {
    if (!form.purchase_order_id) { setPO(null); return; }
    procurementAPI.getPO(parseInt(form.purchase_order_id)).then(p => {
      setPO(p);
      setForm(f => ({
        ...f,
        supplier_id: p.supplier_id,
        items: p.items.filter(it => it.remaining_qty_base > 0).map(it => ({
          product_id:            it.product_id,
          product_name:          it.product_name,
          purchase_order_item_id:it.id,
          received_qty_purchase: "0",
          purchase_unit_type:    it.purchase_unit_type,
          units_per_purchase:    it.units_per_purchase,
          damaged_qty_base:      "0",
          rejected_qty_base:     "0",
          cost_per_base_unit:    it.unit_cost,
          batch_number:          "",
          expiry_date:           "",
          notes:                 "",
          _max_base:             it.remaining_qty_base,
        })),
      }));
    }).catch(console.error);
  }, [form.purchase_order_id]);

  const addItem = () => setForm(f => ({
    ...f, items: [...f.items, {
      product_id:"", product_name:"", purchase_order_item_id: null,
      received_qty_purchase:"0", purchase_unit_type:"carton",
      units_per_purchase:"24", damaged_qty_base:"0", rejected_qty_base:"0",
      cost_per_base_unit:"0", batch_number:"", expiry_date:"", notes:"", _max_base: null,
    }],
  }));

  const updateItem = (i, field, val) => setForm(f => {
    const items = [...f.items];
    items[i] = { ...items[i], [field]: val };
    if (field === "product_id") {
      const prod = products.find(p => String(p.id) === String(val));
      if (prod) items[i].product_name = prod.name;
    }
    return { ...f, items };
  });

  const removeItem = (i) => setForm(f => ({ ...f, items: f.items.filter((_,idx)=>idx!==i) }));

  const baseQty      = (it) => Math.ceil((parseFloat(it.received_qty_purchase)||0) * (parseInt(it.units_per_purchase)||1));
  const acceptedQty  = (it) => Math.max(0, baseQty(it) - (parseInt(it.damaged_qty_base)||0) - (parseInt(it.rejected_qty_base)||0));

  const save = async (andPost=false) => {
    if (!form.supplier_id) { setErr("Select a supplier"); return; }
    if (form.items.length === 0) { setErr("Add at least one product"); return; }
    for (const it of form.items) {
      if (!it.product_id) { setErr("All lines need a product"); return; }
      const dmg = parseInt(it.damaged_qty_base)||0;
      const rej = parseInt(it.rejected_qty_base)||0;
      if (dmg + rej > baseQty(it)) { setErr(`Damaged + rejected cannot exceed received qty for ${it.product_name || "a product"}`); return; }
    }
    setSaving(true); setErr("");
    try {
      const payload = {
        ...form,
        supplier_id:       parseInt(form.supplier_id),
        purchase_order_id: form.purchase_order_id ? parseInt(form.purchase_order_id) : null,
        items: form.items.map(it => ({
          product_id:             parseInt(it.product_id),
          purchase_order_item_id: it.purchase_order_item_id || null,
          received_qty_purchase:  parseFloat(it.received_qty_purchase)||0,
          purchase_unit_type:     it.purchase_unit_type,
          units_per_purchase:     parseInt(it.units_per_purchase)||1,
          damaged_qty_base:       parseInt(it.damaged_qty_base)||0,
          rejected_qty_base:      parseInt(it.rejected_qty_base)||0,
          cost_per_base_unit:     parseFloat(it.cost_per_base_unit)||0,
          batch_number:           it.batch_number || undefined,
          expiry_date:            it.expiry_date  || undefined,
          notes:                  it.notes        || undefined,
        })),
      };
      const grn = await procurementAPI.createGRN(payload);
      if (andPost) await procurementAPI.postGRN(grn.id);
      onSaved(grn.id);
    } catch(e) {
      setErr(e?.detail || e?.message || "Failed to save GRN");
    } finally { setSaving(false); }
  };

  return (
    <div>
      <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:24 }}>
        <Btn variant="secondary" onClick={onBack}>← Back</Btn>
        <span style={{ fontFamily:SYNE, fontWeight:800, fontSize:20 }}>Receive Inventory</span>
      </div>
      <Err msg={err}/>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:20 }}>
        <Card>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13, marginBottom:16 }}>Receiving Details</div>
          <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
            <Select label="Supplier *" value={form.supplier_id}
                    onChange={e => setForm(f=>({...f,supplier_id:e.target.value}))}>
              <option value="">— select supplier —</option>
              {suppliers.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input label="Received Date" type="date" value={form.received_date}
                   onChange={e => setForm(f=>({...f,received_date:e.target.value}))}/>
            <Input label="Supplier Invoice No." value={form.supplier_invoice_number}
                   onChange={e => setForm(f=>({...f,supplier_invoice_number:e.target.value}))}/>
            <Input label="Delivery Note No." value={form.supplier_delivery_note}
                   onChange={e => setForm(f=>({...f,supplier_delivery_note:e.target.value}))}/>
          </div>
        </Card>
        <Card>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13, marginBottom:16 }}>Link Purchase Order (optional)</div>
          <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
            <Input label="PO ID (leave blank for direct receive)"
                   type="number" value={form.purchase_order_id}
                   onChange={e => setForm(f=>({...f,purchase_order_id:e.target.value}))}/>
            {po && (
              <div style={{ background:SAND, borderRadius:6, padding:"10px 12px", fontSize:12, fontFamily:MONO }}>
                <span style={{ color:MUTED }}>Linked: </span>
                <strong>{po.po_number}</strong> — {po.supplier_name}
                <Badge status={po.status}/>
              </div>
            )}
            <Input label="Notes" value={form.notes}
                   onChange={e => setForm(f=>({...f,notes:e.target.value}))}/>
          </div>
        </Card>
      </div>

      <Card style={{ marginBottom:20 }}>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:16 }}>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13 }}>Products to Receive</div>
          <Btn small onClick={addItem}>+ Add Line</Btn>
        </div>

        {form.items.length === 0 ? (
          <div style={{ textAlign:"center", color:MUTED, padding:32, fontFamily:MONO, fontSize:12 }}>
            {po ? "All remaining PO items are added above." : "Click '+ Add Line' to add products."}
          </div>
        ) : (
          <div style={{ overflowX:"auto" }}>
            <table style={{ width:"100%", borderCollapse:"collapse", minWidth:900 }}>
              <thead>
                <tr style={{ background:SAND }}>
                  {["Product","Received","Unit Type","Units/Pkg","Base Units","Damaged","Rejected","Accepted","Cost/Unit","Batch","Expiry",""].map(h=>(
                    <th key={h} style={{ padding:"8px 8px", textAlign:"left", fontSize:10,
                                         fontFamily:MONO, color:MUTED, fontWeight:600,
                                         textTransform:"uppercase", letterSpacing:"0.06em",
                                         borderBottom:`1px solid ${BONE}`, whiteSpace:"nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {form.items.map((item, i) => {
                  const accepted = acceptedQty(item);
                  const base     = baseQty(item);
                  return (
                    <tr key={i} style={{ borderBottom:`1px solid ${BONE}` }}>
                      <td style={{ padding:"6px 8px", minWidth:140 }}>
                        {item.purchase_order_item_id ? (
                          <span style={{ fontFamily:MONO, fontSize:12, fontWeight:600 }}>{item.product_name}</span>
                        ) : (
                          <select value={item.product_id}
                                  onChange={e => updateItem(i,"product_id",e.target.value)}
                                  style={{ padding:"5px 7px", borderRadius:6, border:`1px solid ${BONE}`,
                                           fontFamily:MONO, fontSize:11, width:140 }}>
                            <option value="">— product —</option>
                            {products.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}
                          </select>
                        )}
                      </td>
                      <td style={{ padding:"6px 8px", width:70 }}>
                        <input type="number" min="0" step="0.001" value={item.received_qty_purchase}
                               onChange={e => updateItem(i,"received_qty_purchase",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                        fontFamily:MONO, fontSize:11, width:70 }}/>
                      </td>
                      <td style={{ padding:"6px 8px", width:90 }}>
                        <select value={item.purchase_unit_type}
                                onChange={e => updateItem(i,"purchase_unit_type",e.target.value)}
                                style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                         fontFamily:MONO, fontSize:11 }}>
                          {UNIT_TYPES.map(u=><option key={u} value={u}>{u}</option>)}
                        </select>
                      </td>
                      <td style={{ padding:"6px 8px", width:60 }}>
                        <input type="number" min="1" step="1" value={item.units_per_purchase}
                               onChange={e => updateItem(i,"units_per_purchase",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                        fontFamily:MONO, fontSize:11, width:60 }}/>
                      </td>
                      <td style={{ padding:"6px 8px", fontFamily:MONO, fontSize:12,
                                   fontWeight:600, color:BLUE, textAlign:"center" }}>
                        {base.toLocaleString()}
                      </td>
                      <td style={{ padding:"6px 8px", width:70 }}>
                        <input type="number" min="0" step="1" value={item.damaged_qty_base}
                               onChange={e => updateItem(i,"damaged_qty_base",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6,
                                        border:`1px solid ${AMBER}`, fontFamily:MONO,
                                        fontSize:11, width:70, background:"#fffbeb" }}/>
                      </td>
                      <td style={{ padding:"6px 8px", width:70 }}>
                        <input type="number" min="0" step="1" value={item.rejected_qty_base}
                               onChange={e => updateItem(i,"rejected_qty_base",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6,
                                        border:`1px solid #fca5a5`, fontFamily:MONO,
                                        fontSize:11, width:70, background:"#fef2f2" }}/>
                      </td>
                      <td style={{ padding:"6px 8px", fontFamily:MONO, fontSize:13,
                                   fontWeight:700, color: accepted>0 ? GREEN : RED,
                                   textAlign:"center" }}>
                        {accepted.toLocaleString()}
                      </td>
                      <td style={{ padding:"6px 8px", width:80 }}>
                        <input type="number" min="0" step="0.01" value={item.cost_per_base_unit}
                               onChange={e => updateItem(i,"cost_per_base_unit",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                        fontFamily:MONO, fontSize:11, width:80 }}/>
                      </td>
                      <td style={{ padding:"6px 8px", width:90 }}>
                        <input placeholder="batch" value={item.batch_number}
                               onChange={e => updateItem(i,"batch_number",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                        fontFamily:MONO, fontSize:11, width:90 }}/>
                      </td>
                      <td style={{ padding:"6px 8px", width:100 }}>
                        <input type="date" value={item.expiry_date}
                               onChange={e => updateItem(i,"expiry_date",e.target.value)}
                               style={{ padding:"5px 6px", borderRadius:6, border:`1px solid ${BONE}`,
                                        fontFamily:MONO, fontSize:11, width:100 }}/>
                      </td>
                      <td style={{ padding:"6px 8px" }}>
                        {!item.purchase_order_item_id &&
                          <Btn small variant="danger" onClick={() => removeItem(i)}>✕</Btn>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div style={{ background:"#fff", border:`1px solid ${BONE}`, borderRadius:10,
                    padding:"14px 24px", marginBottom:20 }}>
        <div style={{ fontFamily:MONO, fontSize:11, color:MUTED, marginBottom:4 }}>RECEIVING SUMMARY</div>
        <div style={{ display:"flex", gap:32 }}>
          <span style={{ fontFamily:MONO, fontSize:13 }}>
            Total received base units: <strong>
              {form.items.reduce((s,it) => s + baseQty(it), 0).toLocaleString()}
            </strong>
          </span>
          <span style={{ fontFamily:MONO, fontSize:13, color:GREEN }}>
            Total accepted: <strong>
              {form.items.reduce((s,it) => s + acceptedQty(it), 0).toLocaleString()}
            </strong>
          </span>
          <span style={{ fontFamily:MONO, fontSize:13, color:AMBER }}>
            Total damaged: <strong>
              {form.items.reduce((s,it) => s + (parseInt(it.damaged_qty_base)||0), 0).toLocaleString()}
            </strong>
          </span>
          <span style={{ fontFamily:MONO, fontSize:13, color:RED }}>
            Total rejected: <strong>
              {form.items.reduce((s,it) => s + (parseInt(it.rejected_qty_base)||0), 0).toLocaleString()}
            </strong>
          </span>
        </div>
      </div>

      <div style={{ display:"flex", gap:12, justifyContent:"flex-end" }}>
        <Btn variant="secondary" onClick={onBack}>Cancel</Btn>
        <Btn variant="secondary" onClick={() => save(false)} disabled={saving}>Save Draft</Btn>
        <Btn variant="success"   onClick={() => save(true)}  disabled={saving}>
          {saving ? "Posting…" : "Post GRN (Stock Updates Now)"}
        </Btn>
      </div>
    </div>
  );
}

// ── GRN View ───────────────────────────────────────────────────────────────
function GRNView({ grnId, onBack }) {
  const [grn,     setGRN] = useState(null);
  const [loading, setL]   = useState(true);
  const [acting,  setA]   = useState(false);
  const [err,     setErr] = useState("");

  useEffect(() => {
    procurementAPI.getGRN(grnId).then(setGRN).catch(console.error).finally(()=>setL(false));
  }, [grnId]);

  const post = async () => {
    setA(true); setErr("");
    try {
      const r = await procurementAPI.postGRN(grnId);
      setGRN(r);
    } catch(e) { setErr(e?.detail || e?.message || "Post failed"); }
    finally   { setA(false); }
  };

  if (loading) return <Loading/>;
  if (!grn)    return <div style={{ color:RED, fontFamily:MONO }}>GRN not found</div>;

  return (
    <div>
      <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:24 }}>
        <Btn variant="secondary" onClick={onBack}>← Back</Btn>
        <span style={{ fontFamily:SYNE, fontWeight:800, fontSize:20 }}>{grn.grn_number}</span>
        <Badge status={grn.status}/>
      </div>
      <Err msg={err}/>

      {grn.status === "draft" && (
        <div style={{ marginBottom:16, display:"flex", gap:8 }}>
          <Btn variant="success" onClick={post} disabled={acting}>
            {acting ? "Posting…" : "Post GRN — Update Stock"}
          </Btn>
        </div>
      )}
      {grn.posted_at && (
        <div style={{ background:"#f0fdf4", border:`1px solid #bbf7d0`, borderRadius:8,
                      padding:"10px 16px", marginBottom:16, fontFamily:MONO, fontSize:12, color:GREEN }}>
          ✓ Posted {new Date(grn.posted_at).toLocaleString("en-KE")} — stock has been updated
        </div>
      )}

      <div style={{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:14, marginBottom:20 }}>
        {[
          ["Supplier",       grn.supplier_name],
          ["Received Date",  grn.received_date],
          ["Linked PO",      grn.po_number || "Direct receive"],
          ["Invoice No.",    grn.supplier_invoice_number || "—"],
          ["Delivery Note",  grn.supplier_delivery_note  || "—"],
          ["Received By",    grn.received_by],
        ].map(([k,v]) => (
          <Card key={k} style={{ padding:"12px 16px" }}>
            <div style={{ fontSize:10, color:MUTED, fontFamily:MONO, textTransform:"uppercase",
                          letterSpacing:"0.06em", marginBottom:4 }}>{k}</div>
            <div style={{ fontFamily:MONO, fontWeight:600, fontSize:13 }}>{v}</div>
          </Card>
        ))}
      </div>

      <Card style={{ padding:0 }}>
        <table style={{ width:"100%", borderCollapse:"collapse" }}>
          <thead>
            <tr style={{ background:SAND }}>
              {["Product","SKU","Received","Unit","Base Rcvd","Damaged","Rejected","Accepted","Cost/Unit","Line Total","Batch","Expiry"].map(h=>(
                <th key={h} style={{ padding:"10px 12px", textAlign:"left", fontSize:10,
                                     fontFamily:MONO, color:MUTED, fontWeight:600,
                                     textTransform:"uppercase", letterSpacing:"0.06em",
                                     borderBottom:`1px solid ${BONE}` }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grn.items.map((item, i) => (
              <tr key={item.id} style={{ borderBottom: i<grn.items.length-1?`1px solid ${BONE}`:"none" }}>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{item.product_name}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11, color:MUTED }}>{item.product_sku}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12 }}>{item.received_qty_purchase} {item.purchase_unit_type}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11, color:MUTED }}>×{item.units_per_purchase}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{item.received_qty_base.toLocaleString()}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, color: item.damaged_qty_base>0?AMBER:MUTED }}>{item.damaged_qty_base}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, color: item.rejected_qty_base>0?RED:MUTED }}>{item.rejected_qty_base}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:13, fontWeight:700, color:GREEN }}>{item.accepted_qty_base.toLocaleString()}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12 }}>{fmtKES(item.cost_per_base_unit)}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{fmtKES(item.line_total)}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11, color:MUTED }}>{item.batch_number || "—"}</td>
                <td style={{ padding:"10px 12px", fontFamily:MONO, fontSize:11, color:MUTED }}>{item.expiry_date || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ── Invoice Matching ───────────────────────────────────────────────────────
function InvoiceMatchingScreen() {
  const [matches, setMatches] = useState([]);
  const [loading, setL]       = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ supplier_id:"", purchase_order_id:"", grn_id:"",
                                      invoice_number:"", invoice_date:"", invoice_total:"" });
  const [suppliers, setSuppliers] = useState([]);
  const [saving,    setSaving]    = useState(false);
  const [err,       setErr]       = useState("");

  const load = useCallback(() => {
    setL(true);
    procurementAPI.listMatches({}).then(setMatches).catch(console.error).finally(()=>setL(false));
  }, []);

  useEffect(load, [load]);
  useEffect(() => {
    productsAPI.suppliers().then(r => setSuppliers(r.suppliers || r || [])).catch(console.error);
  }, []);

  const submit = async () => {
    if (!form.supplier_id || !form.invoice_number || !form.invoice_total) {
      setErr("Supplier, invoice number, and total are required"); return;
    }
    setSaving(true); setErr("");
    try {
      await procurementAPI.createMatch({
        ...form,
        supplier_id:       parseInt(form.supplier_id),
        purchase_order_id: form.purchase_order_id ? parseInt(form.purchase_order_id) : null,
        grn_id:            form.grn_id            ? parseInt(form.grn_id)            : null,
        invoice_total:     parseFloat(form.invoice_total),
      });
      setShowForm(false);
      setForm({ supplier_id:"", purchase_order_id:"", grn_id:"", invoice_number:"", invoice_date:"", invoice_total:"" });
      load();
    } catch(e) { setErr(e?.detail || e?.message || "Failed"); }
    finally    { setSaving(false); }
  };

  const resolve = async (id, status) => {
    try {
      await procurementAPI.resolveMatch(id, { matched_status: status });
      load();
    } catch(e) { alert(e?.detail || "Resolve failed"); }
  };

  if (loading) return <Loading/>;

  return (
    <div>
      <SectionHead title="Invoice Matching" sub="Match supplier invoices to POs and GRNs"
        action={<Btn onClick={() => setShowForm(s => !s)}>{showForm ? "Cancel" : "+ Match Invoice"}</Btn>}/>

      {showForm && (
        <Card style={{ marginBottom:20 }}>
          <div style={{ fontWeight:700, fontFamily:MONO, fontSize:13, marginBottom:16 }}>New Invoice Match</div>
          <Err msg={err}/>
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:12, marginBottom:16 }}>
            <Select label="Supplier *" value={form.supplier_id}
                    onChange={e => setForm(f=>({...f,supplier_id:e.target.value}))}>
              <option value="">— select —</option>
              {suppliers.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input label="Invoice Number *" value={form.invoice_number}
                   onChange={e => setForm(f=>({...f,invoice_number:e.target.value}))}/>
            <Input label="Invoice Date" type="date" value={form.invoice_date}
                   onChange={e => setForm(f=>({...f,invoice_date:e.target.value}))}/>
            <Input label="Invoice Total (KES) *" type="number" value={form.invoice_total}
                   onChange={e => setForm(f=>({...f,invoice_total:e.target.value}))}/>
            <Input label="PO ID (optional)" type="number" value={form.purchase_order_id}
                   onChange={e => setForm(f=>({...f,purchase_order_id:e.target.value}))}/>
            <Input label="GRN ID (optional)" type="number" value={form.grn_id}
                   onChange={e => setForm(f=>({...f,grn_id:e.target.value}))}/>
          </div>
          <Btn onClick={submit} disabled={saving}>{saving?"Matching…":"Create Match"}</Btn>
        </Card>
      )}

      {matches.length === 0 ? (
        <Card><div style={{ textAlign:"center", color:MUTED, padding:40, fontFamily:MONO }}>No invoice matches yet.</div></Card>
      ) : (
        <Card style={{ padding:0 }}>
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead>
              <tr style={{ background:SAND }}>
                {["Invoice No.","Supplier","PO","GRN","Invoice Total","Status","Variance","Actions"].map(h=>(
                  <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10,
                                       fontFamily:MONO, color:MUTED, fontWeight:600,
                                       textTransform:"uppercase", letterSpacing:"0.06em",
                                       borderBottom:`1px solid ${BONE}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matches.map((m, i) => {
                let variance = null;
                try { variance = m.variance_json ? JSON.parse(m.variance_json) : null; } catch {}
                const hasDisc = variance?.has_discrepancy;
                return (
                  <tr key={m.id} style={{ borderBottom: i<matches.length-1?`1px solid ${BONE}`:"none" }}>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{m.invoice_number}</td>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12 }}>{m.supplier_name}</td>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{m.po_number  || "—"}</td>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:11, color:MUTED }}>{m.grn_number || "—"}</td>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12, fontWeight:600 }}>{fmtKES(m.invoice_total)}</td>
                    <td style={{ padding:"10px 14px" }}><Badge status={m.matched_status}/></td>
                    <td style={{ padding:"10px 14px", fontFamily:MONO, fontSize:12,
                                 color: hasDisc ? RED : GREEN }}>
                      {variance
                        ? (hasDisc
                            ? `⚠ ${fmtKES(Math.abs(variance.total_variance || 0))}`
                            : "✓ No variance")
                        : "—"}
                    </td>
                    <td style={{ padding:"10px 14px", display:"flex", gap:6 }}>
                      {m.matched_status !== "matched" && (
                        <Btn small variant="success" onClick={() => resolve(m.id,"matched")}>Mark Matched</Btn>
                      )}
                      {m.matched_status !== "disputed" && (
                        <Btn small variant="danger" onClick={() => resolve(m.id,"disputed")}>Dispute</Btn>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ── Root ProcurementTab ────────────────────────────────────────────────────
export default function ProcurementTab() {
  const [screen, setScreen] = useState("pos");   // pos | po-new | po-view | po-edit | grns | grn-new | grn-view | invoices
  const [activePoId,  setPoId]  = useState(null);
  const [activeGrnId, setGrnId] = useState(null);
  const [prefillPoId, setPrefillPoId] = useState(null);

  const NAV_ITEMS = [
    { key:"pos",      label:"Purchase Orders" },
    { key:"grns",     label:"Goods Received" },
    { key:"invoices", label:"Invoice Matching" },
  ];

  const topScreen = ["pos","grns","invoices"].includes(screen) ? screen : (
    ["po-new","po-view","po-edit"].includes(screen) ? "pos" : "grns"
  );

  return (
    <div>
      {/* Sub-nav */}
      <div style={{ display:"flex", gap:4, marginBottom:24, borderBottom:`1px solid ${BONE}`, paddingBottom:16 }}>
        {NAV_ITEMS.map(({ key, label }) => (
          <button key={key} onClick={() => setScreen(key)}
            style={{ padding:"6px 18px", borderRadius:6, border:"none",
                     background: topScreen===key ? "#1a1a1a" : "transparent",
                     color: topScreen===key ? "#fff" : "#555",
                     fontFamily:MONO, fontSize:12, cursor:"pointer" }}>
            {label}
          </button>
        ))}
      </div>

      {/* Screens */}
      {screen === "pos" && (
        <POList
          onNew   ={() => { setPoId(null); setScreen("po-new"); }}
          onView  ={(id) => { setPoId(id); setScreen("po-view"); }}
        />
      )}
      {screen === "po-new" && (
        <POForm
          poId    ={null}
          onBack  ={() => setScreen("pos")}
          onSaved ={(id) => { setPoId(id); setScreen("po-view"); }}
        />
      )}
      {screen === "po-edit" && (
        <POForm
          poId    ={activePoId}
          onBack  ={() => setScreen("po-view")}
          onSaved ={(id) => { setPoId(id); setScreen("po-view"); }}
        />
      )}
      {screen === "po-view" && (
        <POView
          poId        ={activePoId}
          onBack      ={() => setScreen("pos")}
          onCreateGRN ={(poId) => { setPrefillPoId(poId); setScreen("grn-new"); }}
        />
      )}
      {screen === "grns" && (
        <GRNList
          onNew ={(poId) => { setPrefillPoId(poId); setScreen("grn-new"); }}
          onView={(id)   => { setGrnId(id); setScreen("grn-view"); }}
        />
      )}
      {screen === "grn-new" && (
        <GRNForm
          prefillPoId={prefillPoId}
          onBack  ={() => setScreen(prefillPoId ? "po-view" : "grns")}
          onSaved ={(id) => { setGrnId(id); setPrefillPoId(null); setScreen("grn-view"); }}
        />
      )}
      {screen === "grn-view" && (
        <GRNView
          grnId  ={activeGrnId}
          onBack ={() => setScreen("grns")}
        />
      )}
      {screen === "invoices" && <InvoiceMatchingScreen/>}
    </div>
  );
}
