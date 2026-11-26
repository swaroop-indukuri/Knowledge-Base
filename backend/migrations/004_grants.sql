-- 004_grants.sql — table privileges for the Supabase API roles.
-- Newer Supabase projects don't auto-grant access on tables created via SQL, so the
-- backend (service_role) failed with "permission denied for table ..." (42501).
-- Row Level Security (001_init.sql) still limits what `authenticated` users can see;
-- service_role bypasses RLS and is used only by the backend.

grant usage on schema public to service_role, authenticated;

grant select, insert, update, delete on all tables in schema public to service_role, authenticated;

-- Tables created by later migrations get the same grants automatically.
alter default privileges in schema public
  grant select, insert, update, delete on tables to service_role, authenticated;
