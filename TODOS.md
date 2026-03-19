# TODOS — Bettor LATAM

Deferred work from /plan-ceo-review on 2026-03-17.

---

## T-001 · `create-checkout` edge function
**Priority:** P1 — LAUNCH BLOCKER
**Effort:** S (human: ~2h / CC: ~10min)
**Depends on:** Stripe products + price IDs created in Stripe dashboard first

**What:** Build `supabase/functions/create-checkout/index.ts`.

**Why:** `app.js:93` calls this function but it doesn't exist. Without it, clicking Subscribe throws an error and nobody can pay. The entire payment flow is broken.

**What it must do:**
1. Verify JWT (same pattern as odds-proxy)
2. Validate `price_id` is one of the known Stripe price IDs
3. Create a Stripe Checkout Session with `metadata: { user_id }` so the webhook can link the payment back to the Supabase user
4. Return `{ url: session.url }` — app.js redirects the browser there

**Context:** `stripe-webhook` already handles `checkout.session.completed` and reads `session.metadata.user_id`. That only works if create-checkout sets it. The two functions are coupled.

---

## T-002 · Player seed script
**Priority:** P1 — needed before launch for images to work
**Effort:** S (human: ~3h / CC: ~15min)
**Depends on:** Supabase project must be connected first

**What:** Build `scripts/seed-players.ts` — fetches players for launch sports and upserts into the `players` table with IDs and team info.

**Why:** Without it, `playerCache` is empty at launch, player images don't load (they fall back to sport emoji), and team names are wrong in the table.

**Launch sports (MVP):** MLB + Soccer (Liga MX, Champions League, Premier League, La Liga)
**Later (off-season):** NBA, NFL

**Sources:**
- MLB: Official MLB Stats API (free, no key) — `https://statsapi.mlb.com`
- Soccer: FBref or API-Football (pending T-006 decision)
- NBA/NFL: defer until in-season

**Run:** Once before launch, refresh each season start.

---

## T-003 · Move edge computation server-side
**Priority:** P2 — important before meaningful subscriber count
**Effort:** M (human: ~4h / CC: ~20min)
**Depends on:** T-004 (soccer multi-league) should be done first since it changes odds-proxy structure

**What:** Move `computeEdge()` + `calcEdge()` from `cheatsheet.js` into `odds-proxy/index.ts`. The API returns scored props `{ player, prop, line, edge, odds, direction }` instead of raw bookmaker data. Browser renders only.

**Why:** The edge formula (implied probability comparison vs bookmaker consensus) is the product's core IP. Any subscriber can currently open devtools and copy it. With server-side computation, raw odds are never in the browser.

**Impact on cheatsheet.js:** Removes ~80 lines. `renderProps()` receives pre-scored data directly. `computeEdge()`, `calcEdge()`, `americanToImplied()`, `marketLabel()` all move to the edge function.

---

## T-004 · Multi-league soccer (Liga MX + Champions League + Premier League + La Liga)
**Priority:** P1 — soccer is a launch sport
**Effort:** S (human: ~2h / CC: ~10min)
**Depends on:** Verify The Odds API covers all 4 leagues on your plan tier

**What:** Update `SPORT_KEYS` in `odds-proxy/index.ts` from `soccer_usa_mls` to support 4 leagues. Fetch and merge into one response.

**Why:** MLS has almost no LATAM audience. Liga MX + Champions League + Premier + La Liga is what the LATAM market actually watches and bets on.

**Credit impact:** 4 leagues × 48 credits = ~192/day for soccer. Total with MLB: ~240/day. Monthly: ~7,200 — well within the $30/mo plan (20K credits).

**API keys:**
- Liga MX: `soccer_mexico_ligamx`
- Champions League: `soccer_uefa_champs_league`
- Premier League: `soccer_epl`
- La Liga: `soccer_spain_la_liga`

**NBA/NFL:** Defer until in-season. Remove from active SPORT_KEYS for now.

---

## T-005 · Momios data pipeline — Firecrawl scraper for MX books
**Priority:** P2 — needed before we can show real prop data for MX books
**Effort:** M (human: ~1 week / CC: ~1h)

**What:** Build a Firecrawl-based scraper for Mexican sportsbooks to pull prop bet momios automatically. One scraper per book: Caliente, Bet365 MX, PlayDoit, Codere.

**Why:** None of the 4 MX books have public APIs. The Odds API does not cover MX books. Manual entry is not viable.

**Decision: Firecrawl (confirmed 2026-03-18)**
- Explored and ruled out: The Odds API (no MX books), parse.bot (browserless, blocked by Cloudflare), Riveter (wrong fit — research tool not scraper), OpticOdds (confirmed Codere only, Caliente missing)

**All 4 books confirmed working (tested 2026-03-18):**

| Book | Liga MX league URL | Method | Cost | Status |
|------|-------------------|--------|------|--------|
| Caliente | `https://sports.caliente.mx/es_MX/Liga-MX` | 1 Firecrawl call (executeJavascript click-all) | 1 credit/match | ✅ 140 markets, 1,077 selections |
| Codere | `https://apuestas.codere.mx/es_MX/t/45349/Liga-MX` | Plain requests.get + parallel web_nr fetches | $0 | ✅ 383 markets, 843 selections |
| 1Win | `https://1witeo.life/betting/prematch/soccer-18/liga-mx-44913` | Firecrawl + JS injection (top-parser API has no odds endpoint) | 1 credit | ✅ 60 live markets, 619 selections |
| PlayDoit | `https://www.playdoit.mx/#page=championship&championshipIds=10009` | Altenar GetEventDetails API (GET) | $0 | ✅ 308 markets, 1,700+ selections |

**URL discovery strategy (per book, once per matchday):**
- Caliente: Scrape `sports.caliente.mx/es_MX/Liga-MX` → extract `/es_MX/Liga-MX/{date}/{team1}-vs-{team2}` links
- Codere: Scrape league page → extract `/e/{id}/{slug}` match links
- Bet365 MX: Scrape league page → extract prematch event links
- PlayDoit: Scrape `#page=championship&championshipIds=10009` with 8s wait → extract event IDs (`#page=event&eventId=...`)

**Cost (Firecrawl):**
- 1 credit per page scrape
- 4 books × ~10 matches × ~24 refreshes per matchday = ~960 credits/matchday (+4 league listing pages/day)
- Hobby plan ($16/mo, 3,000 credits) covers MVP comfortably
- Scale to Standard ($83/mo, 100K credits) when needed

**Pre-match only (MVP).** No live/in-play momios at launch.

**Refresh cadence:** Every 2 hours starting 48h before kickoff. Props open ~24–48h before match.

**Remaining open questions:**
- Bet365 MX: verify full prop data loads on next matchday (URL confirmed, content empty today — matches went live)
- PlayDoit: validate individual match event URL scrape (e.g. `#page=event&eventId=...`) returns single-market props vs. SGP boosts only
- Legal/ToS risk of scraping each book (deferred — MVP decision)

---

## T-006 · Evaluate player stats API for edge calculation
**Priority:** P2 — needed to automate the right side of the edge formula
**Effort:** S (human: ~1 day / CC: ~20min)

**What:** Spike on affordable stats APIs that cover Liga MX player data.

**Why:** The edge formula needs two inputs — momios (The Odds API) and player stats (goals, shots, minutes played). Without a stats source, edge calculation is manual or impossible.

**Ruled out (too expensive for MVP):**
- Opta / Stats Perform: $50K–$200K+/year
- StatsBomb: $20K–$50K/year (free tier is non-commercial, Liga MX coverage poor)

**MLB:** Free official API, no key needed. `https://statsapi.mlb.com` — hits, HR, RBI, K's, ERA, innings pitched. This is solved.

**Soccer candidates to evaluate:**
- **API-Football** (api-football.com) — ~$10–30/mo, has Liga MX, returns goals/shots/minutes per match. Likely MVP winner.
- **SportMonks** — ~$30–50/mo, decent Liga MX coverage
- **FBref scraping** — free, good Liga MX coverage, no official API
- **SofaScore scraping** — free, good coverage, no official API

**Decision criteria for soccer:** Liga MX player stats per match, price, reliability, xG if available.

**Note:** xG (Expected Goals) is ideal but not required for MVP. Goals per 90 + shots per 90 + minutes played is enough to beat casual bettor knowledge.
