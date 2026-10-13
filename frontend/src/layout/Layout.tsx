// Общий макет: боковая панель (навигация по реальным permissions), верхняя панель, контент.

import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Loading, toast } from '../components/ui';

const ROLE_NAMES: Record<string, string> = {
  owner: 'Владелец', manager: 'Управляющий', shift_admin: 'Администратор смены',
  cashier: 'Кассир', waiter: 'Официант', bartender: 'Бармен', cook: 'Повар',
  storekeeper: 'Кладовщик', accountant: 'Бухгалтер', tech_admin: 'Технический администратор',
};

interface NavItem { to: string; label: string; icon: string; perms: string[]; anyOf?: boolean }
interface NavGroup { title: string; items: NavItem[] }

// Пункты меню показываем только если у пользователя есть подтверждённые backend-ом права
// (список permissions из GET /auth/me). Это UX-фильтр; сервер проверяет права на каждом запросе.
const NAV: NavGroup[] = [
  {
    title: 'Рабочие места',
    items: [
      { to: '/pos', label: 'Касса', icon: '🧾', perms: ['orders:create'] },
      { to: '/hall', label: 'Зал и столы', icon: '🪑', perms: ['hall:view'] },
      { to: '/kds', label: 'Кухня / бар (KDS)', icon: '🍳', perms: ['kitchen:view'] },
      { to: '/shifts', label: 'Кассовые смены', icon: '💵', perms: ['shifts:view'] },
    ],
  },
  {
    title: 'Управление',
    items: [
      { to: '/', label: 'Панель управляющего', icon: '📊', perms: ['reports:view'] },
      { to: '/reports', label: 'Отчёты', icon: '📈', perms: ['reports:view'] },
      { to: '/audit', label: 'Журнал событий', icon: '🛡', perms: ['audit:view'] },
      { to: '/notifications', label: 'Уведомления', icon: '🔔', perms: ['notifications:view'] },
      { to: '/admin', label: 'Администрирование', icon: '⚙️', perms: ['admin:users', 'backups:run'], anyOf: true },
    ],
  },
];

export function Layout() {
  const { user, loading, can, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('restocore_theme') as 'dark' | 'light') || 'dark');

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); }, [theme]);
  useEffect(() => { setMenuOpen(false); }, [loc.pathname]);

  if (loading) return <Loading text="Проверка сессии…" />;
  if (!user) { nav('/login', { replace: true }); return null; }

  const visible = (it: NavItem) => (it.anyOf ? it.perms.some(can) : it.perms.every(can));

  async function doLogout() {
    try { await logout(); toast('Сессия завершена', 'ok'); } catch (e) { toast((e as Error).message, 'err'); }
    nav('/login', { replace: true });
  }

  const currentTitle = NAV.flatMap((g) => g.items).find((i) =>
    i.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(i.to))?.label ?? 'RestoCore';

  return (
    <div className="app-shell">
      <div className={`scrim${menuOpen ? ' show' : ''}`} onClick={() => setMenuOpen(false)} />
      <aside className={`sidebar${menuOpen ? ' open' : ''}`}>
        <div className="logo"><span className="logo-mark">R</span> RestoCore</div>
        <nav className="nav" aria-label="Основная навигация">
          {NAV.map((g) => {
            const items = g.items.filter(visible);
            if (!items.length) return null;
            return (
              <div key={g.title}>
                <div className="nav-group-title">{g.title}</div>
                {items.map((it) => (
                  <NavLink key={it.to} to={it.to} end={it.to === '/'}
                    className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}>
                    <span aria-hidden>{it.icon}</span> {it.label}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div>
          <div className="user-meta">
            <div className="name">{user.name}</div>
            <div className="role">{ROLE_NAMES[user.role] ?? user.role}</div>
          </div>
          <button className="icon-btn" title="Завершить сеанс" aria-label="Выход" onClick={doLogout}>⎋</button>
        </div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <button className="hamburger" aria-label="Открыть меню" onClick={() => setMenuOpen(true)}>☰</button>
          <h1>{currentTitle}</h1>
          <div className="breadcrumbs"><span>RestoCore</span><span className="sep">/</span><span>{currentTitle}</span></div>
          <div className="spacer" />
          <button className="icon-btn" aria-label="Переключить тему"
            title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
            onClick={() => {
              const t = theme === 'dark' ? 'light' : 'dark';
              setTheme(t); localStorage.setItem('restocore_theme', t);
            }}>
            {theme === 'dark' ? '☀' : '🌙'}
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
