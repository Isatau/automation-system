// Единый HTTP-клиент RestoCore. Все запросы к backend идут только через него.
// Базовый URL: VITE_API_BASE_URL (по умолчанию '' — same origin; dev-прокси Vite на 5173).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const TOKEN_KEY = 'restocore_token'; // sessionStorage: не переживает закрытие вкладки

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}

type Listener = () => void;
const listeners = new Set<Listener>();
export function onUnauthorized(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).slice(1).join('.')}: ${d.msg ?? 'ошибка'}`).join('; ');
    }
    if (typeof data?.error === 'string') return data.error;
  } catch {
    /* не JSON */
  }
  if (res.status === 401) return 'Сессия истекла или недействительна. Войдите снова.';
  if (res.status === 403) return 'Недостаточно прав для этой операции.';
  if (res.status === 409) return 'Конфликт: операция невозможна в текущем состоянии.';
  return `Ошибка сервера (${res.status})`;
}

async function request<T>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(opts.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  let body = opts.body;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.json);
  }

  let res: Response;
  try {
    res = await fetch(BASE + path, { ...opts, headers, body });
  } catch {
    throw new ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.');
  }

  if (res.status === 401 && getToken()) {
    setToken(null);
    listeners.forEach((fn) => fn());
  }
  if (!res.ok) throw new ApiError(res.status, await extractError(res));

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, json?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: 'POST', json, headers }),
  put: <T>(path: string, json?: unknown) => request<T>(path, { method: 'PUT', json }),
  patch: <T>(path: string, json?: unknown) => request<T>(path, { method: 'PATCH', json }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: async <T>(path: string, form: FormData): Promise<T> => {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(BASE + path, { method: 'POST', headers, body: form });
    if (res.status === 401 && getToken()) { setToken(null); listeners.forEach((fn) => fn()); }
    if (!res.ok) throw new ApiError(res.status, await extractError(res));
    return (await res.json()) as T;
  },
};

// Уникальный ключ идемпотентности для платежей/изменений заказов (backend проверяет Idempotency-Key).
export function idemKey(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
