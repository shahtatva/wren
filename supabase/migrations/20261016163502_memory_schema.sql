-- Wren memory schema v1. Design: CLAUDE.md "Memory design".
--
-- Access model: the browser never talks to Supabase. voice/ connects to Postgres
-- directly. For user requests it runs queries as `authenticated` with the user's id,
-- so the RLS policies below apply. Background jobs and evals run as the table owner.
-- New tables aren't exposed to the Data API by default (Supabase, 2026), so the
-- grants at the end are explicit.

create extension if not exists vector with schema extensions;

-- Keeps updated_at honest on tables that get edited.
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- Profile: one row per user, always in context.
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  summary text not null default '', -- e.g. "MS in Information Management, targeting BDR roles"
  target_roles text[] not null default '{}',
  timezone text not null default 'UTC', -- IANA name; pipeline dates are local to this zone
  age_confirmed_at timestamptz, -- set when the user confirms they are 18 or older
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Pipeline: dates, stages and companies live in a table, not in vectors.
create table public.pipeline (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  company text not null,
  role text,
  stage text not null, -- free text: "recruiter screen", "take-home", "panel", "offer", ...
  next_date date, -- local date in profiles.timezone
  next_time time, -- optional; null for all-day items like a take-home deadline
  notes text,
  superseded_by uuid references public.pipeline (id) on delete set null, -- "it moved to Friday" keeps the old row
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  mode text not null check (mode in ('checkin', 'mock')),
  transcript jsonb not null default '[]', -- raw turns [{id, speaker, text}], for audit and evals only
  summary text, -- written after the session by the background job
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  external_id text, -- stable id from outside the app, e.g. eval session "maya-s03"
  unique (user_id, external_id)
);

-- Story bank: the rehearsable version of a story.
create table public.stories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  title text not null,
  situation text,
  action text,
  result text,
  competencies text[] not null default '{}',
  times_used integer not null default 0 check (times_used >= 0),
  last_rating smallint check (last_rating between 1 and 5),
  embedding extensions.vector(768) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Memories: atomic claims, one per row, extracted after each session.
create table public.memories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null check (kind in ('story', 'feedback', 'episode', 'vibe')),
  text text not null,
  entities text[] not null default '{}',
  importance smallint not null check (importance between 1 and 5),
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  source_session uuid references public.sessions (id) on delete set null, -- purging a transcript keeps its memories
  source_turns text[] not null default '{}', -- turn ids like "maya-s03:t07"; Recall@5 scores against these
  superseded_by uuid references public.memories (id) on delete set null,
  embedding extensions.vector(768) not null,
  tsv tsvector generated always as (to_tsvector('english', text)) stored
);

-- Lookups by owner, plus an index on every foreign key.
create index pipeline_user_next_idx on public.pipeline (user_id, next_date) where superseded_by is null;
create index pipeline_superseded_by_idx on public.pipeline (superseded_by);
create index sessions_user_started_idx on public.sessions (user_id, started_at desc);
create index stories_user_idx on public.stories (user_id);
create index memories_user_current_idx on public.memories (user_id) where superseded_by is null;
create index memories_source_session_idx on public.memories (source_session);
create index memories_superseded_by_idx on public.memories (superseded_by);

-- Vector search (cosine). HNSW is approximate: a query that also filters by user
-- must turn on pgvector's iterative scan (hnsw.iterative_scan), or it can return
-- fewer than k rows when other users' rows are nearer.
create index memories_embedding_idx on public.memories using hnsw (embedding extensions.vector_cosine_ops);
create index stories_embedding_idx on public.stories using hnsw (embedding extensions.vector_cosine_ops);

-- Keyword search, the other half of hybrid retrieval.
create index memories_tsv_idx on public.memories using gin (tsv);

create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();
create trigger pipeline_set_updated_at before update on public.pipeline
  for each row execute function public.set_updated_at();
create trigger stories_set_updated_at before update on public.stories
  for each row execute function public.set_updated_at();

-- Row-level security: each user sees and changes only their own rows.
alter table public.profiles enable row level security;
alter table public.pipeline enable row level security;
alter table public.sessions enable row level security;
alter table public.stories enable row level security;
alter table public.memories enable row level security;

create policy "Users manage their own profile" on public.profiles
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users manage their own pipeline" on public.pipeline
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users manage their own sessions" on public.sessions
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users manage their own stories" on public.stories
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users manage their own memories" on public.memories
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

revoke all on table public.profiles, public.pipeline, public.sessions, public.stories, public.memories from anon;
grant select, insert, update, delete
  on table public.profiles, public.pipeline, public.sessions, public.stories, public.memories
  to authenticated, service_role;
