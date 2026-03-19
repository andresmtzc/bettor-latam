-- ============================================================
-- 002_match_comparisons.sql — line-shopping comparison cache
-- Run: supabase db push
-- ============================================================

-- Public comparison table — written by Oracle service role, read by anyone (no auth)
create table public.match_comparisons (
  id          uuid primary key default gen_random_uuid(),
  match_name  text not null,                  -- "Monterrey vs Club América"
  league      text not null,                  -- "Liga MX"
  match_date  date not null,
  markets     jsonb not null,                 -- sorted comparison rows (see schema below)
  scraped_at  timestamptz default now(),

  unique (match_name, match_date)
);

-- markets JSON shape (array of objects, tier-sorted):
-- [
--   {
--     "canonical":    "result",
--     "display_name": "Resultado del partido",
--     "tier":         1,          -- 1=4book+gap, 2=2-3book+gap, 3=no gap
--     "books":        4,          -- number of books with this market
--     "max_gap":      0.08,       -- max implied-prob gap across selections
--     "selections": [
--       {
--         "name":      "Monterrey",
--         "odds":      {"caliente": "+120", "codere": "+130", "1win": "+115"},
--         "best_book": "codere",
--         "gap":       0.06
--       }
--     ]
--   }
-- ]

create index match_comparisons_league_date_idx
  on public.match_comparisons (league, match_date desc);

-- RLS: public read (no auth required), service role writes
alter table public.match_comparisons enable row level security;

create policy "public read"
  on match_comparisons for select
  to anon using (true);

create policy "service role manages"
  on match_comparisons for all
  using (auth.role() = 'service_role');
