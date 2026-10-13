// Переиспользуемые UI-компоненты единой дизайн-системы (ТЗ §4.3, §13).

import React, { useEffect, useRef, useState } from 'react';

/* ---------- Индикатор загрузки ---------- */
export function Loading({ text = 'Загрузка…' }: { text?: string }) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <div className="spinner" />
      <div>{text}</div>
    </div>
  );
}

/* ---------- Пустое состояние ---------- */
export function EmptyState({ title = 'Нет данных', hint, action }:
  { title?: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="state-block">
      <div className="big-icon" aria-hidden>▤</div>
      <div>
        <div style={{ fontWeight: 600, color: 'var(--text)' }}>{title}</div>
        {hint && <div className="small mt" style={{ marginTop: 4 }}>{hint}</div>}
      </div>
      {action}
    </div>
  );
}

/* ---------- Ошибка + повтор ---------- */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <span aria-hidden>⚠</span>
      <span style={{ flex: 1 }}>{message}</span>
      {onRetry && <button className="btn sm" onClick={onRetry}>Повторить</button>}
    </div>
  );
}

/* ---------- Карточка показателя ---------- */
export function KpiCard({ label, value, sub, accent, onClick }:
  { label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: 'green' | 'red' | 'amber' | 'violet'; onClick?: () => void }) {
  const color = accent === 'green' ? 'var(--success)' : accent === 'red' ? 'var(--danger)'
    : accent === 'amber' ? 'var(--warning)' : accent === 'violet' ? 'var(--accent)' : undefined;
  return (
    <div className="card kpi" onClick={onClick} style={onClick ? { cursor: 'pointer' } : undefined}
         role={onClick ? 'button' : undefined} tabIndex={onClick ? 0 : undefined}
         onKeyDown={onClick ? (e) => { if (e.key === 'Enter') onClick(); } : undefined}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

/* ---------- Метка статуса ---------- */
const STATUS_MAP: Record<string, { cls: string; label: string }> = {
  // заказы
  created: { cls: 'gray', label: 'Создан' },
  open: { cls: 'blue', label: 'Открыт' },
  sent: { cls: 'violet', label: 'На кухне' },
  cooking: { cls: 'amber', label: 'Готовится' },
  ready: { cls: 'green', label: 'Готов' },
  awaiting_payment: { cls: 'amber', label: 'Ожидает оплаты' },
  paid: { cls: 'green', label: 'Оплачен' },
  closed: { cls: 'gray', label: 'Закрыт' },
  cancelled: { cls: 'red', label: 'Отменён' },
  // столы
  free: { cls: 'gray', label: 'Свободен' },
  occupied: { cls: 'amber', label: 'Занят' },
  reserved: { cls: 'violet', label: 'Бронь' },
  clean: { cls: 'green', label: 'Уборка' },
  blocked: { cls: 'red', label: 'Заблокирован' },
  // смены/платежи/документы
  draft: { cls: 'gray', label: 'Черновик' },
  pending: { cls: 'amber', label: 'Ожидает' },
  in_approval: { cls: 'violet', label: 'На согласовании' },
  approved: { cls: 'blue', label: 'Утверждён' },
  posted: { cls: 'green', label: 'Проведён' },
  confirmed: { cls: 'green', label: 'Подтверждён' },
  rejected: { cls: 'red', label: 'Отклонён' },
  failed: { cls: 'red', label: 'Ошибка' },
  new: { cls: 'blue', label: 'Новое' },
  read: { cls: 'gray', label: 'Прочитано' },
  acked: { cls: 'green', label: 'Принято' },
};
export function StatusBadge({ status }: { status: string }) {
  const s = STATUS_MAP[status] ?? { cls: 'gray', label: status };
  return <span className={`badge ${s.cls}`}>{s.label}</span>;
}

/* ---------- Модальное окно ---------- */
export function Modal({ title, onClose, children, footer, wide }:
  { title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal${wide ? ' wide' : ''}`} ref={ref} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- Подтверждение с обязательной причиной ---------- */
export function ConfirmDialog({ title, message, confirmLabel = 'Подтвердить', danger, requireReason, reasonLabel = 'Причина', busy, onConfirm, onCancel }:
  { title: string; message?: string; confirmLabel?: string; danger?: boolean;
    requireReason?: boolean; reasonLabel?: string; busy?: boolean;
    onConfirm: (reason: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
  const invalid = !!requireReason && reason.trim().length < 3;
  return (
    <Modal title={title} onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>Отмена</button>
          <button className={`btn ${danger ? 'danger-solid' : 'primary'}`} disabled={busy || invalid}
            onClick={() => { setTouched(true); if (!invalid) onConfirm(reason.trim()); }}>
            {busy ? 'Выполняется…' : confirmLabel}
          </button>
        </>
      }>
      {message && <p style={{ marginTop: 0 }} className="muted">{message}</p>}
      {requireReason && (
        <div className="field">
          <label htmlFor="confirm-reason">{reasonLabel} <span style={{ color: 'var(--danger)' }}>*</span></label>
          <textarea id="confirm-reason" className={`input${touched && invalid ? ' invalid' : ''}`} rows={2}
            value={reason} onChange={(e) => setReason(e.target.value)} onBlur={() => setTouched(true)} />
          {touched && invalid && <div className="form-error">Укажите причину (минимум 3 символа)</div>}
        </div>
      )}
    </Modal>
  );
}

/* ---------- Toast ---------- */
export interface ToastMsg { id: number; text: string; kind: 'ok' | 'err' | 'info' }
let toastSeq = 1;
const toastListeners = new Set<(t: ToastMsg) => void>();
export function toast(text: string, kind: ToastMsg['kind'] = 'info') {
  toastListeners.forEach((fn) => fn({ id: toastSeq++, text, kind }));
}
export function ToastHost() {
  const [items, setItems] = useState<ToastMsg[]>([]);
  useEffect(() => {
    const fn = (t: ToastMsg) => {
      setItems((prev) => [...prev, t]);
      setTimeout(() => setItems((prev) => prev.filter((i) => i.id !== t.id)), 4500);
    };
    toastListeners.add(fn);
    return () => { toastListeners.delete(fn); };
  }, []);
  if (!items.length) return null;
  return (
    <div className="toast-host" aria-live="polite">
      {items.map((t) => <div key={t.id} className={`toast ${t.kind === 'err' ? 'err' : t.kind === 'ok' ? 'ok' : ''}`}>{t.text}</div>)}
    </div>
  );
}

/* ---------- Хук загрузки данных с состояниями ---------- */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]):
  { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn); fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    fnRef.current()
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e: Error) => { if (alive) setError(e.message || 'Ошибка загрузки'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
