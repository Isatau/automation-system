// Страница входа. POST /api/v1/auth/login — поля {login, password} (подтверждено по коду backend).
// Ответ: {token, user:{id,name,role,login}}. Права подтягиваются из GET /auth/me.

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ApiError } from '../api/client';

export function LoginPage() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [loginName, setLoginName] = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  const valid = loginName.trim().length >= 2 && password.length >= 4;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!valid || busy) return;              // запрет повторной отправки во время запроса
    setError(null);
    setBusy(true);
    try {
      await login(loginName.trim(), password);
      nav('/', { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setError(err.message);
      else if (err instanceof ApiError && err.status === 401) setError('Неверный логин или пароль. После 5 неудачных попыток учётная запись блокируется на 15 минут.');
      else setError((err as Error).message || 'Не удалось выполнить вход');
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit} noValidate>
        <div className="brand">
          <div className="logo-mark">R</div>
          <h1>RestoCore</h1>
          <p>Система автоматизации ресторана</p>
        </div>

        <div className="field">
          <label htmlFor="login">Логин</label>
          <input id="login" className={`input${touched && loginName.trim().length < 2 ? ' invalid' : ''}`}
            value={loginName} onChange={(e) => setLoginName(e.target.value)}
            autoComplete="username" autoFocus disabled={busy} />
          {touched && loginName.trim().length < 2 && <div className="form-error">Введите логин</div>}
        </div>

        <div className="field">
          <label htmlFor="password">Пароль</label>
          <div className="password-wrap">
            <input id="password" type={showPwd ? 'text' : 'password'}
              className={`input${touched && password.length < 4 ? ' invalid' : ''}`}
              value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" disabled={busy} />
            <button type="button" className="eye" aria-label={showPwd ? 'Скрыть пароль' : 'Показать пароль'}
              onClick={() => setShowPwd((s) => !s)}>
              {showPwd ? '🙈' : '👁'}
            </button>
          </div>
          {touched && password.length < 4 && <div className="form-error">Введите пароль (не менее 4 символов)</div>}
        </div>

        {error && <div className="error-box mb" role="alert">{error}</div>}

        <button type="submit" className="btn primary block lg" disabled={busy}>
          {busy ? <><span className="spinner" style={{ width: 18, height: 18, borderWidth: 2 }} /> Вход…</> : 'Войти'}
        </button>
        <p className="form-note" style={{ textAlign: 'center', marginTop: 12 }}>
          Доступ только для сотрудников заведения · ₸ · Asia/Almaty
        </p>
      </form>
    </div>
  );
}
