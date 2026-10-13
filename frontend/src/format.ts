// Форматирование под казахстанские стандарты: ₸, дата DD.MM.YYYY, время 24ч (Asia/Almaty).

const kzt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const kzt0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${kzt.format(v)} ₸`;
}

export function fmtMoneyShort(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${kzt0.format(Math.round(v))} ₸`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(v);
}

function parseIso(s: string): Date {
  // backend отдаёт naive datetime по времени Астаны — парсим компоненты напрямую,
  // чтобы браузер не сдвигал отображаемое время локальным часовым поясом.
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    const [, Y, Mo, D, H, Mi, S] = m;
    return new Date(+Y, +Mo - 1, +D, +H, +Mi, S ? +S : 0);
  }
  return new Date(s);
}

export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return '—';
  const d = parseIso(s);
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  return parseIso(s).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function fmtTime(s: string | null | undefined): string {
  if (!s) return '—';
  return parseIso(s).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function daysAgoISO(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function fmtDuration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
}
