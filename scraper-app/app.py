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

    page = requests.get(f"{BASE}/es_MX/e/{ev_id}/{ev_slug}?show_all=Y", headers=H, timeout=20).text

    all_ids, id_to_name = set(), {}
    for raw in re.findall(r'data-mkt_id="([^"]+)"', page):
        for mid in raw.split(','):
            all_ids.add(mid.strip())
    for m2 in re.finditer(r'data-mkt_id="([^"]+)"[^>]*>.*?class="mkt-name">([^<]+)', page, re.DOTALL):
        for mid in m2.group(1).split(','):
            id_to_name[mid.strip()] = m2.group(2).strip()

    t = re.search(r'<h1[^>]*>([^<]+)</h1>', page)
    event_name = t.group(1).strip() if t else f"Codere {ev_id}"

    def fetch(mkt_id):
        try:
            with urllib.request.urlopen(
                f"{BASE}/web_nr?key=sportsbook.cms.handlers.get_mkt_content&mkt_id={mkt_id}", timeout=10
            ) as r:
                return mkt_id, json.load(r).get('html', '')
        except:
            return mkt_id, ''

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        htmls = dict(ex.map(fetch, all_ids))

    lines = [f"Codere — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for mkt_id in sorted(all_ids, key=lambda x: id_to_name.get(x, x)):
        html = htmls.get(mkt_id, '')
        if not html:
            continue
        mkt_name = id_to_name.get(mkt_id, f'Market_{mkt_id}')
        p = _MktParser()
        p.feed(html)
        if p.results:
            lines.append(f"\n=== {mkt_name} ===")
            for r in p.results:
                lines.append(f"  {r['selection']}: {r['american']}")
            total_m += 1
            total_s += len(p.results)
            markets_list.append({'name': mkt_name, 'selections': p.results})

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), markets_list, total_m, total_s


###############################################################################
# CALIENTE  — Firecrawl, 1 credit. JS clicks all market expanders so Firecrawl's
# browser (not Oracle) fires the AJAX calls to web_nr — bypassing Cloudflare.
# Works for future matches (lazy-loaded) and same-day matches (SSR pre-rendered).
###############################################################################

_CAL_JS = (
    # Scroll to bottom to trigger scroll-based lazy rendering
    "window.scrollTo(0,document.body.scrollHeight);"
)

_CAL_JS2 = (
    # Expand collapsed markets only (skip already-expanded to avoid toggling them shut).
    # Tries multiple header selectors used by Geneity/OpenBet, falls back to firstElementChild.
    "document.querySelectorAll('div[data-mkt_id]').forEach(function(m){"
    "if(m.querySelector('.price.us'))return;"  # already has prices → already expanded
    "var h=m.querySelector('h6,button,.expander-head,.mkt-name,.mkt-header,.accordion-header,[class*=\"expander\"],[class*=\"header\"]');"
    "if(!h)h=m.firstElementChild;"
    "if(h){try{h.click();}catch(e){}}"
    "});"
)

class _CalParser(HTMLParser):
    """
    DOM-depth-aware parser for Caliente full-page HTML.

    Tracks <div> nesting depth so each div[data-mkt_id] owns only the
    selections that are literally inside it — preventing the positional
    text-slicing bleed where one market's odds leak into the next.

    Nested div[data-mkt_id] elements (sub-markets) are saved independently;
    their selections are NOT also accumulated into the parent market.
    """

    def __init__(self):
        super().__init__()
        self.markets = []           # [{id, name, selections}]
        self._depth = 0             # running <div> nesting depth
        self._mkt_stack = []        # stack of open market dicts
        self._in_mkt_name = False
        self._in_btn = False
        self._in_seln_name = False
        self._in_draw_label = False
        self._in_hcap = False
        self._in_price_us = False
        self._cur_seln = self._cur_hcap = self._cur_price = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'div':
            self._depth += 1
            mkt_id = a.get('data-mkt_id')
            if mkt_id:
                self._mkt_stack.append({
                    'id': mkt_id,
                    'depth': self._depth,
                    'name': None,
                    'selections': [],
                })
        if not self._mkt_stack:
            return
        cls = a.get('class', '').split()
        if tag == 'span' and 'mkt-name' in cls:
            self._in_mkt_name = True
        if tag == 'button':
            self._in_btn = True
            self._cur_seln = self._cur_hcap = self._cur_price = None
        if self._in_btn and tag == 'span':
            if 'seln-name'       in cls: self._in_seln_name  = True
            if 'seln-draw-label' in cls: self._in_draw_label = True
            if 'seln-hcap'       in cls: self._in_hcap       = True
            if 'price' in cls and 'us' in cls and 'was-price' not in cls:
                self._in_price_us = True

    def handle_endtag(self, tag):
        if tag == 'div':
            # Pop this market off the stack when its own closing </div> arrives.
            if self._mkt_stack and self._depth == self._mkt_stack[-1]['depth']:
                mkt = self._mkt_stack.pop()
                if mkt['selections']:
                    if not mkt['name']:
                        mkt['name'] = f"Market_{mkt['id']}"
                    self.markets.append(mkt)
            self._depth -= 1
        if tag == 'button' and self._in_btn:
            self._in_btn = False
            if self._cur_price and self._mkt_stack:
                seln = (self._cur_seln or 'UNKNOWN').strip()
                if self._cur_hcap:
                    seln += f" ({self._cur_hcap.strip()})"
                # Add to the innermost open market only
                self._mkt_stack[-1]['selections'].append({
                    'selection': seln,
                    'american': self._cur_price.strip(),
                })
            self._cur_seln = self._cur_hcap = self._cur_price = None
        if tag == 'span':
            self._in_mkt_name = self._in_seln_name = False
            self._in_draw_label = self._in_hcap = self._in_price_us = False

    def handle_data(self, data):
        d = data.strip()
        if not d:
            return
        if self._in_mkt_name and self._mkt_stack and self._mkt_stack[-1]['name'] is None:
            self._mkt_stack[-1]['name'] = d
        elif self._in_seln_name:
            self._cur_seln = d
        elif self._in_draw_label and not self._cur_seln:
            self._cur_seln = d
        elif self._in_hcap:
            self._cur_hcap = d
        elif self._in_price_us:
            self._cur_price = d


def scrape_caliente(url):
    if not FC_KEY:
        raise ValueError("FIRECRAWL_API_KEY not set")
    page_url = re.sub(r'\?.*', '', url) + '?show_all=Y'
    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {FC_KEY}", "Content-Type": "application/json"},
        json={
            "url": page_url,
            "formats": ["rawHtml"],
            "actions": [
                {"type": "wait", "milliseconds": 3000},
                {"type": "executeJavascript", "script": _CAL_JS},
                {"type": "wait", "milliseconds": 3000},
                {"type": "executeJavascript", "script": _CAL_JS2},
                {"type": "wait", "milliseconds": 10000},
            ],
            "timeout": 60000,
        },
        timeout=90
    )
    resp.raise_for_status()
    html = resp.json()['data']['rawHtml']

    t = re.search(r'<title>([^<]+)</title>', html)
    event_name = t.group(1).strip() if t else "Caliente Event"

    parser = _CalParser()
    parser.feed(html)

    lines = [f"Caliente — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    markets_list = []
    total_m = total_s = 0

    for mkt in parser.markets:
        name = mkt['name'] or f"Market_{mkt['id']}"
        selns = mkt['selections']
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
# MARKET MAP — exact lookup: (book, market_name_with_placeholders) -> canonical_key
# Team names are replaced with HOME_TEAM / AWAY_TEAM before lookup.
# 509 entries covering all 4 books (100% coverage on Monterrey vs Chivas scrape).
###############################################################################

MARKET_MAP = {
    ('1win', '1.ª mitad. Doble oportunidad'): 'ht_double_chance',
    ('1win', '1.ª mitad. Resultado'): 'ht_result',
    ('1win', '1.ª mitad. Total'): 'ht_ou_total',
    ('1win', '1.º tiempo Par/Impar'): 'ht_odd_even',
    ('1win', '1st goal time'): 'first_goal_time',
    ('1win', '1st half. AWAY_TEAM to score a goal'): 'ht_away_scores',
    ('1win', '1st half. AWAY_TEAM total'): 'ht_away_ou',
    ('1win', '1st half. HOME_TEAM to score a goal'): 'ht_home_scores',
    ('1win', '1st half. HOME_TEAM total'): 'ht_home_ou',
    ('1win', '2.ª mitad. Resultado'): '2h_result',
    ('1win', '2.ª mitad. Total'): '2h_ou_total',
    ('1win', '2.º tiempo Par/Impar'): '2h_odd_even',
    ('1win', '2nd half. AWAY_TEAM to score a goal'): '2h_away_scores',
    ('1win', '2nd half. HOME_TEAM to score a goal'): '2h_home_scores',
    ('1win', 'AWAY_TEAM exact number of goals scored'): 'away_exact_goals',
    ('1win', 'AWAY_TEAM total'): 'away_ou',
    ('1win', 'AWAY_TEAM total goals. Even/Odd'): 'away_odd_even',
    ('1win', 'Ambos equipos marcan'): 'btts',
    ('1win', 'Doble oportunidad'): 'double_chance',
    ('1win', 'Equipo local - Primer goleador'): 'home_first_scorer',
    ('1win', 'Equipo local - Último goleador'): 'home_last_scorer',
    ('1win', 'Equipo visitante - Primer goleador'): 'away_first_scorer',
    ('1win', 'Equipo visitante - Último goleador'): 'away_last_scorer',
    ('1win', 'Equipo que lanzará el último saque de esquina'): 'last_corner',
    ('1win', 'Ganar una de las mitades'): 'win_either_half',
    ('1win', 'HOME_TEAM exact number of goals scored'): 'home_exact_goals',
    ('1win', 'HOME_TEAM total'): 'home_ou',
    ('1win', 'HOME_TEAM total goals. Even/Odd'): 'home_odd_even',
    ('1win', 'Hándicap'): 'asian_hcap',
    ('1win', 'Impar/Par'): 'odd_even',
    ('1win', 'Jugador que anota 2 o más goles'): 'player_2plus',
    ('1win', 'Jugador que anota 3 o más goles'): 'player_3plus',
    ('1win', 'Jugador que anotará (tiempo reglamentario)'): 'anytime_scorer',
    ('1win', 'Llegará primero a N saques de esquina'): 'first_n_corners',
    ('1win', 'Marcador exacto'): 'exact_score',
    ('1win', 'Medio tiempo/Tiempo completo'): 'htft',
    ('1win', 'Número exacto de goles'): 'exact_goals',
    ('1win', 'Número exacto de goles. 1 Mitad'): 'ht_exact_score',
    ('1win', 'Número exacto de goles. 2 Mitad'): '2h_exact_score',
    ('1win', 'Own Goal'): 'own_goal',
    ('1win', 'Primer equipo en marcar'): 'first_team_scorer',
    ('1win', 'Primer jugador en anotar'): 'first_scorer',
    ('1win', 'Primer saque de esquina del partido - 12'): 'first_corner',
    ('1win', 'Resultado en 10 minutos'): 'result_10min',
    ('1win', 'Resultado final'): 'result',
    ('1win', 'Resultado y ambos equipos marcan'): 'result_btts',
    ('1win', 'Resultado y total'): 'result_ou_2.5',
    ('1win', 'Tiempo con más goles'): 'most_goals_half',
    ('1win', 'Total'): 'ou_total',
    ('1win', 'Total y ambos equipos marcan'): 'btts_ou_2.5',
    ('1win', 'Victoria a cero'): 'clean_sheet',
    ('1win', 'Victoria de remontada'): 'comeback_win',
    ('1win', 'Victoria en ambas mitades'): 'win_both_halves',
    ('1win', 'anotará en ambas mitades'): 'score_both_halves',
    ('1win', 'Último equipo en marcar'): 'last_team_scorer',
    ('1win', 'Último jugador en anotar'): 'last_scorer',
    ('caliente', '1er Equipo en anotar'): 'first_team_scorer',
    ('caliente', '1er Equipo en anotar en el 1er Tiempo'): 'ft_first_scorer',
    ('caliente', '1er Equipo en anotar en la 2da. Mitad'): '2h_first_scorer',
    ('caliente', '1er Mitad Apuesta Sin Equipo Local'): 'ht_dnb_home',
    ('caliente', '1er Mitad Apuesta sin Equipo Visitante'): 'ht_dnb_away',
    ('caliente', '1er Mitad Doble Oportunidad y Ambos Equipos anotan en 1ra Mitad'): 'ht_dc_ht_btts',
    ('caliente', '1ra Mitad Resultado'): 'ht_result',
    ('caliente', '1ra Mitad Resultado y 1ra Mitad Total de Goles Over/Under (1.5)'): 'ht_result_ou_1.5',
    ('caliente', '1ra Mitad Resultado y Ambos Equipos anotan en 1ra Mitad'): 'ht_result_btts',
    ('caliente', '1ra Mitad Total de Goles'): 'ht_exact_goals',
    ('caliente', '1ra Mitad Total de Goles Over/Under'): 'ht_ou_total',
    ('caliente', '1ra Mitad Over/Under AWAY_TEAM Total de Goles'): 'ht_away_ou',
    ('caliente', '1ra Mitad Over/Under HOME_TEAM Total de Goles'): 'ht_home_ou',
    ('caliente', '1ra Mitad/Tiempo Completo'): 'htft',
    ('caliente', '2da Mitad - Over/Under'): '2h_ou_total',
    ('caliente', '2da Mitad - Over/Under AWAY_TEAM Total de Goles'): '2h_away_ou',
    ('caliente', '2da Mitad - Over/Under HOME_TEAM Total de Goles'): '2h_home_ou',
    ('caliente', '2da Mitad Apuesta sin Equipo Local'): '2h_dnb_home',
    ('caliente', '2da Mitad Apuesta sin Equipo Visitante'): '2h_dnb_away',
    ('caliente', '2da Mitad Marcador Correcto'): '2h_exact_score',
    ('caliente', '2da Mitad Total Goles'): '2h_total_goals',
    ('caliente', '2da Mitad - Over/Under'): '2h_ou_total',
    ('caliente', 'AWAY_TEAM Empata después de ir perdiendo'): 'away_comeback_draw',
    ('caliente', 'AWAY_TEAM Gana a cero'): 'away_win_to_nil',
    ('caliente', 'AWAY_TEAM Portería a 0'): 'away_clean_sheet',
    ('caliente', 'AWAY_TEAM Próximo Anotador (Gol 1)'): 'away_first_scorer2',
    ('caliente', 'AWAY_TEAM Total de Goles'): 'away_total',
    ('caliente', 'AWAY_TEAM Total de Goles Impar/Par'): 'away_odd_even',
    ('caliente', 'AWAY_TEAM Tiros de Esquina'): 'away_corners',
    ('caliente', 'AWAY_TEAM Tiros de Esquina 1ra Mitad'): 'ht_away_corners_exact',
    ('caliente', 'AWAY_TEAM Tiros de Esquina 1ra Mitad (2.5)'): 'ht_away_corners',
    ('caliente', 'AWAY_TEAM Tiros de Esquina 2 opciones (5.5)'): 'away_corners_ou_5.5',
    ('caliente', 'AWAY_TEAM anota 2 o Más Goles'): 'away_2plus',
    ('caliente', 'AWAY_TEAM anota 3 o Más Goles'): 'away_3plus',
    ('caliente', 'Ambos Equipos Anotan'): 'btts',
    ('caliente', 'Ambos Equipos Anotarán en 1ra Mitad'): 'ht_btts',
    ('caliente', 'Ambos Equipos Anotarán en 2da Mitad'): '2h_btts',
    ('caliente', 'Ambos Equipos Anotarán en Ambas Mitades'): 'btts_both_halves',
    ('caliente', 'Ambos Equipos anotan en 1ra Mitad y Over/Under goles 1er Mitad (1.5)'): 'ht_btts_ou_1.5',
    ('caliente', 'Ambos anotan / Over/Under (2.5)'): 'btts_ou_2.5',
    ('caliente', 'Ambos equipos anotan en la 1er Mitad/2da Mitad'): 'btts_each_half',
    ('caliente', 'Anotadores'): 'anytime_scorer',
    ('caliente', 'Apuesta sin Equipo Visitante'): 'dnb_away',
    ('caliente', 'Apuesta sin Victoria Equipo Local'): 'dnb_home',
    ('caliente', 'Cuantos equipos llevarán ventaja en el partido?'): 'teams_lead',
    ('caliente', 'Doble Oportunidad'): 'double_chance',
    ('caliente', 'Doble Oportunidad 1ra Mitad'): 'ht_double_chance',
    ('caliente', 'Doble Oportunidad y Ambos Equipos Anotan'): 'dc_btts',
    ('caliente', 'Doble Oportunidad y Total de Goles Over/Under (1.5)'): 'dc_ou_1.5',
    ('caliente', 'Doble Oportunidad y Total de Goles Over/Under (2.5)'): 'dc_ou_2.5',
    ('caliente', 'Doble Oportunidad y Total de Goles Over/Under (3.5)'): 'dc_ou_3.5',
    ('caliente', 'Empate No Acción'): 'dnb',
    ('caliente', 'Empate no acción Segunda Mitad'): '2h_dnb',
    ('caliente', 'Equipo que Anotará en ambas mitades'): 'team_score_both',
    ('caliente', 'Gana ambas mitades'): 'win_both_halves',
    ('caliente', 'Gana cualquier mitad'): 'win_either_half',
    ('caliente', 'Gana por remontada'): 'comeback_win',
    ('caliente', 'Gana sin recibir gol'): 'win_to_nil',
    ('caliente', 'HOME_TEAM Empata después de ir perdiendo'): 'home_comeback_draw',
    ('caliente', 'HOME_TEAM Gana a cero'): 'home_win_to_nil',
    ('caliente', 'HOME_TEAM Portería a 0'): 'home_clean_sheet',
    ('caliente', 'HOME_TEAM Próximo Anotador (Gol 1)'): 'first_scorer',
    ('caliente', 'HOME_TEAM Total de Goles'): 'home_total',
    ('caliente', 'HOME_TEAM Total de Goles Impar/Par'): 'home_odd_even',
    ('caliente', 'HOME_TEAM Tiros de Esquina'): 'home_corners',
    ('caliente', 'HOME_TEAM Tiros de Esquina 1ra Mitad'): 'ht_home_corners_exact',
    ('caliente', 'HOME_TEAM Tiros de Esquina 1ra Mitad (1.5)'): 'ht_home_corners',
    ('caliente', 'HOME_TEAM Tiros de Esquina 2 opciones (4.5)'): 'home_corners_ou_4.5',
    ('caliente', 'HOME_TEAM Victoria por Remontada'): 'home_comeback',
    ('caliente', 'HOME_TEAM anota 2 o Más Goles'): 'home_2plus',
    ('caliente', 'HOME_TEAM anota 3 o Más Goles'): 'home_3plus',
    ('caliente', 'Hándicap 2da Mitad'): '2h_hcap',
    ('caliente', 'Hándicap Asiático'): 'asian_hcap',
    ('caliente', 'Hándicap Asiático Medio Tiempo (+0)'): 'ht_asian_hcap2',
    ('caliente', 'Hándicap Asiático Medio Tiempo (0 / -0.5)'): 'ht_asian_hcap',
    ('caliente', 'Hándicap Asiático Total Goles 1ra Mitad'): 'ht_asian_ou',
    ('caliente', 'Hándicap Primera Mitad'): 'ht_hcap',
    ('caliente', 'Hándicap Resultado de Partido'): 'hcap_result',
    ('caliente', 'Jugador Goles Exactos (Gol 1)'): 'player_exact1',
    ('caliente', 'Jugador Goles Exactos (Gol 2)'): 'player_exact2',
    ('caliente', 'Jugador Goles Exactos (Gol 3)'): 'player_exact3',
    ('caliente', 'Jugador anota Más goles que el equipo contrario'): 'player_outscore',
    ('caliente', 'Jugador anota gol y gana'): 'player_score_win',
    ('caliente', 'Jugador anota gol y pierde'): 'player_score_lose',
    ('caliente', 'Jugador anote gol y empate'): 'player_score_draw',
    ('caliente', 'Jugador que Anotará 2 o Más goles'): 'player_2plus',
    ('caliente', 'Jugador que Anotará en ambas mitades'): 'player_score_bh',
    ('caliente', 'Jugador que anota 1er Gol y empate'): 'player_1g_draw',
    ('caliente', 'Jugador que anota 1er gol y gana'): 'player_1g_win',
    ('caliente', 'Jugador que anota 1er gol y pierde'): 'player_1g_lose',
    ('caliente', 'Marcador Correcto'): 'exact_score',
    ('caliente', 'Marcador Correcto 1er Tiempo'): 'ht_exact_score',
    ('caliente', 'Margen de Victoria'): 'win_margin',
    ('caliente', 'Medio Tiempo Empate Sin Apuesta'): 'ht_dnb',
    ('caliente', 'Medio Tiempo/Tiempo Completo Doble Oportunidad'): 'htft_dc',
    ('caliente', 'Medio Tiempo/Tiempo Completo y Over/Under (3.5)'): 'htft_ou_3.5',
    ('caliente', 'Método del siguiente gol (Gol 1)'): 'next_goal_method',
    ('caliente', 'Mitad con Más Goles'): 'most_goals_half',
    ('caliente', 'Mitad con Más anotaciones de AWAY_TEAM'): 'away_most_half',
    ('caliente', 'Mitad con Más anotaciones de HOME_TEAM'): 'home_most_half',
    ('caliente', 'Número de equipos en anotar'): 'num_teams_scoring',
    ('caliente', 'Over de 1.5 goles en ambas mitades'): 'ou_1.5_both_halves',
    ('caliente', 'Over/Under AWAY_TEAM Total de Goles'): 'away_ou',
    ('caliente', 'Over/Under HOME_TEAM Total de Goles'): 'home_ou',
    ('caliente', 'Penalti Concedido'): 'penalty',
    ('caliente', 'Portería a 0'): 'clean_sheet',
    ('caliente', 'Primera Mitad AWAY_TEAM Total de Goles'): 'ht_away_total',
    ('caliente', 'Primera Mitad HOME_TEAM Total de Goles'): 'ht_home_total',
    ('caliente', 'Primero en cobrar 3 tiros de esquina'): 'first_3_corners',
    ('caliente', 'Primero en cobrar 5 tiros de esquina'): 'first_5_corners',
    ('caliente', 'Primero en cobrar 7 tiros de esquina'): 'first_7_corners',
    ('caliente', 'Primero en cobrar 9 tiros de esquina'): 'first_9_corners',
    ('caliente', 'Próximo Equipo en Anotar (Gol 1)'): 'next_scorer_method',
    ('caliente', 'Rango de Tiros de Esquina - 3 Opciones'): 'corners_range',
    ('caliente', 'Resultado 1er Mitad o Tiempo Completo'): 'ft_result_or_ht',
    ('caliente', 'Resultado 2da Mitad'): '2h_result',
    ('caliente', 'Resultado 2da Mitad y 2da Mitad Ambos Equipos Anotan'): '2h_result_btts',
    ('caliente', 'Resultado 2da Mitad y 2da Mitad Over/Under Goals (1.5)'): '2h_result_ou_1.5',
    ('caliente', 'Resultado Final (Tiempo Regular)'): 'result',
    ('caliente', 'Resultado Final (Tiempo Regular) - Momios mejorados'): 'result_multilines',
    ('caliente', 'Resultado Final con Over/Under (1.5)'): 'result_ou_1.5',
    ('caliente', 'Resultado Final con Over/Under (2.5)'): 'result_ou_2.5',
    ('caliente', 'Resultado Final con Over/Under (3.5)'): 'result_ou_3.5',
    ('caliente', 'Resultado del partido / Ambos equipos anotan'): 'result_btts',
    ('caliente', 'Resultado del partido por minutos'): 'result_10min',
    ('caliente', 'Se Anotará gol en 2da mitad'): '2h_goal_scored',
    ('caliente', 'Se Anotará gol en la 1ra mitad'): 'ht_goal_scored',
    ('caliente', 'Se Anotarán goles en ambas mitades'): 'score_both_halves',
    ('caliente', 'Siguiente equipo en anotar - Primer Mitad (Gol 1)'): 'ht_first_scorer',
    ('caliente', 'Sin Empate y Ambos Equipos Anotan'): 'dnb_btts',
    ('caliente', 'Tiempo Completo Doble Oportunidad y 1ra Mitad Ambos Equipos Anotan'): 'ft_dc_ht_btts',
    ('caliente', 'Tiempo en que se Anotará el próximo gol (Gol 1)'): 'first_goal_time',
    ('caliente', 'Tiempo en que se Anotará el próximo gol AWAY_TEAM (Gol 1)'): 'away_first_goal_t',
    ('caliente', 'Tiempo en que se Anotará el próximo gol HOME_TEAM (Gol 1)'): 'home_first_goal_t',
    ('caliente', 'Tiros de Esquina - 3 Opciones (10)'): 'corners_3way_10',
    ('caliente', 'Tiros de Esquina - 3 Opciones (8)'): 'corners_3way_8',
    ('caliente', 'Tiros de Esquina - 3 Opciones (9)'): 'corners_3way_9',
    ('caliente', 'Tiros de Esquina 1er Mitad Par/Impar'): 'ht_corners_odd_ev',
    ('caliente', 'Tiros de Esquina 1ra Mitad - 3 Opciones (4)'): 'ht_corners_3way_4',
    ('caliente', 'Tiros de Esquina 2da Mitad - 3 Opciones (5)'): '2h_corners_3way_5',
    ('caliente', 'Tiros de Esquina Impar/Par'): 'corners_odd_even',
    ('caliente', 'Tiros de esquina Over/Under (9.5)'): 'corners_ou_9.5',
    ('caliente', 'Tiros de esquina 1ra mitad Over/Under (4.5)'): 'ht_corners_ou_4.5',
    ('caliente', 'Tipo de Jugada'): 'play_type',
    ('caliente', 'Total Goles Asiático'): 'asian_ou',
    ('caliente', 'Total Goles Over/Under'): 'ou_total',
    ('caliente', 'Total Goles Par/Impar'): 'odd_even',
    ('caliente', 'Total Goles 1ª Mitad Par/Impar'): 'ht_odd_even',
    ('caliente', 'Total Goles 2ª Mitad Par/Impar'): '2h_odd_even',
    ('caliente', 'Total de Goles 2da Mitad - Asiático'): '2h_asian_ou',
    ('caliente', 'Total exacto de goles'): 'exact_goals',
    ('caliente', 'Último equipo en anotar'): 'last_team_scorer',
    ('caliente', '1er tiro de esquina del partido'): 'first_corner',
    ('caliente', 'Equipo con Más Tiros de Esquina'): 'corners_1x2',
    ('caliente', 'Primer Equipo en Marcar y 1X2'): 'first_scorer_ft',
    ('codere', '1X2'): 'result',
    ('codere', '1X2 Hándicap'): 'hcap_result',
    ('codere', '1X2 Hándicap 1ª Mitad (+1)'): 'ht_hcap',
    ('codere', '1X2 al Descanso'): 'ht_result',
    ('codere', '1X2 y Altas/Bajas Goles (1.5)'): 'result_ou_1.5',
    ('codere', '1X2 y Altas/Bajas Goles (2.5)'): 'result_ou_2.5',
    ('codere', '1X2 y Altas/Bajas Goles (3.5)'): 'result_ou_3.5',
    ('codere', '1X2 y Marcan Ambos Equipos'): 'result_btts',
    ('codere', '1X2 2ª Parte'): '2h_result',
    ('codere', '1ª Parte - 1X2 y Altas/Bajas Goles (0.5)'): 'ht_result_ou_0.5',
    ('codere', '1ª Parte - 1X2 y Altas/Bajas Goles (1.5)'): 'ht_result_ou_1.5',
    ('codere', '1ª Parte - 1X2 y Altas/Bajas Goles (2.5)'): 'ht_result_ou_2.5',
    ('codere', '1ª Parte - 1X2 y Marcan Ambos Equipos'): 'ht_result_btts',
    ('codere', '1ª Parte - Doble Oportunidad y Marcan Ambos Equipos'): 'ht_dc_btts',
    ('codere', 'AWAY_TEAM Marca en Ambas Partes'): 'away_score_bh',
    ('codere', 'Altas 1.5 Goles en Ambas Partes'): 'score_both_halves',
    ('codere', 'Altas/Bajas Total Goles 1ª Parte'): 'ht_ou_total',
    ('codere', 'Altas/Bajas Total Goles 2ª Parte'): '2h_ou_total',
    ('codere', 'Altas/Bajas Total de Goles'): 'ou_total',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 10) (0.5)'): 'ou_1_10_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 20) (0.5)'): 'ou_1_20_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 20) (1.5)'): 'ou_1_20_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 30) (0.5)'): 'ou_1_30_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 30) (1.5)'): 'ou_1_30_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 40) (0.5)'): 'ou_1_40_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 40) (1.5)'): 'ou_1_40_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 40) (2.5)'): 'ou_1_40_2.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 50) (0.5)'): 'ou_1_50_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 50) (1.5)'): 'ou_1_50_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 50) (2.5)'): 'ou_1_50_2.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 60) (0.5)'): 'ou_1_60_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 60) (1.5)'): 'ou_1_60_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 60) (2.5)'): 'ou_1_60_2.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 70) (0.5)'): 'ou_1_70_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 70) (1.5)'): 'ou_1_70_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 70) (2.5)'): 'ou_1_70_2.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 80) (0.5)'): 'ou_1_80_0.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 80) (1.5)'): 'ou_1_80_1.5',
    ('codere', 'Altas/Bajas Total de Goles Entre los Minutos (1 - 80) (2.5)'): 'ou_1_80_2.5',
    ('codere', 'Altas/Bajas Total de Goles Equipo Local'): 'home_ou',
    ('codere', 'Altas/Bajas Total de Goles Equipo Local en la 1ª Parte'): 'ht_home_ou',
    ('codere', 'Altas/Bajas Total de Goles Equipo Local en la 2ª Parte'): '2h_home_ou',
    ('codere', 'Altas/Bajas Total de Goles Equipo Visitante'): 'away_ou',
    ('codere', 'Altas/Bajas Total de Goles Equipo Visitante en la 1ª Parte'): 'ht_away_ou',
    ('codere', 'Altas/Bajas Total de Goles Equipo Visitante en la 2ª Parte'): '2h_away_ou',
    ('codere', 'Anotará o Asistirá'): 'assist_or_score',
    ('codere', 'Apuesta Sin Empate'): 'dnb',
    ('codere', 'Apuesta Sin Empate 1ª Parte'): 'ht_dnb',
    ('codere', 'Apuesta Sin Empate 2ª Parte'): '2h_dnb',
    ('codere', 'Doble Oportunidad'): 'double_chance',
    ('codere', 'Doble Oportunidad 2ª Parte'): '2h_double_chance',
    ('codere', 'Doble Oportunidad del Partido y Marcan Ambos Equipos en la 2ª Parte'): '2h_dc_btts',
    ('codere', 'Doble Oportunidad en la 1ª Parte'): 'ht_double_chance',
    ('codere', 'Doble Oportunidad y Altas/Bajas Goles (0.5)'): 'dc_ou_0.5',
    ('codere', 'Doble Oportunidad y Altas/Bajas Goles (1.5)'): 'dc_ou_1.5',
    ('codere', 'Doble Oportunidad y Altas/Bajas Goles (2.5)'): 'dc_ou_2.5',
    ('codere', 'Doble Oportunidad y Altas/Bajas Goles (3.5)'): 'dc_ou_3.5',
    ('codere', 'Doble Oportunidad y Marcan Ambos Equipos'): 'dc_btts',
    ('codere', 'Doble Oportunidad y Marcan Ambos Equipos 2ª Parte'): '2h_dc_btts2',
    ('codere', 'Equipo Local Gana con Portería a Cero'): 'home_win_to_nil',
    ('codere', 'Equipo Visitante Gana con Portería a Cero'): 'away_win_to_nil',
    ('codere', 'Equipo con más Tiros'): 'corners_1x2',
    ('codere', 'Equipo con más Tiros a Puerta'): 'shots_1x2',
    ('codere', 'Equipo Local Marca Gol'): 'home_scores',
    ('codere', 'Equipo Visitante Marca Gol'): 'away_scores',
    ('codere', 'Ganará Remontando'): 'comeback_win',
    ('codere', 'Ganará Remontando AWAY_TEAM'): 'away_comeback',
    ('codere', 'Ganará Remontando HOME_TEAM'): 'home_comeback',
    ('codere', 'Ganar Alguna de las Dos Partes'): 'win_either_half',
    ('codere', 'Ganar con Portería a Cero'): 'win_to_nil',
    ('codere', 'Ganar en Ambas Partes'): 'win_both_halves',
    ('codere', 'Ganar Sin Portería a Cero'): 'win_no_cs',
    ('codere', 'Goleadores'): 'anytime_scorer',
    ('codere', 'HOME_TEAM Marca en Ambas Partes'): 'home_score_bh',
    ('codere', 'Hándicap Asiático'): 'asian_hcap',
    ('codere', 'Mantener Portería Propia a Cero Goles'): 'clean_sheet',
    ('codere', 'Mantener Portería Propia a Cero Goles - Equipo Local'): 'home_clean_sheet',
    ('codere', 'Mantener Portería Propia a Cero Goles - Equipo Visitante'): 'away_clean_sheet',
    ('codere', 'Mantener Portería Propia a Cero Goles en la 1ª Parte - Equipo Local'): 'ht_home_cs',
    ('codere', 'Mantener Portería Propia a Cero Goles en la 1ª Parte - Equipo Visitante'): 'ht_away_cs',
    ('codere', 'Marca Gol Durante el Partido - Equipos'): 'mark_gol_match',
    ('codere', 'Marcan Ambos Equipos'): 'btts',
    ('codere', 'Marcan Ambos Equipos 1ª Parte'): 'ht_btts',
    ('codere', 'Marcan Ambos Equipos en Ambas Partes'): 'btts_both_halves',
    ('codere', 'Marcan Ambos Equipos en Ambas Partes (1ª Parte / 2ª Parte)'): 'btts_both_halves2',
    ('codere', 'Marcan Ambos Equipos en la 2ª Parte'): '2h_btts',
    ('codere', 'Marcan Ambos Equipos y Altas/Bajas Total de Goles (1.5)'): 'btts_ou_1.5',
    ('codere', 'Marcan Ambos Equipos y Altas/Bajas Total de Goles (2.5)'): 'btts_ou_2.5',
    ('codere', 'Marcan Ambos Equipos y Altas/Bajas Total de Goles (3.5)'): 'btts_ou_3.5',
    ('codere', 'Marcan Ambos Equipos y No Hay Empate'): 'dnb_btts',
    ('codere', 'Marcar en Ambas Partes'): 'score_both_halves',
    ('codere', 'Margen de Victoria'): 'win_margin',
    ('codere', 'Marcará Dos o Más Goles'): 'player_2plus',
    ('codere', 'Numero Exacto de Goles Local'): 'home_exact_goals',
    ('codere', 'Numero Exacto de Goles Visitante'): 'away_exact_goals',
    ('codere', 'Número Exacto de Goles en la 1ª Parte'): 'ht_exact_score',
    ('codere', 'Número Exacto de Goles Equipo Local en la 1ª Parte'): 'ht_home_exact',
    ('codere', 'Número Exacto de Goles Equipo Visitante en la 1ª Parte'): 'ht_away_exact',
    ('codere', 'Número Total de Goles'): 'exact_goals',
    ('codere', 'Par/Impar Total Goles'): 'odd_even',
    ('codere', 'Par/Impar Total Goles 1ª Parte'): 'ht_odd_even',
    ('codere', 'Parte con Más Goles'): 'most_goals_half',
    ('codere', 'Parte con Más Goles AWAY_TEAM'): 'away_most_half',
    ('codere', 'Parte con Más Goles HOME_TEAM'): 'home_most_half',
    ('codere', 'Primer Equipo en Marcar'): 'first_team_scorer',
    ('codere', 'Primer Equipo en Marcar en la 1ª Parte'): 'ht_first_scorer',
    ('codere', 'Primer Equipo en Marcar y 1X2'): 'first_scorer_ft',
    ('codere', 'Primer Equipo en Marcar – 2ª Parte'): '2h_first_scorer',
    ('codere', 'Resultado Final'): 'result',
    ('codere', 'Resultado Final - 1ª Parte'): 'ht_result',
    ('codere', 'Resultado Final - 2ª Parte'): '2h_result',
    ('codere', 'Resultado Final (Múltiples Resultados)'): 'result_multilines',
    ('codere', 'Resultado al Descanso o Final'): 'ft_result_or_ht',
    ('codere', 'Resultado del Partido Entre los Minutos'): 'result_10min',
    ('codere', 'Resultado 1ª Parte y Resto del Partido'): 'htft',
    ('codere', 'Se Adelantan Durante el Partido'): 'se_adelantan',
    ('codere', 'Tiros HOME_TEAM (12.5)'): 'home_shots_ou',
    ('codere', 'Tiros AWAY_TEAM (12.5)'): 'away_shots_ou',
    ('codere', 'Tiros a Puerta HOME_TEAM (4.5)'): 'home_sot_ou',
    ('codere', 'Tiros a Puerta AWAY_TEAM (3.5)'): 'away_sot_ou',
    ('codere', 'Total Goles 2ª Parte'): 'ht_total_goals',
    ('codere', 'Total Tiros (25.5)'): 'total_shots',
    ('codere', 'Total Tiros a Puerta (8.5)'): 'total_sot',
    ('codere', 'Último Equipo en Marcar'): 'last_team_scorer',
    ('codere', '2º Mitad - Se Marcará Gol'): '2h_goal_scored',
    ('codere', '¿Cuándo se Marca el 1er gol? (Gol 1)'): 'first_goal_time',
    ('codere', '¿Habrá Penalti en la 1ª Parte?'): 'penalty',
    ('playdoit', '1X2 1ª mitad / Doble oportunidad (partido)'): 'ht_result_or_ft',
    ('playdoit', '1ª Mitad - 1x2'): 'ht_result',
    ('playdoit', '1ª Mitad - 1x2 y ambos equipos marcan'): 'ht_result_btts',
    ('playdoit', '1ª Mitad - 1x2 y total'): 'ht_result_ou_1.5',
    ('playdoit', '1ª Mitad - AWAY_TEAM Portería a cero'): 'ht_away_cs',
    ('playdoit', '1ª Mitad - AWAY_TEAM total'): 'ht_away_ou',
    ('playdoit', '1ª Mitad - Apuesta sin empate'): 'ht_dnb',
    ('playdoit', '1ª Mitad - Hándicap'): 'ht_hcap',
    ('playdoit', '1ª Mitad - HOME_TEAM Portería a cero'): 'ht_home_cs',
    ('playdoit', '1ª Mitad - HOME_TEAM total'): 'ht_home_ou',
    ('playdoit', '1ª Mitad - Par/Impar'): 'ht_odd_even',
    ('playdoit', '1ª Mitad - Ultimo Tiro De Esquina'): 'last_corner',
    ('playdoit', '1ª Mitad - ambos equipos marcan'): 'ht_btts',
    ('playdoit', '1ª Mitad - doble oportunidad'): 'ht_double_chance',
    ('playdoit', '1ª Mitad - hándicap 1X2'): 'ht_hcap_1x2',
    ('playdoit', '1ª Mitad - primer Tiro de esquina'): 'first_5_corners',
    ('playdoit', '1ª Mitad - primer gol'): 'ht_first_scorer',
    ('playdoit', '1ª Mitad - total'): 'ht_ou_total',
    ('playdoit', '1ª mitad  - Tiros de esquina exacto AWAY_TEAM'): 'ht_away_corners',
    ('playdoit', '1ª mitad - AWAY_TEAM marcará'): 'ht_away_scores',
    ('playdoit', '1ª mitad - AWAY_TEAM par/impar'): 'ht_away_odd_even',
    ('playdoit', '1ª mitad - Doble oportunidad y ambos equipos marcan'): 'ht_dc_btts',
    ('playdoit', '1ª mitad - Escala de tiros de esquina'): 'ht_corners_range',
    ('playdoit', '1ª mitad - Goles exacto'): 'ht_exact_goals',
    ('playdoit', '1ª mitad - Hándicap  de tiros de esquina'): 'ht_corners_hcap',
    ('playdoit', '1ª mitad - HOME_TEAM marcará'): 'ht_home_scores',
    ('playdoit', '1ª mitad - HOME_TEAM par/impar'): 'ht_home_odd_even',
    ('playdoit', '1ª mitad - Tiros de Esquina  Par/Impar'): 'ht_corners_odd',
    ('playdoit', '1ª mitad - Tiros de esquina 1x2'): 'ht_corners_1x2',
    ('playdoit', '1ª mitad - Tiros de esquina exacto HOME_TEAM'): 'ht_home_corners',
    ('playdoit', '1ª mitad - Total Tiros de Esquina'): 'ht_corners_ou_4.5',
    ('playdoit', '1ª mitad - marcador exacto'): 'ht_exact_score',
    ('playdoit', '1ª mitad - multigoles'): 'ht_goal_range',
    ('playdoit', '1ª/2ª mitad ambos equipos marcan'): 'btts_both_halves',
    ('playdoit', '1x2'): 'result',
    ('playdoit', '1x2 (partido) & 1º tiempo ambos equipos marcan'): 'ft_result_ht_btts',
    ('playdoit', '1x2 (partido) & 2ª mitad ambos equipos marcan'): 'ft_result_2h_btts',
    ('playdoit', '1x2 y ambos equipos marcan'): 'result_btts',
    ('playdoit', '1x2 y total'): 'result_ou_2.5',
    ('playdoit', '2ª Mitad - 1x2'): '2h_result',
    ('playdoit', '2ª Mitad - 1x2 y ambos equipos marcan'): '2h_result_btts',
    ('playdoit', '2ª Mitad - 1x2 y total'): '2h_result_ou_1.5',
    ('playdoit', '2ª Mitad - AWAY_TEAM Portería a cero'): '2h_away_cs',
    ('playdoit', '2ª Mitad - AWAY_TEAM marcará'): '2h_away_scores',
    ('playdoit', '2ª Mitad - AWAY_TEAM total'): '2h_away_ou',
    ('playdoit', '2ª Mitad - Apuesta sin empate'): '2h_dnb',
    ('playdoit', '2ª Mitad - Doble oportunidad y ambos equipos marcan'): '2h_dc_btts',
    ('playdoit', '2ª Mitad - HOME_TEAM Portería a cero'): '2h_home_cs',
    ('playdoit', '2ª Mitad - HOME_TEAM marcará'): '2h_home_scores',
    ('playdoit', '2ª Mitad - HOME_TEAM total'): '2h_home_ou',
    ('playdoit', '2ª Mitad - ambos equipos marcan'): '2h_btts',
    ('playdoit', '2ª Mitad - doble oportunidad'): '2h_double_chance',
    ('playdoit', '2ª Mitad - hándicap'): '2h_hcap',
    ('playdoit', '2ª Mitad - hándicap 1X2'): '2h_hcap_1x2',
    ('playdoit', '2ª Mitad - marcador exacto'): '2h_exact_score',
    ('playdoit', '2ª Mitad - multigoles'): '2h_goal_range',
    ('playdoit', '2ª Mitad - par/impar'): '2h_odd_even',
    ('playdoit', '2ª Mitad - primer gol'): '2h_first_scorer',
    ('playdoit', '2ª Mitad - total'): '2h_ou_total',
    ('playdoit', '2ª mitad - Goles exactos'): '2h_exact_goals',
    ('playdoit', 'AWAY_TEAM  Total de Tiros de Esquina'): 'away_corners_ou_5.5',
    ('playdoit', 'AWAY_TEAM  remontará y ganará'): 'away_comeback',
    ('playdoit', 'AWAY_TEAM Escala de tiros de esquina'): 'away_corners',
    ('playdoit', 'AWAY_TEAM Marca en ambos tiempos'): 'away_score_bh',
    ('playdoit', 'AWAY_TEAM N° de Goles exactos'): 'away_exact_goals',
    ('playdoit', 'AWAY_TEAM Portería a cero'): 'away_clean_sheet',
    ('playdoit', 'AWAY_TEAM gana'): 'away_win',
    ('playdoit', 'AWAY_TEAM gana a cero'): 'away_win_to_nil',
    ('playdoit', 'AWAY_TEAM gana ambas mitades'): 'away_win_bh',
    ('playdoit', 'AWAY_TEAM gana cualquier mitad'): 'away_win_eh',
    ('playdoit', 'AWAY_TEAM liderará durante el partido'): 'away_lead',
    ('playdoit', 'AWAY_TEAM marcará'): 'away_scores',
    ('playdoit', 'AWAY_TEAM marcará 2 goles consecutivos'): 'away_consec',
    ('playdoit', 'AWAY_TEAM mitad de mayor marcador'): 'away_most_half',
    ('playdoit', 'AWAY_TEAM multigoles'): 'away_2plus',
    ('playdoit', 'AWAY_TEAM o ambos equipos marcan'): 'away_or_btts',
    ('playdoit', 'AWAY_TEAM o cualquier portería a cero'): 'any_cs_away',
    ('playdoit', 'AWAY_TEAM o menos de 2.5'): 'away_or_under',
    ('playdoit', 'AWAY_TEAM o más de 2.5'): 'away_or_over',
    ('playdoit', 'AWAY_TEAM par/impar'): 'away_odd_even',
    ('playdoit', 'AWAY_TEAM sin apuesta'): 'dnb_away',
    ('playdoit', 'AWAY_TEAM total de goles'): 'away_ou',
    ('playdoit', 'Al menos un equipo marcará 2 goles consecutivos'): 'consec_goals_team',
    ('playdoit', 'Ambas mitades menos de 1.5'): 'under_1.5_both',
    ('playdoit', 'Ambas mitades menos de 2.5'): 'under_2.5_both',
    ('playdoit', 'Ambas mitades más de 0.5'): 'ou_0.5_both_halves',
    ('playdoit', 'Ambas mitades más de 1.5'): 'ou_1.5_both_halves',
    ('playdoit', 'Ambos equipos marcan'): 'btts',
    ('playdoit', 'Ambos los equipos liderarán durante el partido'): 'teams_lead',
    ('playdoit', 'Ambos los equipos marcarán 2 goles consecutivos'): 'both_consec',
    ('playdoit', 'Boosted Odds'): 'boosted',
    ('playdoit', 'Cualquier equipo gana'): 'any_team_wins',
    ('playdoit', 'Doble oportunidad'): 'double_chance',
    ('playdoit', 'Doble oportunidad (partido) y ambos equipos marcan 1ª mitad'): 'ft_dc_ht_btts',
    ('playdoit', 'Doble oportunidad (partido) y ambos equipos marcan 2ª mitad'): 'ft_dc_2h_btts',
    ('playdoit', 'Doble oportunidad 1º mitad / 1X2 (partido)'): 'ht_dc_ft_1x2',
    ('playdoit', 'Doble oportunidad 1º mitad / Doble oportunidad (partido)'): 'ht_dc_ft_dc',
    ('playdoit', 'Doble oportunidad y ambos equipos marcan'): 'dc_btts',
    ('playdoit', 'Doble oportunidad y total 1.5 de goles'): 'dc_ou_1.5',
    ('playdoit', 'Doble oportunidad y total 2.5 de goles'): 'dc_ou_2.5',
    ('playdoit', 'Doble oportunidad y total 3.5 de goles'): 'dc_ou_3.5',
    ('playdoit', 'Doble oportunidad y total 4.5 de goles'): 'dc_ou_4.5',
    ('playdoit', 'Doble oportunidad y total 5.5 de goles'): 'dc_ou_5.5',
    ('playdoit', 'Empate No Accion'): 'dnb',
    ('playdoit', 'Empate o ambos equipos marcan'): 'draw_btts',
    ('playdoit', 'Empate o en blanco'): 'draw_blank',
    ('playdoit', 'Empate o menos de 2.5'): 'draw_under_2.5',
    ('playdoit', 'Empate o más de 2.5'): 'draw_over_2.5',
    ('playdoit', 'Escala de goles'): 'goal_range',
    ('playdoit', 'Escala de tiros de Esquina'): 'corners_range',
    ('playdoit', 'Escala de tiros de esquina'): 'corners_range_lc',
    ('playdoit', 'Goleador'): 'anytime_scorer',
    ('playdoit', 'Goles exactos'): 'exact_goals',
    ('playdoit', 'HOME_TEAM  remontará y ganará'): 'home_comeback',
    ('playdoit', 'HOME_TEAM Escala de tiros de esquina'): 'home_corners',
    ('playdoit', 'HOME_TEAM N° de Goles exactos'): 'home_exact_goals',
    ('playdoit', 'HOME_TEAM Portería a cero'): 'home_clean_sheet',
    ('playdoit', 'HOME_TEAM Total de Tiros de Esquina'): 'home_corners_ou_4.5',
    ('playdoit', 'HOME_TEAM gana'): 'home_win',
    ('playdoit', 'HOME_TEAM gana a cero'): 'home_win_to_nil',
    ('playdoit', 'HOME_TEAM gana ambas mitades'): 'home_win_bh',
    ('playdoit', 'HOME_TEAM gana cualquier mitad'): 'home_win_eh',
    ('playdoit', 'HOME_TEAM liderará durante el partido'): 'home_lead',
    ('playdoit', 'HOME_TEAM marca en ambos tiempos'): 'home_score_bh',
    ('playdoit', 'HOME_TEAM marcará'): 'home_scores',
    ('playdoit', 'HOME_TEAM marcará 2 goles consecutivos'): 'home_consec',
    ('playdoit', 'HOME_TEAM mitad de mayor marcador'): 'home_most_half',
    ('playdoit', 'HOME_TEAM multigoles'): 'home_2plus',
    ('playdoit', 'HOME_TEAM o ambos equipos marcan'): 'home_or_btts',
    ('playdoit', 'HOME_TEAM o cualquier portería a cero'): 'any_cs',
    ('playdoit', 'HOME_TEAM o menos de 2.5'): 'home_or_under',
    ('playdoit', 'HOME_TEAM o más de 2.5'): 'home_or_over',
    ('playdoit', 'HOME_TEAM par/impar'): 'home_odd_even',
    ('playdoit', 'HOME_TEAM sin apuesta'): 'dnb_home',
    ('playdoit', 'HOME_TEAM total de goles'): 'home_ou',
    ('playdoit', 'Hándicap 1x2'): 'hcap_result',
    ('playdoit', 'Hándicap Asiatico'): 'asian_hcap',
    ('playdoit', 'Hándicap en Tiros de Esquina'): 'corners_hcap',
    ('playdoit', 'Intervalo con mas goles'): 'most_goals_half',
    ('playdoit', 'Marcador exacto'): 'exact_score',
    ('playdoit', 'Marcador exacto XL'): 'exact_score_xl',
    ('playdoit', 'Margen de victoria'): 'win_margin',
    ('playdoit', 'Multigoleadores'): 'multi_goalscorers',
    ('playdoit', 'Multigoles'): 'multigoles',
    ('playdoit', 'Multimarcadores'): 'multi_scorers',
    ('playdoit', 'Par/Impar'): 'odd_even',
    ('playdoit', 'Primer Gol'): 'first_team_scorer',
    ('playdoit', 'Primer Tiros de esquina'): 'first_corner',
    ('playdoit', 'Primera mitad/final del partido'): 'htft',
    ('playdoit', 'Primera mitad/final del partido y 1ª mitad total 1.5'): 'htft_ht_ou_1.5',
    ('playdoit', 'Primera mitad/final del partido y 1ª mitad total 2.5'): 'htft_ht_ou_2.5',
    ('playdoit', 'Primera mitad/final del partido y 1ª mitad total 3.5'): 'htft_ht_ou_3.5',
    ('playdoit', 'Primera mitad/final del partido y exacto goles'): 'htft_exact',
    ('playdoit', 'Primera mitad/final del partido y marcador exacto'): 'htft_exact_score',
    ('playdoit', 'Primera mitad/final del partido y total 1.5'): 'htft_ou_1.5',
    ('playdoit', 'Primera mitad/final del partido y total 2.5'): 'htft_ou_2.5',
    ('playdoit', 'Primera mitad/final del partido y total 3.5'): 'htft_ou_3.5_pld',
    ('playdoit', 'Primera mitad/final del partido y total 4.5'): 'htft_ou_4.5',
    ('playdoit', 'Primera mitad/final del partido y total 5.5'): 'htft_ou_5.5',
    ('playdoit', 'Primero gol y 1x2'): 'first_scorer_ft',
    ('playdoit', 'Qué equipo marca'): 'next_scorer_method',
    ('playdoit', 'Tiros de esquina 1x2'): 'corners_1x2',
    ('playdoit', 'Tiros de esquina Par/Impar'): 'corners_odd_even',
    ('playdoit', 'Total'): 'ou_total',
    ('playdoit', 'Total Tiros De Esquina'): 'corners_ou_9.5',
    ('playdoit', 'Total de tarjetas'): 'cards_total',
    ('playdoit', 'Total tarjetas Impar/Par'): 'cards_odd_even',
    ('playdoit', 'Total y ambos equipos marcan'): 'btts_ou_2.5',
    ('playdoit', 'Último Tiro de esquina'): 'last_corner_match',
    ('playdoit', 'Último gol'): 'last_team_scorer',
    ('playdoit', 'Último gol de la 1a Mitad'): 'ht_last_scorer',
    ('playdoit', 'Último gol de la 2a Mitad'): '2h_last_scorer',
}


MARKET_DISPLAY = {
    'result':           'Match Winner (1X2)',
    'double_chance':    'Double Chance',
    'btts':             'Both Teams to Score',
    'ou_total':         'Total Goals O/U',
    'asian_hcap':       'Asian Handicap',
    'exact_score':      'Correct Score',
    'dnb':              'Draw No Bet',
    'win_margin':       'Margin of Victory',
    'win_to_nil':       'Win to Nil',
    'clean_sheet':      'Clean Sheet',
    'odd_even':         'Odd/Even Goals',
    'most_goals_half':  'Highest Scoring Half',
    'first_team_scorer':'First Team to Score',
    'first_goal_time':  'First Goal Time',
    'penalty':          'Penalty Awarded',
    'ht_result':        'HT Result',
    'ht_ou_total':      'HT Total Goals O/U',
    'ht_dnb':           'HT Draw No Bet',
    'ht_home_scores':   'HT Home to Score',
    'ht_away_scores':   'HT Away to Score',
    'ht_home_cs':       'HT Home Clean Sheet',
    'ht_away_cs':       'HT Away Clean Sheet',
    '2h_result':        '2H Result',
    '2h_dnb':           '2H Draw No Bet',
    '2h_first_scorer':  '2H First Goal',
    'win_both_halves':  'Win Both Halves',
    'win_either_half':  'Win Either Half',
    'team_score_both':  'Team Scores Both Halves',
    'home_ou':          'Home Total Goals O/U',
    'away_ou':          'Away Total Goals O/U',
    'home_exact_goals': 'Home Exact Goals',
    'away_exact_goals': 'Away Exact Goals',
    'home_scores':      'Home to Score',
    'away_scores':      'Away to Score',
    'home_clean_sheet': 'Home Clean Sheet',
    'away_clean_sheet': 'Away Clean Sheet',
    'home_comeback':    'Home Win from Behind',
    'away_comeback':    'Away Win from Behind',
    'anytime_scorer':   'Anytime Goalscorer',
    'first_scorer':     'First Goalscorer',
    'player_2plus':     'Player to Score 2+',
    'player_3plus':     'Player to Score 3+',
    'player_shots':     'Player Shots O/U',
    'player_sot':       'Player Shots on Target O/U',
    'player_assists':   'Player Assists',
    'player_passes':    'Player Passes',
    'player_tackles':   'Player Tackles',
    'player_cards':     'Player Cards',
    'player_score_assist': 'Player to Score or Assist',
    'corners_ou_9.5':   'Total Corners O/U',
    'total_shots':      'Shots (Total)',
    'total_sot':        'Shots on Target',
    'total_assists':    'Assists (Total)',
    'total_saves':      'Saves (Goalkeeper)',
    'cards_total':      'Cards - Total',
    'first_card':       'First Card',
}
MARKET_FILTER = set(MARKET_DISPLAY)


def _normalize_placeholders(name, home_team, away_team):
    """Replace actual team names with HOME_TEAM/AWAY_TEAM placeholders."""
    if home_team:
        name = name.replace(home_team, 'HOME_TEAM')
    if away_team:
        name = name.replace(away_team, 'AWAY_TEAM')
    return name


def canonical_for(book, market_name, home_team='', away_team=''):
    """Return canonical key for a book's market name, or None if unknown."""
    normalized = _normalize_placeholders(market_name, home_team, away_team)
    return MARKET_MAP.get((book, normalized))


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


def compute_comparison(book_markets, home_team='', away_team=''):
    """
    book_markets: {'caliente': [{name, selections}], 'codere': [...], ...}
    home_team / away_team: actual team name strings for placeholder substitution.
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
            key = canonical_for(book, mkt['name'], home_team, away_team)
            if key is None:
                continue
            if key not in canonical:
                canonical[key] = {'display_name': mkt['name'], 'books': {}}
            # Don't overwrite with a less-named version; first seen wins per book
            if book not in canonical[key]['books']:
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

        if key not in MARKET_FILTER:
            continue

        rows.append({
            'canonical': key,
            'display_name': MARKET_DISPLAY.get(key, data['display_name']),
            'tier': tier,
            'books': book_count,
            'max_gap': round(overall_gap, 4),
            'selections': sel_rows,
        })

    # Sort: tier asc, then gap desc within tier
    rows.sort(key=lambda r: (r['tier'], -r['max_gap']))
    return rows


###############################################################################
# SUPABASE UPSERT — two tables
###############################################################################

def _supabase_post(table, payload, label):
    """POST to a Supabase table with upsert semantics."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates',
    }
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code not in (200, 201):
        print(f"⚠️  Supabase upsert [{table}] failed for {label}: {r.status_code} {r.text[:200]}")
        return False
    return True


def supabase_upsert_raw(match_name, league, match_date, book_markets):
    """Store full per-book markets in match_data_raw — no filtering."""
    ok = _supabase_post('match_data_raw', {
        'match_name': match_name,
        'league':     league,
        'match_date': match_date,
        'book_markets': book_markets,
        'scraped_at': datetime.utcnow().isoformat() + 'Z',
    }, match_name)
    if ok:
        total = sum(len(v) for v in book_markets.values())
        print(f"✅ Raw upsert {match_name}: {list(book_markets.keys())} — {total} markets")


def supabase_upsert(match_name, league, match_date, comparison_rows):
    """Store processed comparison rows in match_comparisons."""
    ok = _supabase_post('match_comparisons', {
        'match_name': match_name,
        'league':     league,
        'match_date': match_date,
        'markets':    comparison_rows,
        'scraped_at': datetime.utcnow().isoformat() + 'Z',
    }, match_name)
    if ok:
        print(f"✅ Comparison upsert {match_name}: {len(comparison_rows)} compared markets")




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


@app.route('/compare.html')
def compare():
    import os
    path = os.path.join(os.path.dirname(__file__), 'compare.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}


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
        # Derive match name — prefer books that don't use fallback placeholder names
        match_name = None
        for b in ['caliente', 'codere', '1win', 'playdoit'] + list(results.keys()):
            r = results.get(b)
            if not r or r.get('status') != 'ok' or not r.get('txt'):
                continue
            first_line = r['txt'].split('\n')[0]
            if ' — ' in first_line:
                name = first_line.split(' — ', 1)[1].strip()
                if 'Local' not in name and 'Visitante' not in name:
                    match_name = name
                    break

        if match_name:
            # Extract home/away team names for MARKET_MAP placeholder substitution
            if ' vs ' in match_name:
                home_team, away_team = match_name.split(' vs ', 1)
            else:
                home_team, away_team = '', ''

            match_date = datetime.now().strftime('%Y-%m-%d')

            # 1. Store full raw data (no filtering)
            supabase_upsert_raw(match_name, 'Liga MX', match_date, book_markets)

            # 2. Compute comparison using MARKET_MAP and store processed result
            comparison_rows = compute_comparison(book_markets, home_team.strip(), away_team.strip())
            supabase_upsert(match_name, 'Liga MX', match_date, comparison_rows)

    return jsonify(results)


if __name__ == '__main__':
    print("🚀  Bettor LATAM Scraper → http://localhost:5050")
    app.run(debug=False, port=5050)
