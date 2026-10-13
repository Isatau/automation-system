// Универсальная таблица: поиск, сортировка, пагинация по уже загруженным данным.
// ВАЖНО: это клиентская фильтрация — она не выдаётся за серверную.

import React, { useMemo, useState } from 'react';
import { EmptyState } from './ui';
import { fmtMoney, fmtNum } from '../format';

export interface Column<T> {
  key: keyof T & string;
  label: string;
  type?: 'text' | 'num' | 'money' | 'date' | 'render';
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, idx: number) => string;
  searchable?: boolean;
  searchPlaceholder?: string;
  pageSize?: number;
  onRowClick?: (row: T) => void;
  emptyTitle?: string;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
}

export function DataTable<T>({
  columns, rows, rowKey, searchable = true, searchPlaceholder = 'Поиск…',
  pageSize = 25, onRowClick, emptyTitle = 'Нет данных для отображения', initialSort,
}: Props<T>) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    let out = rows;
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      out = out.filter((r) => columns.some((c) => String((r as Record<string, unknown>)[c.key] ?? '').toLowerCase().includes(needle)));
    }
    if (sort) {
      const dir = sort.dir === 'asc' ? 1 : -1;
      out = [...out].sort((a, b) => {
        const av = (a as Record<string, unknown>)[sort.key];
        const bv = (b as Record<string, unknown>)[sort.key];
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
        return String(av ?? '').localeCompare(String(bv ?? ''), 'ru') * dir;
      });
    }
    return out;
  }, [rows, q, sort, columns]);

  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, pages - 1);
  const view = filtered.slice(cur * pageSize, cur * pageSize + pageSize);

  function cell(row: T, c: Column<T>): React.ReactNode {
    const v = (row as Record<string, unknown>)[c.key];
    if (c.render) return c.render(row);
    if (c.type === 'money') return <span className="num">{fmtMoney(v as number)}</span>;
    if (c.type === 'num') return <span className="num">{fmtNum(v as number)}</span>;
    if (c.type === 'date') return <span className="mono">{String(v ?? '—')}</span>;
    return v === null || v === undefined || v === '' ? '—' : String(v);
  }

  return (
    <div>
      {searchable && rows.length > 0 && (
        <div className="toolbar">
          <input className="input grow" style={{ maxWidth: 340 }} value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder={searchPlaceholder}
            aria-label="Поиск по таблице" />
          <span className="muted small">{filtered.length} из {rows.length}</span>
        </div>
      )}
      {view.length === 0 ? (
        <EmptyState title={rows.length === 0 ? emptyTitle : 'Ничего не найдено'}
          hint={rows.length === 0 ? undefined : 'Измените поисковый запрос'} />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {columns.map((c) => {
                  const sortable = c.sortable !== false && c.type !== 'render';
                  const active = sort?.key === c.key;
                  return (
                    <th key={c.key} className={sortable ? 'sortable' : undefined}
                      onClick={sortable ? () => {
                        setSort((s) => s && s.key === c.key
                          ? { key: c.key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
                          : { key: c.key, dir: 'asc' });
                      } : undefined}
                      aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                      {c.label}{active ? (sort!.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {view.map((row, i) => (
                <tr key={rowKey(row, i)} className={onRowClick ? 'clickable' : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}>
                  {columns.map((c) => <td key={c.key}>{cell(row, c)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="flex" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
          <button className="btn sm" disabled={cur === 0} onClick={() => setPage(cur - 1)}>← Назад</button>
          <span className="muted small">стр. {cur + 1} / {pages}</span>
          <button className="btn sm" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Вперёд →</button>
        </div>
      )}
    </div>
  );
}
