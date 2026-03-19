-- ============================================================
-- 001_init.sql — Bettor LATAM initial schema
-- Run: supabase db push
-- ============================================================

-- ---- subscriptions (per-league access) ----
create table public.subscriptions (
  id                     uuid primary key default gen_random_uuid(),
  user_id                uuid references auth.users not null,
  league                 text not null,           -- 'nba' | 'nfl' | 'soccer'
  status                 text not null,           -- 'active' | 'cancelled'
  stripe_subscription_id text unique,
  stripe_customer_id     text,
  current_period_end     timestamptz,
  created_at             timestamptz default now(),

  constraint subscriptions_league_check
    check (league in ('nba', 'nfl', 'soccer', 'bundle')),
  constraint subscriptions_status_check
    check (status in ('active', 'cancelled', 'past_due'))
);

create index subscriptions_user_id_idx    on public.subscriptions(user_id);
create index subscriptions_user_league_idx on public.subscriptions(user_id, league, status);

-- RLS: users see only their own rows
alter table public.subscriptions enable row level security;

create policy "users see own subs"
  on subscriptions for select
  using (user_id = auth.uid());

-- Only edge functions (service role) can insert/update
create policy "service role manages subs"
  on subscriptions for all
  using (auth.role() = 'service_role');


-- ---- players (name → image ID mapping) ----
create table public.players (
  id           uuid primary key default gen_random_uuid(),
  name         text unique not null,      -- "LeBron James"
  sport        text not null,             -- 'nba' | 'nfl' | 'soccer'
  nba_id       int,                       -- 2544 → cdn.nba.com headshot
  espn_id      int,                       -- 1966 → espncdn.com headshot
  fbref_id     text,                      -- "abc123" → fbref.com headshot (soccer)
  team         text,                      -- "Lakers"
  team_abbrev  text,                      -- "lal" → ESPN team logo URL
  created_at   timestamptz default now(),

  constraint players_sport_check
    check (sport in ('nba', 'nfl', 'soccer'))
);

create index players_name_idx  on public.players(name);
create index players_sport_idx on public.players(sport);

-- Readable by all authenticated users (headshots are non-sensitive)
alter table public.players enable row level security;

create policy "authenticated users read players"
  on players for select
  to authenticated using (true);

-- Only service role can seed/update players
create policy "service role manages players"
  on players for all
  using (auth.role() = 'service_role');


-- ---- cached_odds (shared cache across all users) ----
create table public.cached_odds (
  id         uuid primary key default gen_random_uuid(),
  league     text not null,
  market     text not null,
  data       jsonb not null,
  fetched_at timestamptz default now(),
  expires_at timestamptz not null,

  unique(league, market),

  constraint cached_odds_league_check
    check (league in ('nba', 'nfl', 'soccer'))
);

create index cached_odds_league_market_idx on public.cached_odds(league, market, expires_at);

-- Readable by all authenticated users
alter table public.cached_odds enable row level security;

create policy "authenticated users read cache"
  on cached_odds for select
  to authenticated using (true);

-- Only edge functions (service role) can write cache
create policy "service role manages cache"
  on cached_odds for all
  using (auth.role() = 'service_role');
