-- Session bookkeeping for the background jobs, and where each pipeline item came from.
-- Proposed in session 1, added before the first push. RLS and grants are unchanged:
-- policies work on rows, and new columns inherit the table grants.

-- How many transcript turns the rolling summary already covers, so the summary job
-- starts at the right turn.
alter table public.sessions add column summarized_turns integer not null default 0;

-- When extraction finished. Null means it hasn't run or it failed, so the job retries
-- it. A finished session is never extracted twice.
alter table public.sessions add column extracted_at timestamptz;

-- The session a pipeline item came from, for the "What Wren knows" page. Purging a
-- session's transcript keeps the item, as it keeps memories.
alter table public.pipeline
  add column source_session uuid references public.sessions (id) on delete set null;
create index pipeline_source_session_idx on public.pipeline (source_session);
