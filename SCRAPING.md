# Scraping Playbook — MX Sportsbooks

How to scrape all props/markets from PlayDoit, Codere, Caliente, and 1Win for any event.

---

## Summary

| Book | Front door | What we actually hit | Method | Time | Cost | Markets |
|------|-----------|---------------------|--------|------|------|---------|
| **PlayDoit** | Cloudflare-protected | Altenar API on biahosted.com — no auth | Direct HTTP | ~1-2s | $0 | ~233 |
| **Codere** | Would need browser | SSR HTML — odds pre-rendered, plain HTTP | Direct HTTP (1 call) | ~1-2s | $0 | ~165 |
| **Caliente** | Cloudflare blocks | Firecrawl — odds SSR pre-rendered, no actions | Firecrawl (wait 3s) | ~3-4s | 1 credit | ~263 |
| **1Win** | Cloudflare + Oracle blocked | api-gateway.top-parser.com WebSocket — unprotected | WebSocket | ~2s | $0 | ~57 |

All 4 run in parallel. Total wall time ~3-4s (Caliente/Firecrawl is bottleneck). Only Caliente costs money.

---

# PlayDoit

How to scrape all props/markets from PlayDoit (Altenar sportsbook) for any event.

---

## The Problem

PlayDoit (`playdoit.mx`) is behind Cloudflare and blocks headless browsers by IP.
The sports betting widget is loaded inside a cross-origin iframe from `biahosted.com`.
Most Altenar API endpoints return 401 or empty without auth — **except one**.

---

## The Solution

The `GetEventDetails` endpoint on `sb2frontend-altenar2.biahosted.com` returns
**all 300+ markets and 1700+ odds in a single call**, no auth required.

### Step 1 — Find the eventId

On PlayDoit, click any event. The URL hash will look like:
```
https://www.playdoit.mx/#page=event&eventId=14983336&sportId=66
```
The `eventId` is what you need (`14983336`).

### Step 2 — Call the API

```bash
curl -s "https://sb2frontend-altenar2.biahosted.com/api/widget/GetEventDetails?\
culture=es-ES&timezoneOffset=240&integration=playdoit2&deviceType=1&\
numFormat=en-GB&countryCode=US&eventId=14983336&showNonBoosts=false" \
> /tmp/event-details.json
```

No cookies, no auth headers, no Cloudflare bypass needed. Just a plain GET.

### Step 3 — Parse the response

The response is ~1.2MB JSON with these top-level keys:
- `marketGroups` — 14 tab groups (Principal, 1ª mitad, Tarjetas, etc.)
- `markets` — 308 market definitions (each has `desktopOddIds`)
- `odds` — 5456 individual selections with `price` (decimal format)

```python
import json

d = json.load(open('/tmp/event-details.json'))

odds_map   = {o['id']: o for o in d['odds']}
markets_map = {m['id']: m for m in d['markets']}

def decimal_to_american(dec):
    if dec >= 2.0:
        return f"+{int(round((dec - 1) * 100))}"
    else:
        return f"{int(round(-100 / (dec - 1)))}"

for group in d['marketGroups']:
    print(f"\n=== {group['name']} ===")
    for mid in group.get('marketIds', []):
        market = markets_map.get(mid)
        if not market:
            continue
        # desktopOddIds is a list of columns (lists) of odd IDs
        all_odd_ids = []
        seen = set()
        for col in market.get('desktopOddIds', []):
            for oid in col:
                if oid not in seen:
                    seen.add(oid)
                    all_odd_ids.append(oid)
        if not all_odd_ids:
            continue
        print(f"\n  {market['name']}")
        for oid in all_odd_ids:
            odd = odds_map.get(oid)
            if odd:
                print(f"    {odd['name']}: {decimal_to_american(odd['price'])}")
```

---

## Key API Params

| Param | Value | Notes |
|-------|-------|-------|
| `culture` | `es-ES` | Language |
| `timezoneOffset` | `240` | Minutes from UTC |
| `integration` | `playdoit2` | PlayDoit's skin ID |
| `deviceType` | `1` | 1=desktop, 2=mobile |
| `numFormat` | `en-GB` | Number formatting |
| `countryCode` | `US` | Doesn't affect data |
| `eventId` | `{id from URL hash}` | The event to scrape |
| `showNonBoosts` | `false` | Include all markets |

---

## Market Groups

From the "Todas" tab:

| ID | Name |
|----|------|
| 1 | Principal |
| 1629 | Insights |
| 23 | Crear Apuesta |
| 17 | Especiales por jugador |
| 5 | Tiros esquina |
| 1648 | Flash⚡ |
| 19 | Mercados Rápidos |
| 18 | 1 minuto |
| 1589 | Equipo H2H |
| 1050 | Extra |
| 2 | 1ª mitad |
| 3 | 2ª mitad |
| 6 | Tarjetas |
| 4 | Combinación |

---

## Odds Format

Prices are in **decimal** format. Convert to American:

```python
# Decimal → American
if decimal >= 2.0:
    american = f"+{int(round((decimal - 1) * 100))}"
else:
    american = f"{int(round(-100 / (decimal - 1)))}"

# Examples:
# 1.375 → -267
# 5.333 → +433
# 7.5   → +650
```

---

## Full Script (copy-paste ready)

```python
import json, urllib.request

EVENT_ID = 14983336  # Change this for each event

url = (
    f"https://sb2frontend-altenar2.biahosted.com/api/widget/GetEventDetails"
    f"?culture=es-ES&timezoneOffset=240&integration=playdoit2"
    f"&deviceType=1&numFormat=en-GB&countryCode=US"
    f"&eventId={EVENT_ID}&showNonBoosts=false"
)

with urllib.request.urlopen(url) as resp:
    d = json.load(resp)

odds_map    = {o['id']: o for o in d['odds']}
markets_map = {m['id']: m for m in d['markets']}

def american(dec):
    if dec >= 2.0: return f"+{int(round((dec-1)*100))}"
    return f"{int(round(-100/(dec-1)))}"

rows = []
for group in d['marketGroups']:
    for mid in group.get('marketIds', []):
        mkt = markets_map.get(mid)
        if not mkt: continue
        seen = set()
        for col in mkt.get('desktopOddIds', []):
            for oid in col:
                if oid in seen: continue
                seen.add(oid)
                odd = odds_map.get(oid)
                if odd:
                    rows.append({
                        'group':   group['name'],
                        'market':  mkt['name'],
                        'outcome': odd['name'],
                        'american': american(odd['price']),
                        'decimal': odd['price'],
                    })

print(f"Scraped {len(rows)} odds across {len(d['marketGroups'])} groups")
# rows is now a list of dicts ready for Supabase / CSV / whatever
```

---

## Player Props (Goleador, etc.)

Player prop markets use **`childMarketIds`** instead of `desktopOddIds`.
Each child market = one player. The response includes a top-level `childMarkets` array.

```python
child_markets = {m['id']: m for m in d.get('childMarkets', [])}

for group in d['marketGroups']:
    for mid in group.get('marketIds', []):
        mkt = markets_map.get(mid)
        if not mkt or not mkt.get('childMarketIds'):
            continue

        # Column headers (e.g. "Primero", "Último", "Cualq. Momen")
        col_names = []
        if mkt.get('headers'):
            col_names = [o['name'] for o in mkt['headers'][0]['odds']]

        print(f"\n{mkt['name']} — columns: {col_names}")
        for cid in mkt['childMarketIds']:
            cm = child_markets.get(cid, {})
            player = cm.get('childName', '?')
            team_id = cm.get('competitorId')  # 46420=Chivas, 47213=León

            # Each column is a list; take first odd in each
            prices = []
            for col in cm.get('desktopOddIds', []):
                oid = col[0] if col else None
                odd = odds_map.get(oid, {})
                p = odd.get('price', 0)
                prices.append(american(p) if p > 1 else '-')

            print(f"  {player} (team={team_id}): {prices}")
```

**Competitor IDs for this event:**
- `46420` = Guadalajara Chivas
- `47213` = Club Leon

---

## Notes

- **No Cloudflare bypass needed** — this API endpoint is publicly accessible.
- **No Firecrawl credits used** — plain HTTP GET.
- Response is ~1.2MB so cache it; don't call more than once per event per ~5 min.
- Player props (Goleador, Multigoleadores, etc.) are in `childMarkets`, not `markets`.
- Standard markets use `desktopOddIds`; player markets use `childMarketIds`.
- Odds format: `oddStatus: 0` = active, `1` = suspended.

---

# Codere

How to scrape all props/markets from Codere (Geneity/OpenBet platform) for any event.

---

## The Platform

Codere (`apuestas.codere.mx`) uses the **Geneity/OpenBet** platform. Same as Caliente.
No Cloudflare blocking, no auth, no cookies needed.

**Key insight:** Odds are pre-rendered in the SSR HTML — one plain `requests.get` with `?show_all=Y`
gets everything. We used to fire ~143 parallel `web_nr` calls (one per market) but that's unnecessary.
Same regex parsing approach as Caliente works directly on the full page HTML.

---

## Step 1 — Find the event URL

Navigate to the event page. The URL format is:
```
https://apuestas.codere.mx/es_MX/e/{ev_id}/{slug}
```
Example:
```
https://apuestas.codere.mx/es_MX/e/12125471/Chivas-Guadalajara-v-Le%C3%B3n
```

The `ev_id` is `12125471`. It's embedded in the page as `Geneity.Page.page_args.ev_id`.

**Liga MX league page** (to discover event IDs):
```
https://apuestas.codere.mx/es_MX/t/45349/Liga-MX
```

---

## Step 2 — Get all market IDs + names from `?show_all=Y`

No browser needed — the page is SSR (server-side rendered). Plain `requests.get` returns all
383 market accordion headers in the HTML, even though their content is empty.

```python
import requests, re

BASE = "https://apuestas.codere.mx"
EV_ID = 12125471

# Fetch the show_all page
r = requests.get(
    f"{BASE}/es_MX/e/{EV_ID}/Chivas-Guadalajara-v-Le%C3%B3n?show_all=Y",
    headers={"User-Agent": "Mozilla/5.0"}
)
page_html = r.text

# Extract all market IDs (some elements have comma-separated IDs)
all_market_ids = set()
for raw in re.findall(r'data-mkt_id="([^"]+)"', page_html):
    for mid in raw.split(','):
        all_market_ids.add(mid.strip())

# Extract market names (mkt-name span inside each accordion h6)
id_to_name = {}
for m in re.finditer(
    r'data-mkt_id="([^"]+)"[^>]*>.*?class="mkt-name">([^<]+)',
    page_html, re.DOTALL
):
    name = m.group(2).strip()
    for mid in m.group(1).split(','):
        id_to_name[mid.strip()] = name

print(f"{len(all_market_ids)} market IDs, {len(id_to_name)} named")
```

**Special case — goalscorer table IDs:** Three IDs share one `.mkt` wrapper
(`data-mkt_id="486761944,486762009,486762008"`). Their real column names come from the PLAYR tab:

```python
r2 = requests.get(f"{BASE}/es_MX/e/{EV_ID}/...?mkt_grp_code=PLAYR",
                  headers={"User-Agent": "Mozilla/5.0"})
for m in re.finditer(
    r'class="mkt-sort-title">([^<]+)</[^>]+>.*?data-mkt_ids="([^"]+)"',
    r2.text, re.DOTALL
):
    for mid in m.group(2).split(','):
        id_to_name[mid.strip()] = m.group(1).strip()
# Goalscorer IDs → Primer Goleador / Marca Gol Durante el Partido / Hat Trick
```

## Step 3 — Fetch all market content via parallel curl

```python
import json, urllib.request, concurrent.futures

def fetch_market(mkt_id):
    url = f"{BASE}/web_nr?key=sportsbook.cms.handlers.get_mkt_content&mkt_id={mkt_id}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return mkt_id, json.load(r).get('html', '')

# ~382 parallel fetches — each ~1-3KB, total ~500KB, done in ~5s
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    htmls = dict(ex.map(fetch_market, all_market_ids))
```

No Cloudflare. No auth. Plain HTTP. Total cost: $0.

## Step 4 — Parse each HTML snippet

From each response `html`:
- Selection name: `.seln-name` span, or `.seln-draw-label` (for X/draw), or `title` attr on button
- American odds: `.price.us` span (skip `.was-price` variants)
- Skip buttons with `data-priced="N"` or price text `N/A`

**Player milestones** (Tiros, Asistencias, Titular+Goles): use a CSS grid in the main page DOM,
not covered by `get_mkt_content`. Extract from `?show_all=Y` DOM:
- `.player-list > .players-column` = player names
- `.seln-list .seln-wrapper` buttons, N per player (N = CSS `--num-columns`)
- Column headers = `.seln-list .header-row` divs (1+, 2+, etc.)

---

## Full Script (copy-paste ready)

```python
import json, re, requests, concurrent.futures
from html.parser import HTMLParser

EV_ID   = 12125471
EV_SLUG = "Chivas-Guadalajara-v-Le%C3%B3n"
BASE    = "https://apuestas.codere.mx"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- Step 1: market IDs + names ---
page_html = requests.get(f"{BASE}/es_MX/e/{EV_ID}/{EV_SLUG}?show_all=Y", headers=HEADERS).text
all_market_ids = set()
id_to_name = {}
for raw in re.findall(r'data-mkt_id="([^"]+)"', page_html):
    for mid in raw.split(','):
        all_market_ids.add(mid.strip())
for m in re.finditer(r'data-mkt_id="([^"]+)"[^>]*>.*?class="mkt-name">([^<]+)', page_html, re.DOTALL):
    for mid in m.group(1).split(','):
        id_to_name[mid.strip()] = m.group(2).strip()

# --- Step 2: fetch all market content in parallel ---
def fetch_market(mkt_id):
    url = f"{BASE}/web_nr?key=sportsbook.cms.handlers.get_mkt_content&mkt_id={mkt_id}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return mkt_id, json.load(r).get('html', '')

with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    htmls = dict(ex.map(fetch_market, all_market_ids))

# --- Step 3: Parse HTML snippet ---
class MktParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_btn = False
        self._btn_title = None
        self._in_seln_name = False
        self._in_draw_label = False
        self._in_us_price = False
        self._cur_seln = None
        self._cur_price = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button' and attrs.get('name') == 'add-to-slip':
            self._in_btn = True
            self._btn_title = attrs.get('title','').strip()
            self._cur_seln = self._cur_price = None
        if self._in_btn and tag == 'span':
            cls = attrs.get('class','').split()
            if 'seln-name' in cls: self._in_seln_name = True
            if 'seln-draw-label' in cls: self._in_draw_label = True
            if 'price' in cls and 'us' in cls:
                self._in_us_price = 'was-price' not in cls

    def handle_endtag(self, tag):
        if tag == 'button' and self._in_btn:
            self._in_btn = False
            if self._cur_price and self._cur_price != 'N/A':
                seln = self._cur_seln or self._btn_title or 'UNKNOWN'
                self.results.append({'selection': seln.strip(), 'american': self._cur_price})
        if tag == 'span':
            self._in_seln_name = self._in_draw_label = self._in_us_price = False

    def handle_data(self, data):
        d = data.strip()
        if self._in_seln_name: self._cur_seln = d
        elif self._in_draw_label and not self._cur_seln: self._cur_seln = d
        elif self._in_us_price: self._cur_price = d

# Parse all
rows = []
for mkt_id, html in htmls.items():
    mkt_name = id_to_name.get(mkt_id, f'Market_{mkt_id}')
    p = MktParser()
    p.feed(html)
    for r in p.results:
        rows.append({'market': mkt_name, **r})

print(f"Scraped {len(rows)} selections across {len(set(r['market'] for r in rows))} markets")
```

---

## Market Groups (from tab nav)

| Tab | Count | URL param |
|-----|-------|-----------|
| Principales | 93 | (default) |
| Crea tu Apuesta | 178 | `?betbuilder_toggle=Y` |
| Goles | 28 | `?mkt_grp_code=GLSCR` |
| Handicap | 12 | `?mkt_grp_code=HACAP` |
| Equipos | 42 | `?mkt_grp_code=TEAMS` |
| Especiales Jugadores | 47 | `?mkt_grp_code=PLAYR_SP` |
| 1ª Parte | 27 | `?mkt_grp_code=FHALF` |
| 2ª Parte | 18 | `?mkt_grp_code=SHALF` |
| Goleadores | 37 | `?mkt_grp_code=PLAYR` |
| Corners | 24 | `?mkt_grp_code=CRNR` |
| Tarjetas | 7 | `?mkt_grp_code=CARD` |
| Próximos Minutos | 27 | `?mkt_grp_code=NXMIN` |
| Wincast | 13 | `?mkt_grp_code=WINCAST` |
| Combinados | 30 | `?mkt_grp_code=COMBI` |
| Estadísticas | 8 | `?mkt_grp_code=STATS` |
| Asistencias | 32 | `?mkt_grp_code=ASST` |
| Tiros | 32 | `?mkt_grp_code=SHOT` |
| Tiros a Puerta | 32 | `?mkt_grp_code=SOT` |
| **Todos** | **383** | `?show_all=Y` |

---

## Notes

- **No Cloudflare blocking** — plain `requests.get` and `curl` both work. No browser needed at all.
- **No auth needed** — page loads without cookies.
- **Both steps are plain HTTP** — Step 1 is `requests.get` on the SSR page, Step 2 is parallel curl. Total cost: $0.
- **American odds pre-formatted** — `.price.us` spans (e.g. `-271`, `+380`). No conversion needed.
- Platform: Geneity/OpenBet. Internal event key: `Geneity.Page.page_args.ev_id`.
- Player milestones (Tiros, Asistencias, etc.) must be extracted from DOM — `get_mkt_content` doesn't cover these.
- Goalscorer table IDs share one `.mkt` wrapper with comma-separated `data-mkt_id`. Get real column names from `?mkt_grp_code=PLAYR`.
- Confirmed: ~1446 selections, 229 markets scraped for Chivas vs León (2026-03-18).
- Push updates via WebSocket: `wss://sports-push.codere.mx/`

## Anytime Scorer — Market Name

`Marca Gol Durante el Partido` — true anytime scorer (scores at any point, regardless of result).

---

# Caliente

How to scrape all props/markets from Caliente (Geneity/OpenBet platform) for any event.

---

## The Platform

Caliente (`sports.caliente.mx`) uses the same **Geneity/OpenBet** platform as Codere.
Cloudflare blocks direct curl and headless browsers from Oracle's IP.

**Cost: 1 Firecrawl credit per scrape. ~3-4s. ~263 markets.**

Key insight: **odds are pre-rendered in the SSR HTML** — no JavaScript execution, no accordion
clicking needed. Firecrawl with `wait: 3000` and no actions gets everything in one shot.

We tested Playwright on Oracle (clicking all expanders, 4s wait) — it was slow (~15s) and
returned *fewer* markets (166) than Firecrawl with no actions (263). Don't use Playwright for Caliente.

---

## The Approach — Firecrawl, no actions

```python
import requests, re

def scrape_caliente(url):
    page_url = re.sub(r'\?.*', '', url) + '?show_all=Y'
    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {FC_KEY}", "Content-Type": "application/json"},
        json={"url": page_url, "formats": ["rawHtml"], "actions": [{"type": "wait", "milliseconds": 3000}]},
        timeout=30
    )
    resp.raise_for_status()
    html = resp.json()['data']['rawHtml']
    # parse with same Geneity regex as Codere
```

---

## Parse all markets from the HTML

Same Geneity/OpenBet HTML structure as Codere. Container pattern:
`<div class="... mkt ..." data-mkt_id="{ID}">` → `<div class="expander-content">`.

```python
import re

mkt_re = re.compile(
    r'<div class="[^"]*\bmkt\b[^"]*"\s+data-mkt_id="(\d+)"\s*(?:data-fetch_url="[^"]*")?>'
)
btn_re = re.compile(r'<button[^>]*>(.*?)</button>', re.DOTALL)
positions = [(m.group(1), m.start()) for m in mkt_re.finditer(html)]

for i, (mkt_id, start) in enumerate(positions):
    end = positions[i+1][1] if i+1 < len(positions) else len(html)
    content = html[start:end]

    name_m = re.search(r'class="mkt-name">([^<]+)', content)
    name = name_m.group(1).strip() if name_m else 'UNKNOWN'

    for btn in btn_re.finditer(content):
        b = btn.group(1)
        seln_name = re.search(r'class="seln-name">([^<]+)', b)
        seln_draw = re.search(r'class="seln-draw-label">([^<]+)', b)
        seln_hcap = re.search(r'class="seln-hcap">([^<]+)', b)
        price_us  = re.search(r'class="price us"[^>]*>([^<]+)', b)
        if price_us:
            n = seln_name or seln_draw
            if n:
                full_name = n.group(1).strip()
                if seln_hcap and seln_name:
                    full_name += f" ({seln_hcap.group(1).strip()})"
                # selection: full_name, american: price_us.group(1).strip()
```

---

## Key Notes

- **1 Firecrawl credit per scrape.** ~3-4s total.
- **Odds are SSR pre-rendered** — no JS execution or accordion clicking needed. `wait: 3000` is just a safety buffer.
- **Don't use Playwright** — it's slower (~15s) and gets fewer markets (166 vs 263) because it only captures what's visible after expander clicks, not the full SSR payload.
- **American odds**: pre-formatted in `.price.us` spans. No conversion needed.
- **Event URL**: strip query params and re-add `?show_all=Y`.
- **Confirmed**: 263 markets (Firecrawl, no actions, 2026-03-19).

---

## Anytime Scorer — Market Name

Caliente does **not** offer a true anytime scorer market. What they have instead:

| Market | What it means | Comparable? |
|--------|--------------|-------------|
| `Jugador Goles Exactos (Gol 1)` | Exactly 1 goal (excludes braces) | ❌ No |
| `Jugador anota gol y gana` | Scores AND his team wins | ❌ No (parlay) |
| `Jugador anota gol y empate` | Scores AND draw | ❌ No |
| `Jugador anota gol y pierde` | Scores AND his team loses | ❌ No |
| `Próximo Anotador (Gol 1)` | Next scorer (live only, appears post-kickoff) | ❌ No |

**Use `—` for Caliente in anytime scorer line shopping tables. Do not compute edge.**

---

# 1Win

How to scrape all props/markets from 1Win (top-parser.com platform) for any event.

---

## The Platform

1Win MX (`1witeo.life`) is a SPA backed by **`api-gateway.top-parser.com`** — a third-party odds
aggregation platform. Odds are delivered in real-time via **Socket.IO WebSocket**, not REST API.

**No Cloudflare blocking. No Firecrawl. Cost: $0.**
Oracle server can reach `api-gateway.top-parser.com` directly even though `1witeo.life` blocks Oracle's IP.

---

## The Approach — Direct WebSocket to top-parser.com

The browser connects via Socket.IO to `api-gateway.top-parser.com/push-server-v2/` and subscribes
to a match. The server pushes all `oddsGroups` in one message within ~2s.

```python
import websocket, json, time, requests

PARTNER = "44ba10e5-7df2-47ab-a44d-dc93803c7a6e"
WS_URL  = (
    f"wss://api-gateway.top-parser.com:443/push-server-v2/"
    f"?Language=es-MX&externalPartnerId={PARTNER}&EIO=4&transport=websocket"
)

def scrape_1win_ws(match_id: int) -> list[dict]:
    """Returns list of {name, oddsList: [{name, cf, status}]}"""
    all_groups = {}
    ws = websocket.create_connection(WS_URL, timeout=15)
    try:
        ws.recv()          # "0{...}" — server hello
        ws.send("40")      # client connect
        ws.recv()          # "40{...}" — connection confirmed
        sub = json.dumps(["subscribe", {
            "messageType": "subscribe-match-odds",
            "data": {"matchIds": [match_id], "isBaseOddsGroups": False}
        }])
        ws.send("42" + sub)
        ws.settimeout(3)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                msg = ws.recv()
                if msg.startswith("42"):
                    payload = json.loads(msg[2:])
                    if len(payload) >= 2 and payload[0] == "u":
                        data = payload[1].get("data", {})
                        if data.get("matchId") == match_id and "oddsGroups" in data:
                            for grp in data["oddsGroups"]:
                                all_groups[grp["id"]] = grp
            except Exception:
                break
    finally:
        ws.close()
    return sorted(all_groups.values(), key=lambda g: g.get("order", 0))
```

---

## Odds Format — decimal `cf` → American

Odds arrive as `cf` (decimal). Status `1` = active, anything else = suspended.

```python
def american(cf: float) -> str:
    if cf >= 2.0: return f"+{int(round((cf - 1) * 100))}"
    return f"{int(round(-100 / (cf - 1)))}"

# Examples: cf=2.16 → +116, cf=3.15 → +215, cf=1.43 → -233
```

---

## Step 1 — Find the match ID

The matchId is the last number in the 1Win match URL:
```
https://1witeo.life/betting/match/sport/club-deportivo-guadalajara-vs-leon-33470209
                                                                              ^^^^^^^^
```

Get match metadata (team names, date) via HTTP — this endpoint is public:
```python
r = requests.get(
    f"https://api-gateway.top-parser.com/matches/get"
    f"?matchId={match_id}&l=es-MX&p={PARTNER}",
    timeout=10
)
match = r.json()["result"]
event_name = match["name"]  # "Pumas UNAM - Club America"
```

Find upcoming Liga MX matches:
```
https://1witeo.life/betting/prematch/soccer-18/liga-mx-44913
```

---

## Socket.IO Protocol Details

| Step | Direction | Message | Meaning |
|------|-----------|---------|---------|
| 1 | ← Server | `0{"sid":"...","pingInterval":25000}` | Server hello |
| 2 | → Client | `40` | Connect |
| 3 | ← Server | `40{"sid":"...","pid":"..."}` | Connection confirmed |
| 4 | → Client | `42["subscribe",{"messageType":"subscribe-match-odds","data":{"matchIds":[id],"isBaseOddsGroups":false}}]` | Subscribe |
| 5 | ← Server | `42["u",{"data":{"matchId":...,"oddsGroups":[...]}}]` | Full odds snapshot |
| 6+ | ← Server | `42["u",{...}]` | Individual odds updates |

**WebSocket URL:**
```
wss://api-gateway.top-parser.com:443/push-server-v2/
  ?Language=es-MX
  &externalPartnerId=44ba10e5-7df2-47ab-a44d-dc93803c7a6e
  &EIO=4
  &transport=websocket
```

---

## Notes

- **$0, 0 Firecrawl credits.** Direct WebSocket to `api-gateway.top-parser.com` from Oracle.
- **Previous assumption was wrong**: "top-parser.com has no public odds API" — odds ARE available via WebSocket, not REST. REST endpoints (`/odds/get`, `/markets/get`) still 404 but `/push-server-v2/` WebSocket works.
- **Oracle IP not blocked** by top-parser.com — only `1witeo.life` (Cloudflare) blocks Oracle. The API server is unprotected.
- **Partner key is stable**: `44ba10e5-7df2-47ab-a44d-dc93803c7a6e` is 1Win MX's hardcoded partner ID. Treat as a constant.
- **CSS module class names are hashed** (`_root_m2ytg_2`) — irrelevant now, but use `[class*="prefix"]` if ever doing DOM extraction.
- **Decimal odds** (`cf` field): convert to American. Status `1` = active; skip others.
- **Confirmed**: 57 mercados, 689 selecciones for Pumas UNAM - Club América (2026-03-19 prematch).
- **Total cost per match scrape across all 4 books: $0.** Zero Firecrawl credits needed.
