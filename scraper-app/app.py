"""
Bettor LATAM — Scraper Web App
Run: python app.py
Open: http://localhost:5050
Requires: FIRECRAWL_API_KEY env var (for Caliente + 1Win)
"""

import os, re, json, concurrent.futures, urllib.request
from datetime import datetime
from html.parser import HTMLParser
import requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
FC_KEY = os.environ.get('FIRECRAWL_API_KEY', '')


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
            else:
                seen, selns = set(), []
                for col in mkt.get('desktopOddIds', []):
                    for oid in col:
                        if oid in seen: continue
                        seen.add(oid)
                        odd = odds_map.get(oid)
                        if odd and odd.get('price', 0) > 1:
                            selns.append((odd['name'], american(odd['price'])))

                if selns:
                    group_lines.append(f"\n  {mkt['name']}")
                    for name, price in selns:
                        group_lines.append(f"    {name}: {price}")
                    total_m += 1
                    total_s += len(selns)
                    group_used = True

        if group_used:
            lines.extend(group_lines)

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), total_m, total_s


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

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), total_m, total_s


###############################################################################
# CALIENTE  — Firecrawl (1 credit), Cloudflare bypass + JS click-all
###############################################################################

def scrape_caliente(url):
    if not FC_KEY:
        raise ValueError("FIRECRAWL_API_KEY not set. Export it: export FIRECRAWL_API_KEY=fc-...")

    fc_url = re.sub(r'\?.*', '', url) + '?show_all=Y'

    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {FC_KEY}", "Content-Type": "application/json"},
        json={
            "url": fc_url,
            "formats": ["rawHtml"],
            "actions": [
                {"type": "wait", "milliseconds": 5000},
                {"type": "executeJavascript",
                 "script": "document.querySelectorAll('.expander-button').forEach(b => b.click())"},
                {"type": "wait", "milliseconds": 12000}
            ]
        },
        timeout=90
    )
    resp.raise_for_status()
    html = resp.json()['data']['rawHtml']

    t = re.search(r'<title>([^<]+)</title>', html)
    event_name = t.group(1).strip() if t else "Caliente Event"

    mkt_re = re.compile(r'<div class="[^"]*\bmkt\b[^"]*"\s+data-mkt_id="(\d+)"\s*(?:data-fetch_url="[^"]*")?>')
    btn_re = re.compile(r'<button[^>]*>(.*?)</button>', re.DOTALL)
    positions = [(m.group(1), m.start()) for m in mkt_re.finditer(html)]

    lines = [f"Caliente — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
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

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), total_m, total_s


###############################################################################
# 1WIN  — Firecrawl (1 credit), JS injection to extract markets
###############################################################################

def scrape_1win(url):
    if not FC_KEY:
        raise ValueError("FIRECRAWL_API_KEY not set. Export it: export FIRECRAWL_API_KEY=fc-...")

    m = re.search(r'-(\d{7,})(?:[?&/]|$)', url)
    if not m:
        raise ValueError("Could not extract matchId from 1Win URL (expected: ...-33470209)")
    match_id = m.group(1)

    # JS that extracts all markets and stores them in a hidden div
    EXTRACT_JS = r"""
        const mkts = [];
        document.querySelectorAll('[class*="_root_m2ytg"]').forEach(root => {
            const titleEl = root.querySelector('[class*="_title_8ulje"]');
            if (!titleEl) return;
            const title = titleEl.textContent.trim();
            const selns = [];
            root.querySelectorAll('button[type="button"]').forEach(btn => {
                const txt = btn.textContent.trim().replace(/\s+/g, ' ');
                const m = txt.match(/^(.+?)([+-]\d+)$/);
                if (m) selns.push({selection: m[1].trim(), american: m[2]});
            });
            if (selns.length) mkts.push({name: title, selections: selns});
        });
        const div = document.createElement('div');
        div.id = '__bettor_data__';
        div.setAttribute('style', 'display:none');
        div.textContent = JSON.stringify(mkts);
        document.body.appendChild(div);
    """

    base_url = re.sub(r'\?.*', '', url)
    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {FC_KEY}", "Content-Type": "application/json"},
        json={
            "url": base_url,
            "formats": ["rawHtml"],
            "actions": [
                {"type": "wait", "milliseconds": 6000},
                {"type": "executeJavascript", "script": EXTRACT_JS},
            ]
        },
        timeout=90
    )
    resp.raise_for_status()
    html = resp.json()['data']['rawHtml']

    m2 = re.search(r'id="__bettor_data__"[^>]*>(\[.*?\])<', html, re.DOTALL)
    if not m2:
        raise ValueError("1Win: JS extraction failed — page may not have rendered in time")

    markets_data = json.loads(m2.group(1))

    t = re.search(r'<title>([^<]+)</title>', html)
    event_name = t.group(1).strip() if t else f"1Win {match_id}"

    lines = [f"1Win — {event_name}", f"Scraped: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    total_m = total_s = 0

    for mkt in markets_data:
        name  = mkt.get('name', 'UNKNOWN')
        selns = mkt.get('selections', [])
        if selns:
            lines.append(f"\n=== {name} ===")
            for s in selns:
                lines.append(f"  {s['selection']}: {s['american']}")
            total_m += 1
            total_s += len(selns)

    lines.append(f"\n\nTotal: {total_m} mercados, {total_s} selecciones")
    return '\n'.join(lines), total_m, total_s


###############################################################################
# FLASK APP
###############################################################################

SCRAPERS = {
    'caliente': scrape_caliente,
    'codere':   scrape_codere,
    '1win':     scrape_1win,
    'playdoit': scrape_playdoit,
}

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
.status { margin-top: 9px; font-size: 0.75rem; height: 18px; color: #3a3a3a; }
.status.loading { color: #f59e0b; }
.status.ok      { color: #22c55e; }
.status.error   { color: #ef4444; }

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
    <div class="status" id="st-caliente"></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-codere"></div>
      <div><div class="book-name">Codere</div><div class="book-hint">apuestas.codere.mx/es_MX/e/{id}/{slug}</div></div>
    </div>
    <input type="url" id="url-codere" placeholder="https://apuestas.codere.mx/es_MX/e/12345/...">
    <div class="status" id="st-codere"></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-1win"></div>
      <div><div class="book-name">1Win</div><div class="book-hint">1witeo.life/betting/match/sport/...-{matchId}</div></div>
    </div>
    <input type="url" id="url-1win" placeholder="https://1witeo.life/betting/match/sport/...-33470209">
    <div class="status" id="st-1win"></div>
  </div>

  <div class="card">
    <div class="book-header">
      <div class="dot dot-playdoit"></div>
      <div><div class="book-name">PlayDoit</div><div class="book-hint">#page=event&amp;eventId={id}</div></div>
    </div>
    <input type="url" id="url-playdoit" placeholder="https://www.playdoit.mx/#page=event&eventId=12345">
    <div class="status" id="st-playdoit"></div>
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
        try:
            txt, markets, selections = SCRAPERS[book](url)
            return book, {
                'status': 'ok',
                'txt': txt,
                'stats': f"{markets} mercados · {selections} selecciones"
            }
        except Exception as e:
            return book, {'status': 'error', 'error': str(e)}

    results = {}
    jobs = {book: url for book, url in data.items() if book in SCRAPERS and url}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run, book, url): book for book, url in jobs.items()}
        for future in concurrent.futures.as_completed(futures, timeout=120):
            book, result = future.result()
            results[book] = result

    return jsonify(results)


if __name__ == '__main__':
    if not FC_KEY:
        print("⚠️  FIRECRAWL_API_KEY not set — Caliente and 1Win scrapers will fail.")
        print("   Export it: export FIRECRAWL_API_KEY=fc-your-key-here\n")
    print("🚀  Bettor LATAM Scraper → http://localhost:5050")
    app.run(debug=False, port=5050)
