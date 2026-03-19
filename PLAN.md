# Bettor LATAM — Executive Plan
_Generated 2026-03-18_

## The Bet
bettoringreen.com is US-only, bad design, no Spanish. LATAM betting analytics market is wide open.

## Team
| | Andrés | Manolo |
|---|---|---|
| Role | Design + Build | Distribution + Industry |
| Equity | 50% | 50% |
| Brings | Product | Draftea network + tipsters |

## Deal
- Manolo pays Andrés **$60,000 MXN/mo × 3 months** while building
- If he stops paying → Andrés stops working. Equity stays 50/50.
- **Month 3 checkpoint:** 200 paying subscribers or renegotiate

## Stack
```
Frontend      GitHub Pages       free
Auth + DB     Supabase           free tier
Backend       Supabase Edge Fn   free tier
Payments      Stripe             2.9% + $0.30/charge
Odds data     The Odds API       $30/mo
```

## Monthly Costs
```
The Odds API       $30 USD   (~$530 MXN)
Claude Code        $20 USD   (~$350 MXN)
Domain             $15 USD   (~$265 MXN)
Zadarma (phone)    $10 USD   (~$175 MXN)
Supabase           $0
GitHub Pages       $0
Misc               $20 USD   (~$350 MXN)
────────────────────────────────────────
Total ops          ~$95 USD  (~$1,670 MXN)
```

## Pricing Model
- Per-league access: **$99 MXN/liga**
- Bundle (all leagues): **$199-299 MXN/mo**
- Break-even: ~700 subscribers

## Revenue Math
```
700 subscribers × $99 MXN = $69,300 MXN/mo
Your 50%                  = $34,650 MXN/mo
```

## Architecture
```
GitHub Pages (frontend)
        ↓
Supabase Edge Functions
  ├── /odds-proxy      → calls The Odds API, caches 15min
  └── /stripe-webhook  → marks user as paid in DB
        ↓
Supabase Postgres
  ├── users
  ├── subscriptions
  └── cached_odds
        ↓
The Odds API + Stripe
```

## User Flow
```
Visitor → Sign up (Supabase Auth)
        → See free preview
        → Hit paywall
        → Stripe checkout ($99 MXN/liga)
        → Webhook fires → subscription created
        → Full cheatsheet unlocked
```

## Timeline
```
Week 1-2   Supabase setup, auth, DB, odds integration, cheatsheet UI
Week 3     Stripe, paywall, per-league access, webhook
Week 4     Design polish, landing page, Spanish/English, soft launch
Month 2    Real users, fix bugs, add sports, Manolo activates tipsters + ads
Month 3    CHECKPOINT: 200 subscribers minimum
Month 6    700 subscribers = break-even
Month 12   B2B / white-label / direct LATAM bookmaker deals
```

## Distribution Plan
- Manolo activates tipster contacts from Draftea
- One contact: 46K followers → 1% conversion = 460 leads → ~46 paying
- Target: 10 tipsters on CPA deals ($50-100 MXN per subscriber)
- Month 2: IG + Twitter native ads ($2,500 USD/mo)

## NOT Building (MVP)
- Scraping Caliente/Draftea directly
- LATAM bookmaker data (v2 via Manolo's contacts)
- Live in-play odds
- Mobile app
- Community/forums
- Automated bet signals
- Injury reports
- Betting calculators

## Critical Risks
1. **Manolo doesn't activate his network** → no users, no revenue
2. **The Odds API lacks LATAM bookmakers** → US odds only for MVP, LATAM in v2
3. **Stripe webhook not verified** → anyone can fake payment → always use signature verification
4. **Cache + API both down** → show empty state, never a broken page
