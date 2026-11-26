import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError as PostgrestError

from app.config import get_settings
from app.routers import ask, conversations, health, me, sources

log = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="Knowledge Management Demo API")

_MISSING_TABLE_CODES = {"PGRST205", "PGRST202", "42P01", "42883"}  # table / function not found


@app.exception_handler(PostgrestError)
async def database_error(request: Request, exc: PostgrestError) -> JSONResponse:
    if exc.code in _MISSING_TABLE_CODES:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The database isn't set up yet. Run the SQL files in backend/migrations "
                "(001_init.sql, then 003_hybrid_search.sql) in the Supabase SQL editor."
            },
        )
    if exc.code == "42501":  # permission denied
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The database is missing table permissions. Run "
                "backend/migrations/004_grants.sql in the Supabase SQL editor."
            },
        )
    log.error("Database error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": "A database error occurred."})


# Registered BEFORE the CORS middleware so it sits inside it: an unexpected crash then
# still gets CORS headers, and the browser shows the real error instead of a bare
# "network error" (Starlette's own 500 handler runs outside the CORS layer).
@app.middleware("http")
async def catch_unexpected_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Extra origins from env: local dev (ALLOW_LOCALHOST_ORIGINS) and/or a custom regex
    # such as Vercel preview URLs (FRONTEND_ORIGIN_REGEX). Exact origins: FRONTEND_ORIGIN.
    allow_origin_regex=settings.origin_regex,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health.router)
app.include_router(me.router)
app.include_router(sources.router)
app.include_router(ask.router)
app.include_router(conversations.router)
