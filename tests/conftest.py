"""Общая инфраструктура тестов: чистая SQLite-БД на каждый прогон + сид."""
import os, shutil

# ВАЖНО: задаём ENV до импорта приложения
os.environ["DATABASE_URL"] = "sqlite:///./test_restocore.db"
os.environ["BACKUP_DIR"] = "./test_backups"
os.environ["RESTOCORE_ADMIN_PASSWORD"] = "TestPass123!"
os.environ["JWT_SECRET"] = "test-secret-not-for-production"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    for f in ("test_restocore.db", "test_restocore.db-wal", "test_restocore.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    if os.path.isdir("test_backups"):
        shutil.rmtree("test_backups")

    from app.db import Base, engine
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)
    from app.seed import seed_all
    seed_all(force=True)

    from app.main import app
    with TestClient(app) as c:   # context manager — выполняет lifespan (без фоновых циклов надолго)
        yield c


PWD = "TestPass123!"


def login(client, login_name: str) -> dict:
    """Возвращает {'token':..., 'user':..., 'h': {...}} с готовым заголовком Authorization."""
    r = client.post("/api/v1/auth/login", json={"login": login_name, "password": PWD})
    assert r.status_code == 200, f"login {login_name}: {r.status_code} {r.text}"
    tok = r.json()["token"]
    return {"token": tok, "user": r.json()["user"], "h": {"Authorization": f"Bearer {tok}"}}


@pytest.fixture(scope="session")
def cashier(client):
    return login(client, "cashier")


@pytest.fixture(scope="session")
def waiter(client):
    return login(client, "waiter")


@pytest.fixture(scope="session")
def manager(client):
    return login(client, "manager")


@pytest.fixture(scope="session")
def owner(client):
    return login(client, "owner")


@pytest.fixture(scope="session")
def storekeeper(client):
    return login(client, "storekeeper")


@pytest.fixture(scope="session")
def techadmin(client):
    return login(client, "techadmin")
