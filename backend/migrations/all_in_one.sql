-- all_in_one.sql: 001_init + 003_hybrid_search + 004_grants, in order. For a FRESH database, paste this whole file into the Supabase SQL editor.
-- (002_storage.sql is not needed: the 'sources' bucket already exists.)

-- 001_init.sql — initial schema for the knowledge-management app (Supabase Postgres)

-- 1. pgvector (halfvec needs pgvector >= 0.7; available on Supabase)
create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.sources (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null,
  type         text not null check (type in ('url', 'pdf', 'youtube')),
  url          text,
  storage_path text,
  title        text,
  summary      text,
  category     text,
  status       text not null default 'processing'
               check (status in ('processing', 'ready', 'failed')),
  error        text,
  created_at   timestamptz not null default now()
);

create table public.chunks (
  id         uuid primary key default gen_random_uuid(),
  source_id  uuid not null references public.sources (id) on delete cascade,
  content    text not null,
  embedding  extensions.halfvec(768),
  tsv        tsvector,                       -- maintained by trigger below
  meta       jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.conversations (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null,
  created_at timestamptz not null default now()
);

create table public.messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  role            text not null check (role in ('user', 'assistant')),
  content         text,
  citations       jsonb not null default '[]'::jsonb,
  created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index sources_user_id_created_at_idx on public.sources (user_id, created_at desc);
create index chunks_source_id_idx           on public.chunks (source_id);
create index conversations_user_id_idx      on public.conversations (user_id, created_at desc);
create index messages_conversation_idx      on public.messages (conversation_id, created_at);

-- 2. HNSW index, cosine distance (halfvec_cosine_ops is the halfvec equivalent of vector_cosine_ops)
create index chunks_embedding_idx on public.chunks
  using hnsw (embedding extensions.halfvec_cosine_ops);

-- 3. GIN index on tsv + trigger keeping tsv in sync with content
create index chunks_tsv_idx on public.chunks using gin (tsv);

create function public.chunks_tsv_sync() returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.tsv := to_tsvector('english', coalesce(new.content, ''));
  return new;
end;
$$;

create trigger chunks_tsv_sync
  before insert or update of content on public.chunks
  for each row execute function public.chunks_tsv_sync();

-- ---------------------------------------------------------------------------
-- 4. Row Level Security
-- One "for all" policy per table covers select/insert/update/delete.
-- USING filters existing rows; WITH CHECK validates new/updated rows.
-- (select auth.uid()) is wrapped so Postgres evaluates it once per query.
-- messages has no user_id column, so ownership is checked via its conversation.
-- ---------------------------------------------------------------------------
alter table public.sources       enable row level security;
alter table public.chunks        enable row level security;
alter table public.conversations enable row level security;
alter table public.messages      enable row level security;

create policy sources_owner on public.sources
  for all to authenticated
  using      (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

create policy conversations_owner on public.conversations
  for all to authenticated
  using      (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

create policy chunks_owner on public.chunks
  for all to authenticated
  using (exists (
    select 1 from public.sources s
    where s.id = chunks.source_id and s.user_id = (select auth.uid())))
  with check (exists (
    select 1 from public.sources s
    where s.id = chunks.source_id and s.user_id = (select auth.uid())));

create policy messages_owner on public.messages
  for all to authenticated
  using (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = (select auth.uid())))
  with check (exists (
    select 1 from public.conversations c
    where c.id = messages.conversation_id and c.user_id = (select auth.uid())));

-- ---------------------------------------------------------------------------
-- 5. match_chunks: cosine-similarity search scoped to one user's sources.
-- SECURITY INVOKER (default): the service-role backend bypasses RLS, so the
-- match_user_id filter is what scopes results there; if an end user ever calls
-- it directly, RLS still applies on top.
-- ---------------------------------------------------------------------------
create function public.match_chunks(
  query_embedding extensions.halfvec(768),
  match_user_id   uuid,
  match_count     int
)
returns table (
  id         uuid,
  source_id  uuid,
  content    text,
  meta       jsonb,
  similarity double precision
)
language sql stable
set search_path = public, extensions
as $$
  select c.id, c.source_id, c.content, c.meta,
         1 - (c.embedding <=> query_embedding) as similarity
  from public.chunks c
  join public.sources s on s.id = c.source_id
  where s.user_id = match_user_id
    and c.embedding is not null
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

revoke execute on function public.match_chunks(extensions.halfvec, uuid, int) from public, anon;
grant  execute on function public.match_chunks(extensions.halfvec, uuid, int) to authenticated, service_role;


-- 003_hybrid_search.sql — hybrid (vector + full-text) search with Reciprocal Rank Fusion.
-- Requires 001_init.sql (chunks.tsv is kept current by its trigger).
--
-- RRF: each ranking contributes 1 / (rrf_k + rank) per chunk; a chunk found by both
-- searches gets both contributions. rrf_k = 60 is the standard constant.
-- SECURITY INVOKER (default) like match_chunks: the service-role backend bypasses RLS,
-- so match_user_id is what scopes results; RLS still applies if a user calls it directly.

create or replace function public.hybrid_search(
  query_text      text,
  query_embedding extensions.halfvec(768),
  match_user_id   uuid,
  match_count     int default 8,
  candidate_count int default 30,
  rrf_k           int default 60
)
returns table (
  id           uuid,
  source_id    uuid,
  content      text,
  meta         jsonb,
  source_title text,
  source_type  text,
  source_url   text,
  score        double precision
)
language sql stable
set search_path = public, extensions
as $$
  with semantic as (
    select c.id,
           row_number() over (order by c.embedding <=> query_embedding) as rnk
    from public.chunks c
    join public.sources s on s.id = c.source_id
    where s.user_id = match_user_id
      and c.embedding is not null
    order by c.embedding <=> query_embedding
    limit candidate_count
  ),
  keyword as (
    select c.id,
           row_number() over (order by ts_rank_cd(c.tsv, tsq.q) desc) as rnk
    from public.chunks c
    join public.sources s on s.id = c.source_id
    cross join (select websearch_to_tsquery('english', query_text) as q) tsq
    where s.user_id = match_user_id
      and c.tsv @@ tsq.q
    order by ts_rank_cd(c.tsv, tsq.q) desc
    limit candidate_count
  ),
  fused as (
    select coalesce(sem.id, kw.id) as id,
           coalesce(1.0::double precision / (rrf_k + sem.rnk), 0.0::double precision)
         + coalesce(1.0::double precision / (rrf_k + kw.rnk),  0.0::double precision) as score
    from semantic sem
    full outer join keyword kw on kw.id = sem.id
  )
  select c.id, c.source_id, c.content, c.meta,
         src.title, src.type, src.url,
         f.score
  from fused f
  join public.chunks  c   on c.id = f.id
  join public.sources src on src.id = c.source_id
  order by f.score desc
  limit match_count;
$$;

revoke execute on function public.hybrid_search(text, extensions.halfvec, uuid, int, int, int) from public, anon;
grant  execute on function public.hybrid_search(text, extensions.halfvec, uuid, int, int, int) to authenticated, service_role;


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
