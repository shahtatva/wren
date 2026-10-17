-- hybrid_search(): memory retrieval for /turn and the eval harness. Design: CLAUDE.md "Memory design".
--
-- For one search question: vector search and full-text search each rank the user's
-- current memories (superseded rows are skipped), reciprocal rank fusion merges the two
-- lists, and the fused score is then boosted by recency and importance.
--
-- The weights are parameters so E3 can run every arm through this one function:
--   vector only:              full_text_weight => 0, recency_weight => 0, importance_weight => 0
--   rank fusion, no boost:    recency_weight => 0, importance_weight => 0
--   rank fusion plus boost:   the defaults
-- The boost defaults are a cautious starting point, not tuned values. E3 tunes them.
--
-- Access: security invoker, so RLS applies when voice/ calls it as `authenticated`.
-- Background jobs and evals run as the table owner, which bypasses RLS, so the user
-- filter is explicit too. Owner-role callers must pass for_user; without it,
-- auth.uid() is null and the function returns nothing.

create function public.hybrid_search(
  query_embedding extensions.vector(768),
  query_text text,
  k integer default 8,
  for_user uuid default auth.uid(),
  as_of timestamptz default now(), -- recency is measured from here; evals pin it so reruns match
  rrf_k integer default 60,
  semantic_weight double precision default 1,
  full_text_weight double precision default 1,
  recency_weight double precision default 0.1, -- a memory said just now scores up to 10% higher
  importance_weight double precision default 0.1, -- importance 5 scores up to 10% higher than 1
  recency_half_life_days double precision default 30
)
returns table (
  id uuid,
  kind text,
  text text,
  entities text[],
  importance smallint,
  source_turns text[],
  said_at timestamptz, -- when the source session happened, which is what recency means here
  score double precision
)
language sql
stable
security invoker
set search_path = ''
-- HNSW is approximate and the user filter applies after it. Iterative scan keeps searching
-- until enough rows pass the filter, and strict_order keeps exact distance order.
set hnsw.iterative_scan = strict_order
as $$
  with
  -- The question's stemmed words, joined with OR. websearch_to_tsquery would AND them,
  -- and a spoken question rarely shares every word with the memory it is after.
  -- Lexemes are quoted and escaped, so the cast never parses them as operators.
  keywords as (
    select string_agg('''' || replace(replace(lexeme, '\', '\\'), '''', '''''') || '''', ' | ')::tsquery as q
    from unnest(tsvector_to_array(to_tsvector('english', query_text))) as lexeme
  ),
  semantic as (
    select nearest.id, row_number() over (order by nearest.distance, nearest.id) as rank_ix
    from (
      -- ORDER BY must be the distance alone, or Postgres can't use the HNSW index.
      select m.id, m.embedding operator(extensions.<=>) query_embedding as distance
      from public.memories m
      where m.user_id = for_user and m.superseded_by is null
      order by m.embedding operator(extensions.<=>) query_embedding
      limit k * 4
    ) nearest
  ),
  full_text as (
    select matched.id, row_number() over (order by matched.text_rank desc, matched.id) as rank_ix
    from (
      select m.id, ts_rank_cd(m.tsv, keywords.q) as text_rank
      from public.memories m
      cross join keywords
      where m.user_id = for_user and m.superseded_by is null and m.tsv @@ keywords.q
      order by text_rank desc, m.id
      limit k * 4
    ) matched
  ),
  fused as (
    select
      coalesce(s.id, f.id) as id,
      coalesce(semantic_weight / (rrf_k + s.rank_ix), 0)
        + coalesce(full_text_weight / (rrf_k + f.rank_ix), 0) as rrf
    from semantic s
    full outer join full_text f on f.id = s.id
  ),
  dated as (
    select
      fused.rrf, m.id, m.kind, m.text, m.entities, m.importance, m.source_turns,
      coalesce(sess.started_at, m.created_at) as said
    from fused
    join public.memories m on m.id = fused.id
    left join public.sessions sess on sess.id = m.source_session
    where fused.rrf > 0 -- a list with weight 0 contributes no candidates
  )
  select
    d.id, d.kind, d.text, d.entities, d.importance, d.source_turns, d.said,
    d.rrf * (
      1
      + recency_weight
        * exp(-ln(2.0) * greatest(extract(epoch from as_of - d.said)::double precision, 0)
              / (recency_half_life_days * 86400))
      + importance_weight * (d.importance - 1) / 4.0
    ) as score
  from dated d
  order by score desc, d.id
  limit k
$$;

-- Supabase grants EXECUTE on new public functions to anon by default. The browser never
-- talks to Supabase, so only voice/ (authenticated) and server jobs (service_role) can call it.
revoke execute on function public.hybrid_search from public, anon;
grant execute on function public.hybrid_search to authenticated, service_role;
