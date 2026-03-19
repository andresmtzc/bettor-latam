# Design System — Bettor LATAM
_Captured 2026-03-18 by /plan-design-review_

## Product Context
**What it is:** LATAM-first sports betting analytics tool. Bettors pay to see prop bet edge data — whether a player's historical stats suggest a sportsbook line is beatable.

**Who uses it:** Spanish-speaking bettors in Mexico, Colombia, Argentina. They check this on their phone at night, right before placing a bet. They need fast, credible, scannable data.

**Design north star:** Bloomberg Terminal meets Vercel dashboard. A trading tool, not a sports blog.

**Competitive gap:** bettoringreen (cream, serif, cartoonish) and Action Network (cluttered news) both look like 2019 sports websites. Nobody has built a modern, dark, data-first betting analytics tool for LATAM. That's the opening.

---

## Color

```css
/* Backgrounds */
--color-bg:           #0A0A0B;  /* near-black, warm undertone */
--color-surface:      #111113;  /* cards, tables, modals */
--color-surface-2:    #18181B;  /* elevated cards */
--color-border:       #1E1E21;  /* dividers, table borders */

/* Text */
--color-text:         #E8E8E8;  /* primary — readable, not harsh white */
--color-text-muted:   #8B8B94;  /* labels, metadata, secondary */
--color-text-faint:   #52525B;  /* placeholders, disabled */

/* Semantic — functional, not decorative */
--color-edge-pos:     #22C55E;  /* edge found → bet this */
--color-edge-neg:     #EF4444;  /* avoid this line */
--color-edge-neutral: #8B8B94;  /* no significant edge */

/* Accent */
--color-accent:       #F59E0B;  /* amber/gold — CTAs, highlights, money */
--color-accent-hover: #D97706;  /* hover state */
```

**Why amber, not green:** bettoringreen owns green. Amber reads as premium and money — right for a paid tool.

**Never use:** purple, indigo, violet, blue-to-purple gradients.

---

## Typography

```css
/* Fonts */
--font-ui:      'Inter', system-ui, sans-serif;     /* all UI text */
--font-mono:    'JetBrains Mono', monospace;         /* ALL numbers */

/* Scale */
--text-xs:    12px;   /* labels, fine print */
--text-sm:    14px;   /* table data, metadata */
--text-base:  16px;   /* body, descriptions */
--text-lg:    18px;   /* card titles */
--text-xl:    24px;   /* section headings */
--text-2xl:   32px;   /* page headings */
--text-hero:  48px+;  /* landing page hero only */

/* Rules */
/* Every number (odds, lines, percentages, stats) → font-mono */
/* font-variant-numeric: tabular-nums on all number columns */
/* Body line-height: 1.5 */
/* Heading line-height: 1.15 */
/* Body max-width: 65ch */
```

---

## Spacing

Base unit: 4px. All spacing is multiples of 4.

```
4px   — tight: icon gaps, inline spacing
8px   — small: within components
12px  — medium-small: card padding mobile
16px  — base: standard component padding
24px  — medium: section sub-spacing
32px  — large: between cards
48px  — xlarge: section gaps mobile
64px  — section gaps desktop
96px  — hero sections
```

---

## Components

### Prop Table Row
```
┌─────────────────────────────────────────────────────────────┐
│  [photo] JUGADOR      PROP        LÍNEA   EDGE    MOMIO     │
├─────────────────────────────────────────────────────────────┤
│  [img]   LeBron James  Puntos      27.5   +8% ▲  Over -115  │
│          Lakers · NBA              mono   green   mono       │
└─────────────────────────────────────────────────────────────┘

- Edge column: green if positive, red if negative, muted if <3%
- All numbers: JetBrains Mono, tabular-nums
- Row hover: background slightly lighter (#1A1A1D)
- Hot props (edge > 10%): 🔥 icon or amber highlight on row
```

### Paywall Blur
```
/* Free users see 3 rows clearly, rest is blurred */
.paywall-row {
  filter: blur(4px);
  user-select: none;
  pointer-events: none;
}
/* Overlay CTA centered on blurred section */
```

### Edge Badge
```
▲ +12%   → green background, white text
▼ −3%    → red background, white text
  −0%    → muted, no background
🔥 +15%  → amber highlight, hot prop
```

### CTA Button
```css
.btn-primary {
  background: #F59E0B;
  color: #0A0A0B;       /* dark text on amber */
  font-weight: 700;
  border-radius: 8px;
  padding: 12px 24px;
  min-height: 44px;     /* touch target */
}
.btn-primary:hover { background: #D97706; }
```

---

## Page Layouts

### Landing (`index.html`)
```
NAV:     Logo left | Login right | "Empieza gratis" amber CTA
HERO:    Left-aligned (not centered) big claim in Spanish
         Subtext 1-2 lines
         Email input + "Empezar" button
         Below fold: blurred table preview
PROOF:   Live counter ("47 props analizados hoy")
         3 short testimonials, no avatars
PRICING: 2 cards — per-liga $99 MXN | bundle $299 MXN
FOOTER:  Minimal — logo, links, legal
```

**Hero copy options:**
- "Los casinos saben más que tú. Nosotros también."
- "Para de adivinar. Empieza a ganar con datos."
- "La ventaja que los casinos no quieren que tengas."

### Cheatsheet (`cheatsheet.html`)
```
HEADER:  Date: "Hoy, 18 Mar · 47 props"
         Quick filter pills: [⚽ Fútbol] [⚾ MLB]  ← temporary override only
LIST:    One flat list, sorted by best single prop edge per match
         Match is the headline row
           → Chivas vs América · Hoy 8pm · ★★★ (avg edge)
         Props always visible underneath, no tap to expand
           → Armando González anota  +235  12% edge
           → Over 2.5 goles          -115   7% edge
         Scroll down = weaker edge matches
MOBILE:  Same list, card-style per match
         Sticky "Desbloquea" CTA at bottom for free users
```

**Cheatsheet layout — ASCII mockup:**
```
┌─────────────────────────┬──┐
│  bettor          [@]    │[C]│
│  Hoy, 18 Mar · 12 props │[A]│
│  [⚽] [⚾]              │[L]│
├─────────────────────────┤[T]│
│  Chivas vs América      │[A]│
│  Hoy 8pm · Liga MX      │[P]│
│                         │[M]│
│  Armando +260  ▲+12%    │[T]│
│  Over 2.5  -108  ▲+7%   │[R]│
│  BTTS  -134  ▲+5%       │[G]│
├─────────────────────────┤   │
│  León vs Tigres         │   │
│  Hoy 6pm · Liga MX      │   │
│                         │   │
│  Cambindo +700  ▲+9%    │   │
│  Over 1.5  +120  ▲+6%   │   │
│  BTTS  -118  ▲+4%       │   │
└─────────────────────────┴──┘
```
- Right column = fixed logo sidebar (does not scroll with content)
- All today's matches visible as logos at all times — acts as a visual scrollbar
- Each logo is 24x24px circle, uniform size regardless of crest complexity
- Tap a logo → jumps to that match
- Content scrolls on the left independently
- 3 props per match card by default (top 3 by edge)
- Tap a match → expands to show all props + full book breakdown

**Per-prop book comparison (expanded view):**
```
Armando González — Anota en cualquier momento
Chivas vs América · Hoy 8pm

Bet365      +260  ← top = best
PlayDoit    +250
Caliente    +235
Codere      +220
```
- Books sorted best momio to worst — position tells the story, no label needed
- User sees at a glance where to place the bet
- This is the core product: edge + best book in one view

**Empty state:**
- Only show matches once props are live (24–48h before kickoff)
- No "coming soon" rows, no placeholders
- If no props available: *"No hay props disponibles aún. Vuelve mañana."*

**Sport preferences (onboarding, set once):**
- User toggles sports on/off during signup — soccer on, MLB off, etc.
- Feed only shows selected sports — no tabs, no switching
- Quick filter pills at top are temporary session overrides, not navigation
- Preference saved to user profile, persists across sessions

### Player Profile (`/jugador/:id`)
```
┌─────────────────────────────┐
│  ← Armando González         │
│     Chivas · Delantero      │
├─────────────────────────────┤
│  [5] [10] [20]  [L] [V]     │
├─────────────────────────────┤
│        ANOTÓ                │
│  U5    ██░░░░  2/5   40%    │
│  U10   ████░░  4/10  40%    │
│  U20   ██████  9/20  45%    │
│                             │
│  Local   ████░░  4/8  50%   │
│  Visita  ██░░░░  2/8  25%   │
│                             │
│  Mañana  █░░░░░  1/5  20%   │
│  Tarde   ████░░  4/7  57%   │
│  Noche   ███░░░  3/8  37%   │
│                             │
│  Vie-Dom ████░░  4/9  44%   │
├─────────────────────────────┤
│  Book Bet365        +260    │
│  Implica                28% │
│  Tus stats              44% │
│  ▲ Edge                +16% │
├─────────────────────────────┤
│  Proyección  ◀───●───▶  44% │
│  Edge ajustado      ▲ +16%  │
└─────────────────────────────┘
```
- All splits visible at once — no tapping to reveal
- Bar charts show scored/total visually
- Splits: last 5/10/20, local/visita, mañana/tarde/noche, day of week
- Book line + implied probability shown vs stats probability
- Slider for user to adjust today's projection → updates edge in real time
- Tap from cheatsheet prop row to reach this screen

---

## AI Slop — Never Do This

- Purple/indigo/violet gradients ✗
- 3-column icon grid (icon-circle + title + 2-line desc) ✗
- Centered everything on landing page ✗
- Cartoonish mascot or character ✗
- Wavy SVG dividers or decorative blobs ✗
- Emoji in headings (🚀 🎯 ✨) ✗
- Colored left-border cards ✗
- "Unlock the power of..." copy ✗
- Same large border-radius on every element ✗
- Light cream background (bettoringreen already owns it) ✗

---

## Responsive

**Mobile first.** Design for 375px, then scale up.

```
375px  Mobile   Card layout, bottom sticky CTA, large tap targets
768px  Tablet   Hybrid — table visible, side filters
1024px Desktop  Full table, all columns, hover states
1440px Wide     Max content width 1280px, nothing stretches wider
```

**Touch targets:** All interactive elements ≥ 44px height.

---

## Motion

- Duration: 150ms for micro-interactions, 250ms for transitions
- Easing: ease-out entering, ease-in exiting
- Edge badge color change: fade 150ms
- Table row hover: background transition 100ms
- `prefers-reduced-motion`: respect it, remove all transitions
- Never animate layout properties (width, height, top) — only transform + opacity

---

## Images & Media

### Player Headshots
```
NBA   https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
      Free, no API key, high quality (confirmed working)

NFL   https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/{espn_id}.png
      Free public CDN (ESPN)
```

### Team Logos
```
NBA   https://a.espncdn.com/i/teamlogos/nba/500/{abbrev}.png
NFL   https://a.espncdn.com/i/teamlogos/nfl/500/{abbrev}.png
⚽    TheSportsDB API → strBadge field (free, covers Liga MX + all major leagues)
```

### Player ID Mapping
The Odds API returns player names as strings. Supabase `players` table maps names to IDs:
```
players: id, name, sport, nba_id, espn_id, team, team_abbrev
```
Build this table incrementally as new players appear in odds data.

### Fallback
If player has no headshot: show team logo in the avatar circle.
If team has no logo: show sport icon (🏀 🏈 ⚽).
Never show a broken image — always have a fallback.

### Prop Table Row Design
```
[40px circular headshot]  Player Name     PROP    LINE    EDGE    MOMIO
                          Team · Sport    mono    mono    color   mono
```
Player photo cropped to circle, 40px on mobile, 48px on desktop.
Team logo shown in sport tab headers, not in every row.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-18 | Dark mode native | Bettors use this at night on phones |
| 2026-03-18 | Amber/gold accent | bettoringreen owns green; gold = premium |
| 2026-03-18 | JetBrains Mono for numbers | Tabular alignment, trading terminal feel |
| 2026-03-18 | Left-aligned hero (not centered) | Avoid AI slop, more editorial and confident |
| 2026-03-18 | No mascot/illustrations | bettoringreen's biggest weakness, avoid entirely |
| 2026-03-18 | Mobile-first | Primary use case: phone, night, bar |
| 2026-03-18 | No sport tabs — feed model instead | Bettors only care about their sports; tabs force choice every session |
| 2026-03-18 | List sorted by best single prop edge per match | A 15% edge prop beats 5 props at 5% — quality over quantity |
| 2026-03-18 | Props always visible under match — no expand tap | Reduces friction; user scans the whole picture at once |
| 2026-03-18 | Sport filter = onboarding preference, not navigation | Set once, forget it; quick filter pills for temporary session override only |
