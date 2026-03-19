# Bettor LATAM — Engineering Plan
_Generated 2026-03-18_

## Decisions Locked

| Decision | Choice | Why |
|---|---|---|
| Pricing model | Per-league $99 MXN, bundle $249 MXN | More granular, higher avg revenue |
| Frontend | Vanilla HTML/CSS/JS | No build step, designer-friendly, GitHub Pages native |
| Deno imports | `npm:` prefix | Simpler, same packages, no CDN fragility |
| Cache location | Supabase Postgres `cached_odds` table | Shared across all users, survives restarts |
| Soccer leagues | Liga MX + Champions League + Premier League | MLS has no LATAM audience |
| Edge formula | Server-side in odds-proxy (TODO) | Core IP — must not be in browser |
| CORS | Lock to GitHub Pages domain at launch | Wildcard allows competitor front-ends |

## File Structure

```
bettor-latam/
├── index.html              ← landing page
├── cheatsheet.html         ← odds UI (auth-gated)
├── login.html              ← auth page
├── style.css               ← all styles
├── app.js                  ← Supabase client + auth logic
├── cheatsheet.js           ← odds fetch + render
├── scripts/
│   └── seed-players.ts     ← one-time: fills players table from NBA/ESPN APIs [TODO]
│
└── supabase/
    ├── migrations/
    │   └── 001_init.sql    ← subscriptions + cached_odds + players tables + RLS
    └── functions/
        ├── odds-proxy/
        │   └── index.ts    ← JWT → sub check → cache → API → return
        ├── stripe-webhook/
        │   └── index.ts    ← verify → upsert subscription
        └── create-checkout/
            └── index.ts    ← JWT → create Stripe session → return URL [MISSING — LAUNCH BLOCKER]
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  BROWSER (GitHub Pages)                         │
│  index.html      ← landing page                 │
│  cheatsheet.html ← odds UI (auth-gated)         │
│  login.html      ← auth page                    │
└──────────┬──────────────┬───────────────────────┘
           │ fetch()      │ Stripe.js
           ▼              ▼
┌──────────────────┐  ┌──────────────────────────┐
│ Supabase         │  │ Stripe                   │
│ Edge Functions   │  │ Checkout Session         │
│                  │  │ (hosted page)            │
│ /odds-proxy      │  └──────────┬───────────────┘
│   ↓ check JWT    │             │ webhook POST
│   ↓ check sub    │  ┌──────────▼───────────────┐
│   ↓ check cache  │  │ /stripe-webhook          │
│   ↓ call API     │  │   verify signature       │
│   ↓ store cache  │  │   upsert subscription    │
│   ↓ return data  │  └──────────────────────────┘
└──────────┬───────┘
           ▼
┌──────────────────────────────────────────────────┐
│  Supabase Postgres                               │
│                                                  │
│  auth.users (managed by Supabase)                │
│                                                  │
│  public.subscriptions                            │
│    id, user_id, league, status,                  │
│    stripe_subscription_id,                       │
│    current_period_end, created_at                │
│                                                  │
│  public.cached_odds                              │
│    id, league, market, data (jsonb),             │
│    fetched_at, expires_at                        │
└──────────────────────────────────────────────────┘
           ▼
┌──────────────────┐
│  The Odds API    │
│  $30/mo          │
│  NBA/NFL/Soccer  │
└──────────────────┘
```

## DB Schema

```sql
-- subscriptions (per-league access)
create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users not null,
  league text not null,           -- 'nba' | 'nfl' | 'soccer'
  status text not null,           -- 'active' | 'cancelled'
  stripe_subscription_id text unique,
  current_period_end timestamptz,
  created_at timestamptz default now()
);

-- RLS: users see only their own rows
alter table public.subscriptions enable row level security;
create policy "users see own subs"
  on subscriptions for select
  using (user_id = auth.uid());

-- players (name → image ID mapping)
create table public.players (
  id uuid primary key default gen_random_uuid(),
  name text unique not null,        -- "LeBron James"
  sport text not null,              -- 'nba' | 'nfl' | 'soccer'
  nba_id int,                       -- 2544 → cdn.nba.com headshot
  espn_id int,                      -- 1966 → espncdn.com headshot
  fbref_id text,                    -- "abc123" → fbref.com headshot (soccer)
  team text,                        -- "Lakers"
  team_abbrev text,                 -- "lal" → team logo URL
  created_at timestamptz default now()
);
-- readable by all authenticated users
alter table public.players enable row level security;
create policy "authenticated users read players"
  on players for select
  to authenticated using (true);

-- cached_odds (shared across all users)
create table public.cached_odds (
  id uuid primary key default gen_random_uuid(),
  league text not null,
  market text not null,
  data jsonb not null,
  fetched_at timestamptz default now(),
  expires_at timestamptz not null,
  unique(league, market)
);

-- cached_odds readable by all authenticated users
alter table public.cached_odds enable row level security;
create policy "authenticated users read cache"
  on cached_odds for select
  to authenticated using (true);
```

## Edge Functions

### Player ID Mapping Strategy

The Odds API returns `"LeBron James"` as a string. We need `nba_id: 2544` to fetch his headshot.

**One-time seed script (run before launch, refresh each season):**
```
1. GET https://stats.nba.com/stats/commonallplayers
   → returns all NBA players ever with PERSON_ID + DISPLAY_FIRST_LAST
2. Match names against players table, fill in nba_id
3. Done — covers every active player automatically
```

For NFL: `site.api.espn.com/apis/site/v2/sports/football/nfl/athletes?limit=1000`
  → public API, no key required
  → same seed script approach, fills `espn_id` column
  → headshots: `a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/{espn_id}.png`

For soccer: FBref (fbref.com) is the primary source for player headshots and logos.
  Seed script matches player name → fbref_id, fills `fbref_id` column.
  Headshots: `fbref.com/req/202302030/images/headshots/{fbref_id}_2022.jpg`
  Team logos: TheSportsDB covers Liga MX, Champions League, EPL.
  FBref also has player stats useful for future features (xG, goals, assists).

**Fallback chain when a player isn't matched:**
```
nba_id present    → cdn.nba.com/headshots/nba/latest/1040x760/{id}.png
espn_id present   → espncdn.com headshot
fbref_id present  → fbref.com headshot (soccer)
neither           → team logo (team_abbrev present, TheSportsDB)
no team either    → sport icon (🏀 🏈 ⚽)
```

---

### /odds-proxy
```
1. Read Authorization header → verify JWT with Supabase
2. Read ?league= param
3. Query subscriptions WHERE user_id = uid AND league = ? AND status = 'active'
   → none found? return 403
4. Query cached_odds WHERE league = ? AND expires_at > now()
   → found? return data
5. Call The Odds API
   → timeout/error? return last cached data with stale flag
   → success? upsert cached_odds, return data
```

### /stripe-webhook
```
1. Read raw body + Stripe-Signature header
2. stripe.webhooks.constructEvent(body, sig, STRIPE_WEBHOOK_SECRET)
   → invalid? return 400 immediately
3. Handle event types:
   - checkout.session.completed → upsert subscription (active)
   - customer.subscription.deleted → update status = cancelled
   - invoice.payment_failed → log, optionally notify user
4. Return 200 always (Stripe retries on non-200)
```

### /create-checkout  ← MISSING, LAUNCH BLOCKER
```
1. Read Authorization header → verify JWT
2. Read body: { price_id }
   → validate price_id is one of the known price IDs
3. Stripe API: create Checkout Session
   - mode: 'subscription'
   - line_items: [{ price: price_id, quantity: 1 }]
   - metadata: { user_id: uid }       ← required for webhook to link payment to user
   - success_url: SITE_URL/cheatsheet.html?subscribed=1
   - cancel_url:  SITE_URL/cheatsheet.html
4. Return { url: session.url }
```

## Cache Strategy

```
Cache TTL by league:
  NBA / NFL props     15 minutes
  Soccer              30 minutes

Cost at launch:
  3 leagues × 96 refreshes/day = 288 API calls/day
  288 × 30 days = 8,640/mo → needs $30/mo plan (20K credits)
```

## User State Machine

```
[VISITOR] ──sign up──▶ [FREE] ──subscribe $99 MXN/liga──▶ [PAID]
              │                                               │
           see preview                                   full cheatsheet
           hit paywall                                   for that league
                                                              │
                                                         [CANCELLED]
                                                         loses access
                                                         at period end
```

## Failure Modes

```
CODEPATH           FAILURE              HANDLED?  USER SEES
─────────────────  ───────────────────  ────────  ──────────────────
/odds-proxy        No JWT               YES→401   Redirect to login
/odds-proxy        API timeout          YES       Cached data
/odds-proxy        API down + no cache  YES       Empty state UI
/odds-proxy        Wrong league sub     YES→403   Paywall
/stripe-webhook    Bad signature        YES→400   Rejected silently
/stripe-webhook    Duplicate event      YES       Idempotent no-op
DB                 RLS blocks query     YES       403 → logout
```

## Known Code Fixes (apply before launch)

These bugs exist in the scaffolded code and must be fixed before any user sees the product:

| File | Line | Issue | Fix |
|------|------|-------|-----|
| `cheatsheet.js` | 165 | `preloadPlayers()` never called | Call `await preloadPlayers(names)` inside `renderProps()` before `renderTable()` |
| `cheatsheet.js` | 255 | `extractTeam()` returns wrong team | Return `playerCache[p.player]?.team ?? '—'` instead of `event.home_team` |
| `cheatsheet.js` | init | No `?subscribed=1` handler | On `initCheatsheet`, check `URLSearchParams` and show success toast |
| `odds-proxy/index.ts` | 78 | Dead `league.eq.bundle` in `.or()` | Simplify to `.eq('league', league)` |
| `odds-proxy/index.ts` | 29 | Soccer = MLS | Change to Liga MX + Champions + EPL (see TODOS.md) |
| `odds-proxy/index.ts` | 33 | CORS wildcard | Set `ALLOWED_ORIGIN` env var, restrict to GitHub Pages domain at launch |
| All docs | — | Bundle price inconsistency | **$249 MXN** everywhere. Update PLAN.md and DESIGN.md. |

## NOT In Scope (MVP)

- Scraping Caliente/Draftea directly
- LATAM bookmaker data (v2 via Manolo's contacts)
- Live in-play odds
- Mobile app
- Community / forums
- Automated bet signals
- Injury reports
- Betting calculators

## First Prompt for Claude Code

> "Create a new project called bettor-latam. Vanilla HTML/CSS/JS frontend
> for GitHub Pages. Supabase backend with two edge functions: /odds-proxy
> and /stripe-webhook. DB schema with subscriptions (per-league, user_id,
> stripe_subscription_id, status, current_period_end) and cached_odds
> (league, market, data jsonb, fetched_at, expires_at) tables with RLS.
> Use npm:stripe and npm:@supabase/supabase-js imports in Deno edge functions."
