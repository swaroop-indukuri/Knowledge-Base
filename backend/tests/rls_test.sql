-- RLS two-user test. Paste into the Supabase SQL editor and run.
-- Everything happens inside a transaction that is rolled back, so nothing persists.
-- On failure: an error listing what leaked. On success: one row "ALL RLS CHECKS PASSED".

begin;

-- Seed as the editor's superuser role (bypasses RLS). Fake users: A = aaaa..., B = bbbb...
insert into public.sources (id, user_id, type, title) values
  ('11111111-1111-1111-1111-111111111111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'url', 'A source'),
  ('22222222-2222-2222-2222-222222222222', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'url', 'B source');

insert into public.chunks (source_id, content, embedding) values
  ('11111111-1111-1111-1111-111111111111', 'chunk owned by A',
     array_fill(0.1::real, array[768])::extensions.halfvec(768)),
  ('22222222-2222-2222-2222-222222222222', 'chunk owned by B',
     array_fill(0.2::real, array[768])::extensions.halfvec(768));

insert into public.conversations (id, user_id) values
  ('33333333-3333-3333-3333-333333333333', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
  ('44444444-4444-4444-4444-444444444444', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');

insert into public.messages (conversation_id, role, content) values
  ('33333333-3333-3333-3333-333333333333', 'user', 'A message'),
  ('44444444-4444-4444-4444-444444444444', 'user', 'B message');

do $$
declare
  a     uuid := 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  b     uuid := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  q     extensions.halfvec(768) := array_fill(0.1::real, array[768])::extensions.halfvec(768);
  n     int;
  fails text[] := '{}';
begin
  -- Sanity (still superuser): seed worked and the tsv trigger fires on insert and update.
  select count(*) into n from public.sources;
  if n < 2 then fails := fails || 'seed: sources missing'; end if;
  select count(*) into n from public.chunks where tsv is null;
  if n > 0 then fails := fails || 'trigger: tsv not populated on insert'; end if;
  update public.chunks set content = 'zebra stripes' where content = 'chunk owned by A';
  select count(*) into n from public.chunks where tsv @@ to_tsquery('english', 'zebra');
  if n <> 1 then fails := fails || 'trigger: tsv not updated on content update'; end if;
  update public.chunks set content = 'chunk owned by A' where content = 'zebra stripes';

  -- ===== Act as user A =====
  perform set_config('request.jwt.claims',
    json_build_object('sub', a, 'role', 'authenticated')::text, true);
  set local role authenticated;

  select count(*) into n from public.sources;
  if n <> 1 then fails := fails || format('A sees %s sources (want 1)', n); end if;
  select count(*) into n from public.sources where user_id = b;
  if n <> 0 then fails := fails || 'A can see B source'; end if;
  select count(*) into n from public.chunks;
  if n <> 1 then fails := fails || format('A sees %s chunks (want 1)', n); end if;
  select count(*) into n from public.conversations;
  if n <> 1 then fails := fails || format('A sees %s conversations (want 1)', n); end if;
  select count(*) into n from public.messages;
  if n <> 1 then fails := fails || format('A sees %s messages (want 1)', n); end if;

  -- A cannot write into B's data (RLS violations raise 42501 = insufficient_privilege)
  begin
    insert into public.sources (user_id, type) values (b, 'url');
    fails := fails || 'A inserted a source as B';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.chunks (source_id, content)
      values ('22222222-2222-2222-2222-222222222222', 'x');
    fails := fails || 'A inserted a chunk into B source';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.messages (conversation_id, role, content)
      values ('44444444-4444-4444-4444-444444444444', 'user', 'x');
    fails := fails || 'A inserted a message into B conversation';
  exception when insufficient_privilege then null; end;

  update public.sources set title = 'hacked'
    where id = '22222222-2222-2222-2222-222222222222';
  get diagnostics n = row_count;
  if n <> 0 then fails := fails || 'A updated B source'; end if;
  delete from public.sources where id = '22222222-2222-2222-2222-222222222222';
  get diagnostics n = row_count;
  if n <> 0 then fails := fails || 'A deleted B source'; end if;

  -- match_chunks: own data works; asking for B's data as A returns nothing (RLS backstop)
  select count(*) into n from public.match_chunks(q, a, 10);
  if n <> 1 then fails := fails || format('match_chunks(A) as A returned %s (want 1)', n); end if;
  select count(*) into n from public.match_chunks(q, b, 10);
  if n <> 0 then fails := fails || 'match_chunks(B) as A leaked rows'; end if;

  -- ===== Switch to user B =====
  perform set_config('request.jwt.claims',
    json_build_object('sub', b, 'role', 'authenticated')::text, true);

  select count(*) into n from public.sources where user_id = a;
  if n <> 0 then fails := fails || 'B can see A source'; end if;
  select count(*) into n from public.chunks where content like '%owned by A%';
  if n <> 0 then fails := fails || 'B can see A chunk'; end if;
  select count(*) into n from public.messages where content = 'A message';
  if n <> 0 then fails := fails || 'B can see A message'; end if;
  select count(*) into n from public.sources;
  if n <> 1 then fails := fails || format('B sees %s sources (want 1)', n); end if;

  -- ===== No JWT at all (auth.uid() is null) =====
  perform set_config('request.jwt.claims', '', true);
  select count(*) into n from public.sources;
  if n <> 0 then fails := fails || 'session with no user sees sources'; end if;

  reset role;

  if array_length(fails, 1) is not null then
    raise exception 'RLS TEST FAILED: %', array_to_string(fails, ' | ');
  end if;
end $$;

select 'ALL RLS CHECKS PASSED' as result;

rollback;
