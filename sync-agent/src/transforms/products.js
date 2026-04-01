/**
 * Transform legacy products row → cloud schema.
 * Handles FLOAT → string for safe numeric transmission.
 */
function transformProduct(row) {
  return {
    legacy_id:      row.id,
    sku:            row.sku,
    barcode:        row.barcode || null,
    name:           row.name,
    description:    row.description || null,
    category_name:  row.category_name || null,   // cloud resolves to category_id
    selling_price:  row.selling_price?.toString() || "0",
    cost_price:     row.cost_price?.toString()    || null,
    vat_exempt:     row.vat_exempt ?? false,
    tax_code:       row.tax_code   || "B",
    stock_quantity: row.stock_quantity ?? 0,
    reorder_level:  row.reorder_level  ?? 10,
    unit:           row.unit || "piece",
    is_active:      row.is_active ?? true,
    image_url:      row.image_url || null,
    updated_at:     row.updated_at,
    created_at:     row.created_at,
  };
}

module.exports = { transformProduct };
