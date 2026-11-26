import re
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    gemini_api_key: str = ""
    # Comma-separated list of allowed browser origins (CORS), e.g. your Vercel URL.
    frontend_origin: str = "http://localhost:3000"
    # Optional regex of extra allowed origins, e.g. Vercel preview deployments:
    #   https://knowledge-base-.*-myteam\.vercel\.app
    frontend_origin_regex: str = ""
    # Also allow http://localhost / http://127.0.0.1 on any port (local dev). Set to
    # false in production.
    allow_localhost_origins: bool = True

    # Gemini models (override via env if Google retires/renames them).
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_chat_model: str = "gemini-3.5-flash-lite"
    # Model used to watch YouTube videos natively (must support video input).
    gemini_video_model: str = "gemini-3.5-flash"
    # Must match the halfvec(768) column in the chunks table.
    embedding_dimensions: int = 768

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def origin_regex(self) -> str | None:
        """Combined regex for CORSMiddleware (matched against the whole origin)."""
        parts = []
        if self.allow_localhost_origins:
            parts.append(r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?")
        custom = self.frontend_origin_regex.strip().removeprefix("^").removesuffix("$")
        if custom:
            re.compile(custom)  # fail fast at startup on an invalid pattern
            parts.append(f"(?:{custom})")
        return "(?:" + "|".join(parts) + ")" if parts else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
