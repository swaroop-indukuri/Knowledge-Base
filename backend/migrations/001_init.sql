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
