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
