function transformCustomer(row) {
  return {
    legacy_id:      row.id,
    name:           row.name,
    phone:          row.phone   || null,
    email:          row.email   || null,
    loyalty_points: row.loyalty_points  ?? 0,
    credit_limit:   row.credit_limit?.toString()  || "0",
    credit_balance: row.credit_balance?.toString()|| "0",
    notes:          row.notes   || null,
    is_active:      row.is_active ?? true,
    created_at:     row.created_at,
    updated_at:     row.updated_at || row.created_at,
  };
}

module.exports = { transformCustomer };
