import re

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import Settings

VERCEL = "https://my-app.vercel.app"


def make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # ignore any local .env


def test_exact_origins_trimmed_and_normalized():
    s = make(frontend_origin=f" {VERCEL}/ , http://localhost:3000 ")
    assert s.cors_origins == [VERCEL, "http://localhost:3000"]


def test_production_config_disables_localhost():
    assert make(frontend_origin=VERCEL, allow_localhost_origins=False).origin_regex is None


def test_localhost_regex_matches_any_port_only_on_loopback():
    rx = make(allow_localhost_origins=True).origin_regex
    for ok in ("http://localhost:3000", "http://127.0.0.1:5173", "http://localhost"):
        assert re.fullmatch(rx, ok), ok
    for bad in ("https://localhost:3000", "http://localhost.evil.com", "http://evil.com/localhost", "http://127.0.0.1.evil.com"):
        assert not re.fullmatch(rx, bad), bad


def test_custom_regex_for_preview_deployments():
    s = make(allow_localhost_origins=False, frontend_origin_regex=r"^https://my-app-.*\.vercel\.app$")
    assert re.fullmatch(s.origin_regex, "https://my-app-git-feature-me.vercel.app")
    assert not re.fullmatch(s.origin_regex, "https://other-app-xyz.vercel.app")
    assert not re.fullmatch(s.origin_regex, "https://my-app-x.vercel.app.evil.com")


def test_invalid_regex_fails_fast():
    with pytest.raises(re.error):
        make(frontend_origin_regex="(unclosed").origin_regex


def test_real_cors_behaviour_with_production_settings():
    """Wire the settings into CORSMiddleware exactly as main.py does."""
    s = make(frontend_origin=VERCEL, allow_localhost_origins=False)
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_origin_regex=s.origin_regex,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/x")
    def x():
        return {}

    c = TestClient(app)

    def allowed(origin):
        r = c.options("/x", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                     "Access-Control-Request-Headers": "authorization"})
        return r.headers.get("access-control-allow-origin")

    assert allowed(VERCEL) == VERCEL
    assert allowed("http://localhost:3000") is None  # dev origins are off in production
    assert allowed("https://evil.example") is None
