// Типы, соответствующие реальным ответам backend (app/routers/*.py).
// Ничего не выдумано: поля сверены с кодом роутеров RestoCore.

export interface AuthUser {
  id: string;
  name: string;
  login: string;
  role: string;
  venue_id: string | null;
  discount_limit_pct: number;
  permissions: string[];
}

export interface LoginResponse {
  token: string;
  user: { id: string; name: string; role: string; login: string };
}

// GET /api/v1/dashboard
export interface DashboardData {
  generated_at: string;
  today: {
    revenue: number;
    checks: number;
    avg_check: number;
    cash: number;
    noncash: number;
    orders_open: number;
    open_order_ids: string[];
    refunds_count: number;
    refunds_amount: number;
  };
  week_revenue: { date: string; revenue: number }[];
  by_category: { category: string; amount: number }[];
  top_items: { name: string; qty: number; amount: number }[];
  shifts_open: { id: string; register_id: string; user_id: string; opened: string; expected_cash: number }[];
  hall: { table_id: string; name: string; status: string; open_amount: number; waiter_id: string | null }[];
  low_stock: { product: string; qty: number; min: number; warehouse: string }[];
  late_kitchen_tickets: number;
  critical_events: { at: string; type: string; object: string; amount: number | null; reason: string | null }[];
  unread_critical_notifications: number;
  on_duty: { staff: string; since: string }[];
  finance_month: { revenue: number; expenses: number; note: string };
}

// GET /api/v1/menu/items (_item_out)
export interface MenuItemOut {
  id: string;
  name: string;
  article: string;
  category_id: string | null;
  description: string | null;
  photo_url: string | null;
  price: number;
  unit: string;
  station_id: string | null;
  track_stock: boolean;
  is_set: boolean;
  set_items: unknown[];
  archived: boolean;
  unavailable_until: string | null;
}

export interface MenuCategoryOut {
  id: string;
  name: string;
  parent_id: string | null;
  sort: number;
}

// GET /api/v1/orders/{id} (_order_out)
export interface OrderModifierOut { name: string; qty: number; price_add: number }
export interface OrderItemOut {
  id: string;
  item_id: string;
  name: string;
  qty: number;
  unit_price: number;
  mods_total: number;
  discount_pct: number;
  line_total: number;
  status: string;
  deleted: boolean;
  course: number;
  comment: string | null;
  split_id: string | null;
  modifiers: OrderModifierOut[];
}
export interface OrderSplitOut { id: string; label: string; share_amount: number; paid_amount: number }
export interface OrderOut {
  id: string;
  number: number;
  status: string;
  table_id: string | null;
  waiter_id: string | null;
  shift_id: string | null;
  venue_id: string | null;
  subtotal: number;
  discount_total: number;
  total: number;
  paid_amount: number;
  refund_amount: number;
  tip_amount: number;
  cost_amount: number;
  version: number;
  created_at: string;
  cancel_reason: string | null;
  comment: string | null;
  items?: OrderItemOut[];
  splits?: OrderSplitOut[];
}

// GET /api/v1/halls
export interface HallTableOut {
  id: string;
  name: string;
  seats: number;
  status: string;
  x: number | null;
  y: number | null;
  hourly: boolean;
  tariff: number;
  waiter_id: string | null;
  merged_group_id: string | null;
  open_order_id: string | null;
  open_order_number: number | null;
  open_order_total: number;
  open_minutes: number | null;
}
export interface HallOut {
  id: string;
  name: string;
  kind: string;
  tables: HallTableOut[];
}

// GET /api/v1/kitchen/tickets (_ticket_out)
export interface KitchenTicketOut {
  id: string;
  order_number: number | null;
  table_id: string | null;
  item_name: string;
  qty: number;
  comment: string | null;
  modifiers: string[];
  course: number;
  status: string;
  priority: string;
  station_id: string | null;
  sent_at: string;
  started_at: string | null;
  done_at?: string | null;
  age_min?: number;
  overdue?: boolean;
  reprint_count?: number;
}

// GET /api/v1/shifts/current, /shifts (_shift_out)
export interface ShiftSummaryOut {
  shift_id: string;
  status: string;
  opened_at: string;
  initial_cash: number;
  by_method: { method: string; count: number; amount: number; tip: number }[];
  refunds_total: number;
  paid_orders: number;
  cancelled_orders: number;
  expected_cash: number;
  [k: string]: unknown;
}
export interface ShiftOut {
  id: string;
  register_id: string;
  user_id: string;
  status: string;
  opened_at: string;
  closed_at: string | null;
  initial_cash: number;
  expected_cash: number | null;
  actual_cash: number | null;
  difference: number | null;
  reopen_count: number;
  summary?: ShiftSummaryOut;
}

// GET /api/v1/payments/payment-methods
export interface PaymentMethodOut { code: string; name: string; is_cash: boolean }

// POST /api/v1/payments/orders/{oid}/payments (_pay_out)
export interface PaymentOut {
  id: string;
  order_id: string;
  method: string;
  amount: number;
  tip: number;
  status: string;
  refunded: number;
  auth_code: string | null;
  card_mask: string | null;
  fiscal_state: string | null;
  created_at: string;
}

// GET /api/v1/audit (auditlog._e)
export interface AuditEventOut {
  id: string;
  at: string | null;
  user_id: string | null;
  user_role: string | null;
  event_type: string;
  object_type: string | null;
  object_id: string | null;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  amount: number | null;
  reason: string | null;
  approved_by: string | null;
  register_id: string | null;
  workstation: string | null;
}
export interface AuditListOut { total: number; items: AuditEventOut[] }

// GET /api/v1/reports/* — сервер возвращает разные формы; описываем общие.
export type ReportRows = Record<string, unknown>[];
export interface ReportTable {
  columns?: { key: string; label: string }[];
  rows?: ReportRows;
  total?: number;
  [k: string]: unknown;
}

// GET /api/v1/notifications
export interface NotificationOut {
  id: string;
  at?: string;
  created_at?: string;
  title?: string;
  body?: string;
  message?: string;
  severity?: string;
  status?: string;
  event_type?: string;
  object_type?: string;
  object_id?: string;
  [k: string]: unknown;
}
