-- ============================================================
-- 004_match_data_raw.sql
-- Full per-book market data storage — no filtering, no comparisons.
-- Anon can read + write (Oracle server uses anon key).
-- ============================================================

create table if not exists match_data_raw (
  match_name   text        not null,
  league       text        not null default 'Liga MX',
  match_date   date,
  book_markets jsonb       not null,  -- {caliente: [{name, selections}], codere: ..., ...}
  scraped_at   timestamptz not null default now(),
  primary key (match_name, league)
);

alter table match_data_raw enable row level security;

create policy "anon read raw"
  on match_data_raw for select
  to anon using (true);

create policy "anon insert raw"
  on match_data_raw for insert
  to anon with check (true);

create policy "anon update raw"
  on match_data_raw for update
  to anon using (true);
