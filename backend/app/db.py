from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Supabase client using the service-role key.

    SERVER-SIDE ONLY: it bypasses Row Level Security, so every query made with it
    must be scoped to the authenticated user's id explicitly. Created lazily so
    the app (and /health) can start without Supabase credentials configured.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
