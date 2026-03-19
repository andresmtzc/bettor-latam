"""
Bettor LATAM — Scraper Web App
Run: python app.py
Open: http://localhost:5050
Requires: FIRECRAWL_API_KEY env var (for Caliente)
Optional: SUPABASE_SERVICE_ROLE_KEY for compare job upserts
"""

import os, re, json, concurrent.futures, urllib.request
from datetime import datetime
from html.parser import HTMLParser
import requests
import websocket as ws_client
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
FC_KEY = os.environ.get('FIRECRAWL_API_KEY', '')

SUPABASE_URL      = 'https://wxvjetgeiuzjuyzfhutv.supabase.co'
SUPABASE_ANON_KEY = 'sb_publishable_mXQB6BSxNpWyL0D3HG0gwA_fwdWoqVh'


###############################################################################
# PLAYDOIT  — Altenar API, no auth, $0
###############################################################################

def scrape_playdoit(url):
    m = re.search(r'eventId=(\d+)', url)
    if not m:
        raise ValueError("No eventId in URL. Expected: #page=event&eventId=12345")
    event_id = m.group(1)

    api_url = (
        "https://sb2frontend-altenar2.biahosted.com/api/widget/GetEventDetails"
        f"?culture=es-ES&timezoneOffset=240&integration=playdoit2"
        f"&deviceType=1&numFormat=en-GB&countryCode=US"
        f"&eventId={event_id}&showNonBoosts=false"
    )
    r = requests.get(api_url, timeout=20)
    r.raise_for_status()
    d = r.json()

    odds_map    = {o['id']: o for o in d.get('odds', [])}
    markets_map = {m['id']: m for m in d.get('markets', [])}
    child_map   = {m['id']: m for m in d.get('childMarkets', [])}

    def american(dec):
        if dec >= 2.0: return f"+{int(round((dec - 1) * 100))}"
        return f"{int(round(-100 / (dec - 1)))}"

    ev = d.get('event', {})
    home = ev.get('homeTeamName', ev.get('name', 'Local'))
    away = ev.get('awayTeamName', 'Visitante')
    event_name = f"{home} vs {away}" if away != 'Visitante' else home

    lines = [f"PlayDoit — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for group in d.get('marketGroups', []):
        group_lines = [f"\n{'='*60}", f"GROUP: {group['name']}", '='*60]
        group_used = False

        for mid in group.get('marketIds', []):
            mkt = markets_map.get(mid)
            if not mkt:
                continue

            if mkt.get('childMarketIds'):
                col_names = []
                try:
                    col_names = [o['name'] for o in mkt['headers'][0]['odds']]
                except (KeyError, IndexError, TypeError):
                    pass

                player_rows = []
                for cid in mkt['childMarketIds']:
                    cm = child_map.get(cid, {})
                    player = cm.get('childName', '?')
                    prices = []
                    for col in cm.get('desktopOddIds', []):
                        oid = col[0] if col else None
                        odd = odds_map.get(oid, {})
                        p = odd.get('price', 0)
                        prices.append(american(p) if p > 1 else '-')
                    if any(p != '-' for p in prices):
                        player_rows.append((player, prices))
                        total_s += len([p for p in prices if p != '-'])

                if player_rows:
                    group_lines.append(f"\n  {mkt['name']}")
                    if col_names:
                        group_lines.append("    " + f"{'':32}" + "".join(f"{c:>12}" for c in col_names))
                    for player, prices in player_rows:
                        group_lines.append("    " + f"{player[:32]:<32}" + "".join(f"{p:>12}" for p in prices))
                    total_m += 1
                    group_used = True
                    # Flatten player-grid into selections for comparison
                    flat_sels = []
                    for player, prices in player_rows:
                        for col, price in zip(col_names, prices):
                            if price != '-':
                                flat_sels.append({'selection': f"{player} {col}", 'american': price})
                    if flat_sels:
                        markets_list.append({'name': mkt['name'], 'selections': flat_sels})
            else:
                seen, selns = set(), []
                for col in mkt.get('desktopOddIds', []):
                    for oid in col:
                        if oid in seen: continue
                        seen.add(oid)
                        odd = odds_map.get(oid)
                        if odd and odd.get('price', 0) > 1:
                            selns.append({'selection': odd['name'], 'american': american(odd['price'])})

                if selns:
                    group_lines.append(f"\n  {mkt['name']}")
                    for s in selns:
                        group_lines.append(f"    {s['selection']}: {s['american']}")
                    total_m += 1
                    total_s += len(selns)
                    group_used = True
                    markets_list.append({'name': mkt['name'], 'selections': selns})

        if group_used:
            lines.extend(group_lines)

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), markets_list, total_m, total_s


###############################################################################
# CODERE  — SSR + parallel web_nr, no auth, $0
###############################################################################

class _MktParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._btn = self._seln_name = self._draw_label = self._us_price = False
        self._btn_title = self._cur_seln = self._cur_price = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'button' and a.get('name') == 'add-to-slip':
            self._btn = True
            self._btn_title = a.get('title', '').strip()
            self._cur_seln = self._cur_price = None
        if self._btn and tag == 'span':
            cls = a.get('class', '').split()
            if 'seln-name'       in cls: self._seln_name  = True
            if 'seln-draw-label' in cls: self._draw_label = True
            if 'price' in cls and 'us' in cls and 'was-price' not in cls:
                self._us_price = True

    def handle_endtag(self, tag):
        if tag == 'button' and self._btn:
            self._btn = False
            if self._cur_price and self._cur_price != 'N/A':
                seln = (self._cur_seln or self._btn_title or 'UNKNOWN').strip()
                self.results.append({'selection': seln, 'american': self._cur_price})
        if tag == 'span':
            self._seln_name = self._draw_label = self._us_price = False

    def handle_data(self, data):
        d = data.strip()
        if   self._seln_name:                  self._cur_seln   = d
        elif self._draw_label and not self._cur_seln: self._cur_seln = d
        elif self._us_price:                   self._cur_price  = d


def scrape_codere(url):
    m = re.search(r'/e/(\d+)/([^?&#]+)', url)
    if not m:
        raise ValueError("Could not extract event ID from Codere URL")
    ev_id, ev_slug = m.group(1), m.group(2)

    BASE = "https://apuestas.codere.mx"
    H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    # Odds are pre-rendered in SSR HTML — one plain HTTP GET, no web_nr calls needed
    html = requests.get(f"{BASE}/es_MX/e/{ev_id}/{ev_slug}?show_all=Y", headers=H, timeout=20).text

    t = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    event_name = t.group(1).strip() if t else f"Codere {ev_id}"

    mkt_re = re.compile(r'<div class="[^"]*\bmkt\b[^"]*"\s+data-mkt_id="([^"]+)"\s*(?:data-fetch_url="[^"]*")?>')
    btn_re = re.compile(r'<button[^>]*>(.*?)</button>', re.DOTALL)
    positions = [(m2.group(1).split(',')[0].strip(), m2.start()) for m2 in mkt_re.finditer(html)]

    lines = [f"Codere — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for i, (mkt_id, start) in enumerate(positions):
        end = positions[i+1][1] if i + 1 < len(positions) else len(html)
        content = html[start:end]

        nm = re.search(r'class="mkt-name">([^<]+)', content)
        name = nm.group(1).strip() if nm else f'Market_{mkt_id}'

        selns = []
        for btn in btn_re.finditer(content):
            b = btn.group(1)
            sn = re.search(r'class="seln-name">([^<]+)', b)
            sd = re.search(r'class="seln-draw-label">([^<]+)', b)
            sh = re.search(r'class="seln-hcap">([^<]+)', b)
            pu = re.search(r'class="price us"[^>]*>([^<]+)', b)
            if pu:
                n = sn or sd
                if n:
                    fn = n.group(1).strip()
                    if sh and sn:
                        fn += f" ({sh.group(1).strip()})"
                    selns.append({'selection': fn, 'american': pu.group(1).strip()})

        if selns:
            lines.append(f"\n=== {name} ===")
            for s in selns:
                lines.append(f"  {s['selection']}: {s['american']}")
            total_m += 1
            total_s += len(selns)
            markets_list.append({'name': name, 'selections': selns})

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), markets_list, total_m, total_s


###############################################################################
# CALIENTE  — Firecrawl, no actions. Odds are SSR pre-rendered, no clicks needed.
###############################################################################

def scrape_caliente(url):
    if not FC_KEY:
        raise ValueError("FIRECRAWL_API_KEY not set")
    page_url = re.sub(r'\?.*', '', url) + '?show_all=Y'
    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {FC_KEY}", "Content-Type": "application/json"},
        json={"url": page_url, "formats": ["rawHtml"], "actions": [{"type": "wait", "milliseconds": 3000}]},
        timeout=30
    )
    resp.raise_for_status()
    html = resp.json()['data']['rawHtml']

    t = re.search(r'<title>([^<]+)</title>', html)
    event_name = t.group(1).strip() if t else "Caliente Event"

    mkt_re = re.compile(r'<div class="[^"]*\bmkt\b[^"]*"\s+data-mkt_id="(\d+)"\s*(?:data-fetch_url="[^"]*")?>')
    btn_re = re.compile(r'<button[^>]*>(.*?)</button>', re.DOTALL)
    positions = [(m.group(1), m.start()) for m in mkt_re.finditer(html)]

    lines = [f"Caliente — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for i, (mkt_id, start) in enumerate(positions):
        end = positions[i+1][1] if i + 1 < len(positions) else len(html)
        content = html[start:end]

        nm = re.search(r'class="mkt-name">([^<]+)', content)
        name = nm.group(1).strip() if nm else f'Market_{mkt_id}'

        selns = []
        for btn in btn_re.finditer(content):
            b = btn.group(1)
            sn = re.search(r'class="seln-name">([^<]+)', b)
            sd = re.search(r'class="seln-draw-label">([^<]+)', b)
            sh = re.search(r'class="seln-hcap">([^<]+)', b)
            pu = re.search(r'class="price us"[^>]*>([^<]+)', b)
            if pu:
                n = sn or sd
                if n:
                    fn = n.group(1).strip()
                    if sh and sn:
                        fn += f" ({sh.group(1).strip()})"
                    selns.append({'selection': fn, 'american': pu.group(1).strip()})

        if selns:
            lines.append(f"\n=== {name} ===")
            for s in selns:
                lines.append(f"  {s['selection']}: {s['american']}")
            total_m += 1
            total_s += len(selns)
            markets_list.append({'name': name, 'selections': selns})

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), markets_list, total_m, total_s


###############################################################################
# 1WIN  — Direct WebSocket to api-gateway.top-parser.com, $0, no Firecrawl.
###############################################################################

_1WIN_PARTNER = "44ba10e5-7df2-47ab-a44d-dc93803c7a6e"
_1WIN_WS = (
    "wss://api-gateway.top-parser.com:443/push-server-v2/"
    f"?Language=es-MX&externalPartnerId={_1WIN_PARTNER}&EIO=4&transport=websocket"
)

def scrape_1win(url):
    m = re.search(r'-(\d{7,})(?:[?&/]|$)', url)
    if not m:
        raise ValueError("Could not extract match ID from 1Win URL")
    match_id = int(m.group(1))

    # Get match name via HTTP
    r = requests.get(
        f"https://api-gateway.top-parser.com/matches/get"
        f"?matchId={match_id}&l=es-MX&p={_1WIN_PARTNER}",
        timeout=10
    )
    event_name = r.json().get("result", {}).get("name", f"1Win {match_id}")

    # WebSocket: Socket.IO v4 protocol
    all_groups = {}
    ws = ws_client.create_connection(_1WIN_WS, timeout=15)
    try:
        ws.recv()                    # "0{...}" server hello
        ws.send("40")                # client connect
        ws.recv()                    # "40{...}" connection confirmed
        sub = json.dumps(["subscribe", {
            "messageType": "subscribe-match-odds",
            "data": {"matchIds": [match_id], "isBaseOddsGroups": False}
        }])
        ws.send("42" + sub)
        ws.settimeout(3)
        deadline = _time.time() + 10
        while _time.time() < deadline:
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

    def american(cf):
        if cf >= 2.0: return f"+{int(round((cf - 1) * 100))}"
        return f"{int(round(-100 / (cf - 1)))}"

    lines = [f"1Win — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for grp in sorted(all_groups.values(), key=lambda g: g.get("order", 0)):
        name = grp.get("name", "UNKNOWN")
        active = [o for o in grp.get("oddsList", []) if o.get("status") == 1 and o.get("cf", 0) > 1]
        if active:
            lines.append(f"\n=== {name} ===")
            for o in active:
                lines.append(f"  {o['name']}: {american(o['cf'])}")
            total_m += 1
            total_s += len(active)
            sels = [{'selection': o['name'], 'american': american(o['cf'])} for o in active]
            markets_list.append({'name': name, 'selections': sels})

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), markets_list, total_m, total_s


###############################################################################
# CACHE  — in-memory, 10-minute TTL per URL
###############################################################################

import time as _time
_cache: dict = {}  # url -> (timestamp, result)
CACHE_TTL = 600    # seconds

def cache_get(url):
    entry = _cache.get(url)
    if entry and (_time.time() - entry[0]) < CACHE_TTL:
        return entry[1]
    return None

def cache_set(url, result):
    _cache[url] = (_time.time(), result)


###############################################################################
# FLASK APP
###############################################################################

SCRAPERS = {
    'caliente': scrape_caliente,
    'codere':   scrape_codere,
    '1win':     scrape_1win,
    'playdoit': scrape_playdoit,
}


###############################################################################
# MARKET NORMALIZATION + COMPARISON ENGINE
###############################################################################

def normalize_market(name):
    """Map a book-specific market name to a canonical key. Returns None if unknown."""
    n = name.lower().strip()

    # Half-time check first (more specific)
    is_ht = any(k in n for k in ['primera mitad', 'half time', 'halftime', 'descanso', 'medio tiempo', '1t ', '1ht', 'half-time'])

    # 1X2 / match result
    if any(k in n for k in ['resultado', '1x2', 'ganador del partido', 'match result', 'winner', 'moneyline']):
        return 'ht_result' if is_ht else 'result'

    # BTTS
    if any(k in n for k in ['ambos', 'btts', 'both teams', 'anotan', 'marcan']):
        return 'btts'

    # Double chance
    if any(k in n for k in ['doble oportunidad', 'double chance']):
        return 'double_chance'

    # Draw no bet
    if any(k in n for k in ['empate no apuesta', 'draw no bet', 'dnb']):
        return 'dnb'

    # Over/under totals with specific value
    for val in ['0.5', '1.5', '2.5', '3.5', '4.5', '5.5', '6.5']:
        if val in n and any(k in n for k in ['goles', 'total', 'más', 'menos', 'over', 'under', 'goals']):
            prefix = 'ht_ou' if is_ht else 'ou'
            return f'{prefix}_{val}'

    # Half-time result (standalone, e.g. "Resultado Primera Mitad")
    if is_ht:
        return 'ht_result'

    # Asian handicap
    if any(k in n for k in ['hándicap asiático', 'handicap asiatico', 'asian handicap']):
        return None  # too varied to normalize reliably for MVP

    return None


def implied_prob(american):
    """Convert American odds string to implied probability (0.0–1.0). Returns None on error."""
    try:
        s = str(american).strip()
        v = int(s.replace('+', ''))
        if s.startswith('+') or v > 0:
            return 100 / (v + 100)
        else:
            return abs(v) / (abs(v) + 100)
    except Exception:
        return None


def compute_comparison(book_markets):
    """
    book_markets: {'caliente': [{name, selections}], 'codere': [...], ...}
    Returns sorted list of tier-classified market rows.
    Tier 1: 4 books + gap > 2%  (premium)
    Tier 2: 2-3 books + gap > 2%
    Tier 3: 2+ books, gap <= 2% (flat)
    Tier 4: 1 book — excluded from output
    """
    # Build canonical -> {display_name, books: {book: [sels]}}
    canonical = {}
    for book, markets in book_markets.items():
        for mkt in markets:
            key = normalize_market(mkt['name'])
            if key is None:
                continue
            if key not in canonical:
                canonical[key] = {'display_name': mkt['name'], 'books': {}}
            # Don't overwrite with a less-named version
            canonical[key]['books'][book] = mkt['selections']

    rows = []
    for key, data in canonical.items():
        books_data = data['books']
        book_count = len(books_data)
        if book_count < 2:
            continue  # tier 4 — skip

        # Collect all unique selection names (preserve first-seen order)
        sel_names = []
        seen_sels = set()
        for sels in books_data.values():
            for s in sels:
                if s['selection'] not in seen_sels:
                    seen_sels.add(s['selection'])
                    sel_names.append(s['selection'])

        # Build per-selection odds + gap
        sel_rows = []
        for sel_name in sel_names:
            odds = {}
            for book, sels in books_data.items():
                for s in sels:
                    if s['selection'] == sel_name:
                        odds[book] = s['american']
                        break
            if len(odds) < 2:
                continue
            probs = [implied_prob(o) for o in odds.values()]
            probs = [p for p in probs if p is not None]
            gap = round(max(probs) - min(probs), 4) if len(probs) >= 2 else 0.0
            best_book = min(
                (b for b in odds if implied_prob(odds[b]) is not None),
                key=lambda b: implied_prob(odds[b]) or 1.0,
                default=None
            )
            sel_rows.append({'name': sel_name, 'odds': odds, 'best_book': best_book, 'gap': gap})

        if not sel_rows:
            continue

        overall_gap = max(s['gap'] for s in sel_rows)

        if overall_gap > 0.02:
            tier = 1 if book_count == 4 else 2
        else:
            tier = 3

        rows.append({
            'canonical': key,
            'display_name': data['display_name'],
            'tier': tier,
            'books': book_count,
            'max_gap': round(overall_gap, 4),
            'selections': sel_rows,
        })

    # Sort: tier asc, then gap desc within tier
    rows.sort(key=lambda r: (r['tier'], -r['max_gap']))
    return rows


###############################################################################
# SUPABASE UPSERT
###############################################################################

def supabase_upsert(match_name, league, match_date, markets_data):
    """Upsert comparison rows into match_comparisons table via Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/match_comparisons"
    headers = {
        'apikey': SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates',
    }
    payload = {
        'match_name': match_name,
        'league': league,
        'match_date': match_date,
        'markets': markets_data,
        'scraped_at': datetime.utcnow().isoformat() + 'Z',
    }
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code not in (200, 201):
        print(f"⚠️  Supabase upsert failed for {match_name}: {r.status_code} {r.text[:200]}")
    else:
        tier_counts = {}
        for m in markets_data:
            tier_counts[m['tier']] = tier_counts.get(m['tier'], 0) + 1
        print(f"✅ Upserted {match_name}: {tier_counts}")




###############################################################################
# SCRAPER DEBUG UI  (existing)
###############################################################################

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bettor LATAM — Scraper</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0c0c0c; color: #d4d4d4;
  min-height: 100vh; padding: 48px 20px;
}
h1 { text-align: center; font-size: 1.3rem; font-weight: 700; color: #fff; letter-spacing: -0.02em; }
.sub { text-align: center; color: #555; font-size: 0.82rem; margin-top: 6px; margin-bottom: 44px; }

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; max-width: 780px; margin: 0 auto 28px; }
.card {
  background: #141414; border: 1px solid #222; border-radius: 12px;
  padding: 18px 18px 14px; transition: border-color 0.2s;
}
.card:focus-within { border-color: #333; }
.book-header { display: flex; align-items: center; gap: 9px; margin-bottom: 11px; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.book-name  { font-size: 0.9rem; font-weight: 600; color: #fff; }
.book-hint  { font-size: 0.72rem; color: #444; margin-top: 1px; }
input {
  width: 100%; background: #0c0c0c; border: 1px solid #2a2a2a;
  border-radius: 7px; padding: 9px 11px; color: #ccc;
  font-size: 0.8rem; outline: none; transition: border-color 0.15s;
}
input:focus { border-color: #444; }
input::placeholder { color: #333; }
.card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 9px; }
.status { font-size: 0.75rem; color: #3a3a3a; }
.status.loading { color: #f59e0b; }
.status.ok      { color: #22c55e; }
.status.error   { color: #ef4444; }
.scrape-one {
  background: none; border: 1px solid #2a2a2a; color: #555;
  border-radius: 6px; padding: 4px 10px; font-size: 0.72rem;
  cursor: pointer; transition: border-color 0.15s, color 0.15s; white-space: nowrap;
}
.scrape-one:hover:not(:disabled) { border-color: #444; color: #aaa; }
.scrape-one:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-wrap { text-align: center; max-width: 780px; margin: 0 auto 36px; }
.btn {
  background: #2563eb; color: #fff; border: none; border-radius: 9px;
  padding: 13px 52px; font-size: 0.95rem; font-weight: 600;
  cursor: pointer; transition: background 0.15s;
}
.btn:hover:not(:disabled) { background: #1d4ed8; }
.btn:disabled { background: #1a1a2e; color: #334155; cursor: not-allowed; }

.results { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; max-width: 780px; margin: 0 auto; }
.res-card {
  background: #141414; border: 1px solid #1a3a1a;
  border-radius: 12px; padding: 18px; display: none;
}
.res-card.show { display: block; }
.res-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.res-name { font-weight: 600; color: #fff; font-size: 0.9rem; }
.dl-btn {
  background: #166534; color: #bbf7d0; border: none; border-radius: 6px;
  padding: 6px 14px; font-size: 0.76rem; cursor: pointer; transition: background 0.15s;
}
.dl-btn:hover { background: #15803d; }
.res-stats { font-size: 0.75rem; color: #555; margin-bottom: 10px; }
.preview {
  background: #0c0c0c; border-radius: 7px; padding: 10px 12px;
  font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.7rem;
  color: #666; max-height: 130px; overflow-y: auto; white-space: pre;
}

/* book colors */
.dot-caliente { background: #ef4444; }
.dot-codere   { background: #22c55e; }
.dot-1win     { background: #f59e0b; }
.dot-playdoit { background: #3b82f6; }

@media (max-width: 580px) { .grid, .results { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<h1>Bettor LATAM — Scraper</h1>
<p class="sub">Pega los 4 links del mismo partido · se scrapeán en paralelo</p>

<div class="grid">
  <div class="card">
    <div class="book-header">
      <div class="dot dot-caliente"></div>
      <div><div class="book-name">Caliente</div><div class="book-hint">sports.caliente.mx/es_MX/Liga-MX/fecha/equipo-vs-equipo</div></div>
    </div>
    <input type="url" id="url-caliente" placeholder="https://sports.caliente.mx/es_MX/...">
    <div class="card-footer"><div class="status" id="st-caliente"></div><button class="scrape-one" onclick="scrapeOne('caliente')">Scrape</button></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-codere"></div>
      <div><div class="book-name">Codere</div><div class="book-hint">apuestas.codere.mx/es_MX/e/{id}/{slug}</div></div>
    </div>
    <input type="url" id="url-codere" placeholder="https://apuestas.codere.mx/es_MX/e/12345/...">
    <div class="card-footer"><div class="status" id="st-codere"></div><button class="scrape-one" onclick="scrapeOne('codere')">Scrape</button></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-1win"></div>
      <div><div class="book-name">1Win</div><div class="book-hint">1witeo.life/betting/match/sport/...-{matchId}</div></div>
    </div>
    <input type="url" id="url-1win" placeholder="https://1witeo.life/betting/match/sport/...-33470209">
    <div class="card-footer"><div class="status" id="st-1win"></div><button class="scrape-one" onclick="scrapeOne('1win')">Scrape</button></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-playdoit"></div>
      <div><div class="book-name">PlayDoit</div><div class="book-hint">#page=event&amp;eventId={id}</div></div>
    </div>
    <input type="url" id="url-playdoit" placeholder="https://www.playdoit.mx/#page=event&eventId=12345">
    <div class="card-footer"><div class="status" id="st-playdoit"></div><button class="scrape-one" onclick="scrapeOne('playdoit')">Scrape</button></div>
  </div>
</div>

<div class="btn-wrap">
  <button class="btn" id="scrape-btn" onclick="scrapeAll()">Scrape All Books</button>
</div>

<div class="results" id="results"></div>

<script>
const BOOKS = ['caliente','codere','1win','playdoit'];
const NAMES = {caliente:'Caliente', codere:'Codere', '1win':'1Win', playdoit:'PlayDoit'};
const store  = {};

function setStatus(book, cls, msg) {
  const el = document.getElementById(`st-${book}`);
  el.className = `status ${cls}`;
  el.textContent = msg;
}

async function scrapeOne(book) {
  const url = document.getElementById(`url-${book}`).value.trim();
  if (!url) { alert(`Pega el link de ${NAMES[book]} primero.`); return; }
  setStatus(book, 'loading', '⏳ Scraping…');
  try {
    const resp = await fetch('/scrape', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({[book]: url})
    });
    const data = await resp.json();
    const r = data[book];
    if (r?.status === 'ok') {
      setStatus(book, 'ok', `✅ ${r.stats}${r.cached ? ' (cached)' : ''}`);
      store[book] = r.txt;
      document.querySelectorAll('.res-card').forEach(c => { if (c.dataset.book === book) c.remove(); });
      addResult(book, r);
    } else {
      setStatus(book, 'error', `❌ ${r?.error}`);
    }
  } catch(e) { setStatus(book, 'error', `❌ ${e.message}`); }
}

async function scrapeAll() {
  const urls = {};
  let any = false;
  BOOKS.forEach(b => {
    const v = document.getElementById(`url-${b}`).value.trim();
    if (v) { urls[b] = v; any = true; setStatus(b, 'loading', '⏳ Scraping…'); }
    else   { setStatus(b, '', ''); }
  });
  if (!any) { alert('Pega al menos un link.'); return; }

  const btn = document.getElementById('scrape-btn');
  btn.disabled = true; btn.textContent = 'Scraping…';
  document.getElementById('results').innerHTML = '';

  try {
    const resp = await fetch('/scrape', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(urls)
    });
    const data = await resp.json();

    BOOKS.forEach(b => {
      if (!urls[b]) return;
      const r = data[b];
      if (!r) return;
      if (r.status === 'ok') {
        setStatus(b, 'ok', `✅ ${r.stats}`);
        store[b] = r.txt;
        addResult(b, r);
      } else {
        setStatus(b, 'error', `❌ ${r.error}`);
      }
    });
  } catch(e) {
    BOOKS.forEach(b => { if (urls[b]) setStatus(b, 'error', `❌ ${e.message}`); });
  } finally {
    btn.disabled = false; btn.textContent = 'Scrape All Books';
  }
}

function addResult(book, r) {
  const preview = r.txt.split('\\n').slice(0, 22).join('\\n');
  const card = document.createElement('div');
  card.className = 'res-card show';
  card.dataset.book = book;
  card.innerHTML = `
    <div class="res-top">
      <div class="res-name">${NAMES[book]}</div>
      <button class="dl-btn" onclick="download('${book}')">↓ Download .txt</button>
    </div>
    <div class="res-stats">${r.stats}</div>
    <div class="preview">${preview}\n…</div>`;
  document.getElementById('results').appendChild(card);
}

function download(book) {
  const txt = store[book]; if (!txt) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([txt], {type:'text/plain'}));
  a.download = `${book}-${new Date().toISOString().slice(0,10)}.txt`;
  a.click();
}
</script>
</body>
</html>"""


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json or {}

    def run(book, url):
        cached = cache_get(url)
        if cached:
            return book, {**cached, 'cached': True}
        try:
            txt, markets_list, total_m, total_s = SCRAPERS[book](url)
            result = {
                'status': 'ok',
                'txt': txt,
                'stats': f"{total_m} mercados · {total_s} selecciones",
                'markets_list': markets_list,
            }
            cache_set(url, result)
            return book, result
        except Exception as e:
            return book, {'status': 'error', 'error': str(e)}

    results = {}
    jobs = {book: url for book, url in data.items() if book in SCRAPERS and url}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run, book, url): book for book, url in jobs.items()}
        for future in concurrent.futures.as_completed(futures, timeout=120):
            book, result = future.result()
            results[book] = result

    # Auto-upsert to Supabase when 2+ books scraped successfully
    book_markets = {
        b: r['markets_list'] for b, r in results.items()
        if r.get('status') == 'ok' and r.get('markets_list')
    }
    if len(book_markets) >= 2:
        # Derive match name from the first successful txt (format: "BookName — Event Name")
        match_name = None
        for b, r in results.items():
            if r.get('status') == 'ok' and r.get('txt'):
                first_line = r['txt'].split('\n')[0]
                if ' — ' in first_line:
                    match_name = first_line.split(' — ', 1)[1].strip()
                    break
        if match_name:
            markets_data = compute_comparison(book_markets)
            supabase_upsert(match_name, 'Liga MX', datetime.now().strftime('%Y-%m-%d'), markets_data)

    return jsonify(results)


if __name__ == '__main__':
    print("🚀  Bettor LATAM Scraper → http://localhost:5050")
    app.run(debug=False, port=5050)
