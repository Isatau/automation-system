// Контекст авторизации. Роль и permissions — только из реального ответа API (/auth/login, /auth/me).
// Скрытие кнопок по правам — это UX; настоящая защита — на backend (require_perm).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi } from '../api/endpoints';
import { getToken, onUnauthorized, setToken } from '../api/client';
import type { AuthUser } from '../types/api';

interface AuthCtx {
  user: AuthUser | null;
  loading: boolean;          // восстановление сессии через /auth/me
  can: (perm: string) => boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({
  user: null, loading: true,
  can: () => false,
  login: async () => {}, logout: async () => {},
});

export function useAuth() { return useContext(Ctx); }

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState<boolean>(!!getToken());

  // Восстановление сессии после перезагрузки страницы/перезапуска приложения
  useEffect(() => {
    let alive = true;
    if (!getToken()) { setLoading(false); return; }
    authApi.me()
      .then((u) => { if (alive) setUser(u); })
      .catch(() => { setToken(null); if (alive) setUser(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  // Любая 401 от клиента => разрываем локальную сессию
  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const login = useCallback(async (loginName: string, password: string) => {
    const res = await authApi.login(loginName, password);
    setToken(res.token);
    // полный объект с permissions получаем из /auth/me (ответ login содержит только id/name/role/login)
    const me = await authApi.me();
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    try { await authApi.logout(); } catch { /* сервер недоступен — всё равно завершаем локально */ }
    setToken(null);
    setUser(null);
  }, []);

  const can = useCallback((perm: string) => !!user?.permissions.includes(perm), [user]);

  const value = useMemo(() => ({ user, loading, can, login, logout }), [user, loading, can, login, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
