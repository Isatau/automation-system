// Методы API — строго подтверждённые маршруты из app/routers/*.py.
// Если эндпоинта нет в backend — здесь его нет.

import { api, idemKey } from './client';
import type {
  AuditListOut, DashboardData, HallOut, KitchenTicketOut, LoginResponse, MenuItemOut,
  MenuCategoryOut, NotificationOut, OrderOut, PaymentMethodOut, PaymentOut, ShiftOut,
} from '../types/api';

const V = '/api/v1';

export const authApi = {
  login: (login: string, password: string) =>
    api.post<LoginResponse>(`${V}/auth/login`, { login, password }),
  me: () => api.get<{ id: string; name: string; login: string; role: string; venue_id: string | null; discount_limit_pct: number; permissions: string[] }>(`${V}/auth/me`),
  logout: () => api.post(`${V}/auth/logout`),
};

export const dashboardApi = {
  get: () => api.get<DashboardData>(`${V}/dashboard`),
};

export const menuApi = {
  categories: () => api.get<MenuCategoryOut[]>(`${V}/menu/categories`),
  items: (categoryId?: string, q?: string) => {
    const p = new URLSearchParams();
    if (categoryId) p.set('category_id', categoryId);
    if (q) p.set('q', q);
    const s = p.toString();
    return api.get<MenuItemOut[]>(`${V}/menu/items${s ? '?' + s : ''}`);
  },
};

export const ordersApi = {
  list: (params?: { status?: string; table_id?: string; number?: number }) => {
    const p = new URLSearchParams();
    if (params?.status) p.set('status', params.status);
    if (params?.table_id) p.set('table_id', params.table_id);
    if (params?.number) p.set('number', String(params.number));
    const s = p.toString();
    return api.get<OrderOut[]>(`${V}/orders${s ? '?' + s : ''}`);
  },
  get: (id: string) => api.get<OrderOut>(`${V}/orders/${id}`),
  create: (body: { table_id?: string | null; register_id?: string | null; waiter_id?: string | null; comment?: string | null }) =>
    api.post<OrderOut>(`${V}/orders`, body),
  addItem: (oid: string, item_id: string, qty: number, comment?: string) =>
    api.post<OrderOut>(`${V}/orders/${oid}/items`, { item_id, qty, comment: comment || undefined },
      { 'Idempotency-Key': idemKey() }),
  patchItem: (itemId: string, body: { qty?: number; discount_pct?: number; comment?: string }) =>
    api.patch<OrderOut>(`${V}/orders/items/${itemId}`, body),
  deleteItem: (itemId: string, reason: string) =>
    api.del<OrderOut>(`${V}/orders/items/${itemId}?reason=${encodeURIComponent(reason)}`),
  send: (oid: string) => api.post<OrderOut>(`${V}/orders/${oid}/send`),
  cancel: (oid: string, reason: string) =>
    api.post(`${V}/orders/${oid}/cancel`, { reason }),
  close: (oid: string) => api.post<OrderOut>(`${V}/orders/${oid}/close`),
  prebill: (oid: string) => api.post(`${V}/orders/${oid}/prebill`),
  transfer: (oid: string, table_id: string) => api.post<OrderOut>(`${V}/orders/${oid}/transfer`, { table_id }),
  split: (oid: string, labels: string[]) => api.post<OrderOut>(`${V}/orders/${oid}/split`, { labels }),
};

export const paymentsApi = {
  methods: () => api.get<PaymentMethodOut[]>(`${V}/payment-methods`),
  pay: (oid: string, body: { method: string; amount: number; tip?: number; split_id?: string | null; guest_id?: string | null; certificate_code?: string | null; card_mask?: string | null }) =>
    api.post<PaymentOut>(`${V}/orders/${oid}/payments`, body, { 'Idempotency-Key': idemKey() }),
  orderPayments: (oid: string) => api.get<PaymentOut[]>(`${V}/orders/${oid}/payments`),
  refund: (pid: string, body: { amount: number; reason: string }) =>
    api.post(`${V}/payments/${pid}/refund`, body),
};

export const shiftsApi = {
  current: (registerId?: string) =>
    api.get<ShiftOut | null>(`${V}/shifts/current${registerId ? '?register_id=' + registerId : ''}`),
  list: (params?: { status?: string; date_from?: string; date_to?: string }) => {
    const p = new URLSearchParams();
    if (params?.status) p.set('status', params.status);
    if (params?.date_from) p.set('date_from', params.date_from);
    if (params?.date_to) p.set('date_to', params.date_to);
    const s = p.toString();
    return api.get<ShiftOut[]>(`${V}/shifts${s ? '?' + s : ''}`);
  },
  open: (register_id: string, initial_cash: number) =>
    api.post<ShiftOut>(`${V}/shifts/open`, { register_id, initial_cash }),
  cashIn: (sid: string, amount: number, reason: string) =>
    api.post<ShiftOut>(`${V}/shifts/${sid}/cash-in`, { amount, reason }),
  cashOut: (sid: string, amount: number, reason: string) =>
    api.post<ShiftOut>(`${V}/shifts/${sid}/cash-out`, { amount, reason }),
  summary: (sid: string) => api.get(`${V}/shifts/${sid}/summary`),
  close: (sid: string, actual_cash: number) =>
    api.post<ShiftOut>(`${V}/shifts/${sid}/close`, { actual_cash }),
};

export const hallApi = {
  halls: () => api.get<HallOut[]>(`${V}/halls`),
};

export const kitchenApi = {
  tickets: (stationId?: string, status?: string) => {
    const p = new URLSearchParams();
    if (stationId) p.set('station_id', stationId);
    if (status) p.set('status', status);
    const s = p.toString();
    return api.get<KitchenTicketOut[]>(`${V}/kitchen/tickets${s ? '?' + s : ''}`);
  },
  start: (tid: string) => api.post(`${V}/kitchen/tickets/${tid}/start`),
  done: (tid: string) => api.post(`${V}/kitchen/tickets/${tid}/done`),
  cancel: (tid: string, reason: string) => api.post(`${V}/kitchen/tickets/${tid}/cancel`, { reason }),
  priority: (tid: string, priority: string) => api.post(`${V}/kitchen/tickets/${tid}/priority`, { priority }),
};

export const reportsApi = {
  // GET /reports/{name}?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
  raw: (name: string, dateFrom?: string, dateTo?: string) => {
    const p = new URLSearchParams();
    if (dateFrom) p.set('date_from', dateFrom);
    if (dateTo) p.set('date_to', dateTo);
    const s = p.toString();
    return api.get<unknown>(`${V}/reports/${name}${s ? '?' + s : ''}`);
  },
};

export const auditApi = {
  list: (params: { event_type?: string; user_id?: string; object_type?: string; object_id?: string; q?: string; limit?: number; offset?: number }) => {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') p.set(k, String(v)); });
    const s = p.toString();
    return api.get<AuditListOut>(`${V}/audit${s ? '?' + s : ''}`);
  },
};

export const notificationsApi = {
  list: () => api.get<NotificationOut[]>(`${V}/notifications`),
  read: (nid: string) => api.post(`${V}/notifications/${nid}/read`),
  ack: (nid: string) => api.post(`${V}/notifications/${nid}/ack`),
};

export const adminApi = {
  users: () => api.get<Record<string, unknown>[]>(`${V}/admin/users`),
  sessions: () => api.get<Record<string, unknown>[]>(`${V}/admin/sessions`),
  backups: () => api.get<Record<string, unknown>[]>(`${V}/admin/backups`),
  runBackup: () => api.post<Record<string, unknown>>(`${V}/admin/backups/run`),
};
