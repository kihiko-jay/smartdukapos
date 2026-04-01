/**
 * Transform legacy transactions rows → cloud schema.
 * Injects store_id which was missing in the legacy schema.
 */
function transformTransaction(row, items) {
  return {
    legacy_id:       row.id,
    txn_number:      row.txn_number,
    store_id:        parseInt(process.env.STORE_ID || "1"),   // injected by sync agent
    terminal_id:     row.terminal_id,
    subtotal:        row.subtotal?.toString()        || "0",
    discount_amount: row.discount_amount?.toString() || "0",
    vat_amount:      row.vat_amount?.toString()      || "0",
    total:           row.total?.toString()           || "0",
    payment_method:  row.payment_method,
    cash_tendered:   row.cash_tendered?.toString()   || null,
    change_given:    row.change_given?.toString()    || null,
    mpesa_ref:       row.mpesa_ref    || null,
    card_ref:        row.card_ref     || null,
    status:          row.status,
    etims_invoice_no:row.etims_invoice_no || null,
    etims_synced:    row.etims_synced  ?? false,
    cashier_id:      row.cashier_id    || null,
    customer_id:     row.customer_id   || null,
    created_at:      row.created_at,
    completed_at:    row.completed_at  || null,
    items:           (items || []).map(transformItem),
  };
}

function transformItem(item) {
  return {
    product_id:      item.product_id,
    product_name:    item.product_name,
    sku:             item.sku,
    qty:             item.qty,
    unit_price:      item.unit_price?.toString()  || "0",
    cost_price_snap: item.cost_price_snap?.toString() || null,
    discount:        item.discount?.toString()    || "0",
    line_total:      item.line_total?.toString()  || "0",
  };
}

module.exports = { transformTransaction };
