// Главная панель (дашборд). GET /api/v1/dashboard — только реальные поля ответа backend.

import { useNavigate } from 'react-router-dom';
import { dashboardApi } from '../api/endpoints';
import { EmptyState, ErrorState, KpiCard, Loading, useAsync } from '../components/ui';
import { fmtDateTime, fmtMoney, fmtMoneyShort, fmtNum, fmtTime } from '../format';
import { StatusBadge } from '../components/ui';

const EVENT_LABELS: Record<string, string> = {
  'order.cancel': 'Отмена заказа', 'payment.refund': 'Возврат', 'stock.writeoff': 'Списание товара',
  'shift.cash_out': 'Изъятие наличных', 'shift.closed_with_diff': 'Закрытие смены с расхождением',
  'discount.over_limit': 'Скидка сверх лимита',
};

export function DashboardPage() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => dashboardApi.get(), []);

  if (loading) return <Loading text="Загрузка показателей…" />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <EmptyState title="Нет ответа от сервера" hint="Попробуйте обновить страницу" action={<button className="btn" onClick={reload}>Повторить</button>} />;

  const maxDay = Math.max(...data.week_revenue.map((d) => d.revenue), 1);
  const maxCat = Math.max(...data.by_category.map((c) => c.amount), 1);

  return (
    <div>
      <div className="flex between wrap mb">
        <div className="muted small">Данные на {fmtDateTime(data.generated_at)} · каждый показатель — из операций в базе</div>
        <button className="btn sm" onClick={reload}>↻ Обновить</button>
      </div>

      <div className="grid cols-4">
        <KpiCard label="Выручка сегодня" value={fmtMoney(data.today.revenue)}
          sub={`Чек-возвраты: ${fmtMoney(data.today.refunds_amount)} (${data.today.refunds_count})`} accent="violet" />
        <KpiCard label="Чеков / средний чек" value={`${data.today.checks} / ${fmtMoneyShort(data.today.avg_check)}`}
          sub={data.today.checks ? undefined : 'Подтверждённых оплат сегодня нет'} />
        <KpiCard label="Наличные / безнал" value={fmtMoneyShort(data.today.cash)}
          sub={`Безнал: ${fmtMoneyShort(data.today.noncash)}`} />
        <KpiCard label="Открытых заказов" value={data.today.orders_open}
          sub={data.today.open_order_ids.length ? 'Нажмите, чтобы перейти к залу' : undefined}
          accent={data.today.orders_open ? 'amber' : undefined}
          onClick={data.today.orders_open ? () => nav('/hall') : undefined} />
      </div>

      <div className="grid cols-2 mt">
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Выручка за 7 дней</div>
          {data.week_revenue.every((d) => d.revenue === 0) ? (
            <EmptyState title="За неделю оплат не было" hint="Это реальный нулевой результат, а не отсутствие данных" />
          ) : data.week_revenue.map((d) => (
            <div className="bar-row" key={d.date}>
              <span className="bar-label">{d.date.split('-').reverse().join('.')}</span>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${(d.revenue / maxDay) * 100}%` }} /></div>
              <span className="bar-val">{fmtMoneyShort(d.revenue)}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Категории за сегодня (оплаченные)</div>
          {data.by_category.length === 0 ? <EmptyState title="Сегодня ещё нет оплаченных позиций" /> :
            data.by_category.slice(0, 8).map((c) => (
              <div className="bar-row" key={c.category}>
                <span className="bar-label">{c.category}</span>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${(c.amount / maxCat) * 100}%`, background: 'var(--success)' }} /></div>
                <span className="bar-val">{fmtMoneyShort(c.amount)}</span>
              </div>
            ))}
        </div>
      </div>

      <div className="grid cols-2 mt">
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Топ позиций за сегодня</div>
          {data.top_items.length === 0 ? <EmptyState title="Нет проданных позиций" /> : (
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>Позиция</th><th className="num">Кол-во</th><th className="num">Сумма</th></tr></thead>
              <tbody>{data.top_items.map((t) => (
                <tr key={t.name}><td>{t.name}</td><td className="num">{fmtNum(t.qty, 3)}</td><td className="num">{fmtMoney(t.amount)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Открытые смены ({data.shifts_open.length})</div>
          {data.shifts_open.length === 0 ? <EmptyState title="Смены закрыты или не открыты" /> : (
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>Касса</th><th>Открыта</th><th className="num">Ожидается в кассе</th></tr></thead>
              <tbody>{data.shifts_open.map((s) => (
                <tr key={s.id}><td>#{s.register_id.slice(0, 8)}</td><td>{fmtTime(s.opened)}</td><td className="num">{fmtMoney(s.expected_cash)}</td></tr>
              ))}</tbody>
            </table>
          )}
          <div className="small muted mt">
            Просрочка на кухне: <b style={{ color: data.late_kitchen_tickets > 0 ? 'var(--danger)' : 'inherit' }}>{data.late_kitchen_tickets}</b> тикетов (&gt;15 мин)
            {' · '}Непрочитанных критических уведомлений: <b>{data.unread_critical_notifications}</b>
          </div>
        </div>
      </div>

      <div className="grid cols-2 mt">
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Низкие остатки склада</div>
          {data.low_stock.length === 0 ? <EmptyState title="Все запасы выше минимума" /> : (
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>Товар</th><th>Склад</th><th className="num">Факт</th><th className="num">Минимум</th></tr></thead>
              <tbody>{data.low_stock.slice(0, 10).map((l, i) => (
                <tr key={i}><td>{l.product}</td><td>{l.warehouse}</td><td className="num" style={{ color: 'var(--danger)' }}>{fmtNum(l.qty, 3)}</td><td className="num">{fmtNum(l.min, 3)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Критические события за 7 дней</div>
          {data.critical_events.length === 0 ? <EmptyState title="Критических событий не зафиксировано" /> : (
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>Время</th><th>Событие</th><th className="num">Сумма</th></tr></thead>
              <tbody>{data.critical_events.map((e, i) => (
                <tr key={i}>
                  <td className="mono">{fmtDateTime(e.at)}</td>
                  <td><StatusBadge status={e.type.includes('refund') || e.type.includes('cancel') ? 'cancelled' : 'pending'} />{' '}{EVENT_LABELS[e.type] ?? e.type}</td>
                  <td className="num">{e.amount !== null ? fmtMoney(e.amount) : '—'}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </div>

      <div className="grid cols-2 mt">
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Сотрудники на смене (подтверждённое время)</div>
          {data.on_duty.length === 0 ? <EmptyState title="Нет активных временных записей" /> :
            data.on_duty.map((o, i) => (
              <div key={i} className="flex between" style={{ padding: '6px 0', borderBottom: '1px dashed var(--border)' }}>
                <span>{o.staff}</span><span className="muted small mono">с {fmtDateTime(o.since)}</span>
              </div>
            ))}
        </div>
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Финансы за месяц</div>
          <div className="grid cols-2">
            <KpiCard label="Выручка (оплаты)" value={fmtMoney(data.finance_month.revenue)} accent="green" />
            <KpiCard label="Расходы (проведено)" value={fmtMoney(data.finance_month.expenses)} accent="red" />
          </div>
          <div className="small muted mt">{data.finance_month.note}</div>
        </div>
      </div>
    </div>
  );
}
