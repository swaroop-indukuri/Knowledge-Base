import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings

URL = "https://test.supabase.co"
SECRET = "test-secret-test-secret-test-secret-123456"
USER = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def claims(**over):
    base = {
        "sub": USER,
        "aud": "authenticated",
        "iss": f"{URL}/auth/v1",
        "exp": int(time.time()) + 300,
    }
    base.update(over)
    return base


def get_me(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/me", headers=headers)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_token_is_401(client):
    assert get_me(client).status_code == 401


def test_garbage_token_is_401(client):
    assert get_me(client, "not-a-jwt").status_code == 401


def test_hs256_valid(client):
    token = jwt.encode(claims(), SECRET, algorithm="HS256")
    r = get_me(client, token)
    assert r.status_code == 200 and r.json() == {"user_id": USER}


def test_hs256_wrong_secret(client):
    token = jwt.encode(claims(), "x" * 40, algorithm="HS256")
    assert get_me(client, token).status_code == 401


def test_hs256_expired(client):
    token = jwt.encode(claims(exp=int(time.time()) - 10), SECRET, algorithm="HS256")
    assert get_me(client, token).status_code == 401


def test_wrong_audience(client):
    token = jwt.encode(claims(aud="anon"), SECRET, algorithm="HS256")
    assert get_me(client, token).status_code == 401


def test_wrong_issuer(client):
    token = jwt.encode(claims(iss="https://evil.example/auth/v1"), SECRET, algorithm="HS256")
    assert get_me(client, token).status_code == 401


def test_alg_none_rejected(client):
    token = jwt.encode(claims(), None, algorithm="none")
    assert get_me(client, token).status_code == 401


def _fake_jwks(monkeypatch, public_key):
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda t: SimpleNamespace(key=public_key))
    monkeypatch.setattr(auth, "_jwks_client", lambda url: fake)


def test_es256_valid_via_jwks(client, monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    _fake_jwks(monkeypatch, private.public_key())
    token = jwt.encode(claims(), private, algorithm="ES256", headers={"kid": "k1"})
    r = get_me(client, token)
    assert r.status_code == 200 and r.json() == {"user_id": USER}


def test_es256_signed_by_other_key(client, monkeypatch):
    trusted = ec.generate_private_key(ec.SECP256R1())
    attacker = ec.generate_private_key(ec.SECP256R1())
    _fake_jwks(monkeypatch, trusted.public_key())
    token = jwt.encode(claims(), attacker, algorithm="ES256", headers={"kid": "k1"})
    assert get_me(client, token).status_code == 401


def test_cors_preflight_allows_frontend(client):
    r = client.options(
        "/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
