-- 002_storage.sql — private Storage bucket for uploaded PDFs.
-- Path convention: {user_id}/{source_id}.pdf  (written by the backend with the
-- service-role key, which bypasses Storage RLS). No storage.objects policies are
-- created, so anon/authenticated clients cannot read or write these files directly;
-- the backend hands out short-lived signed URLs instead.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('sources', 'sources', false, 20971520, array['application/pdf'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;
