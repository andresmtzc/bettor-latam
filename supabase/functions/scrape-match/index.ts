/**
 * scrape-match — Supabase Edge Function (Deno)
 *
 * Scrapes all markets/odds for a match from 4 MX sportsbooks in parallel.
 *
 * POST /scrape-match
 * Headers: Authorization: Bearer <supabase_jwt>
 * Body: { caliente?: string, codere?: string, "1win"?: string, playdoit?: string }
 *
 * Returns: { caliente?: Result, codere?: Result, "1win"?: Result, playdoit?: Result }
 * Result: { status: "ok", txt: string, stats: string } | { status: "error", error: string }
 *
 * Cost: 2 Firecrawl credits per full match (Caliente + 1Win)
 * Speed: ~20s wall-clock (parallel, Caliente is bottleneck)
 */

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL      = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const FC_KEY            = Deno.env.get("FIRECRAWL_API_KEY")!;

const CORS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
};

// ─── Types ───────────────────────────────────────────────────────────────────

interface Selection { selection: string; american: string }
interface Market    { name: string; selections: Selection[] }
interface ScraperResult {
  status: "ok" | "error";
  txt?: string;
  stats?: string;
  error?: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function now() {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

function buildTxt(book: string, eventName: string, markets: Market[]): string {
  const lines: string[] = [
    `${book} — ${eventName}`,
    `Scraped: ${now()}\n`,
  ];
  for (const mkt of markets) {
    lines.push(`\n=== ${mkt.name} ===`);
    for (const s of mkt.selections) {
      lines.push(`  ${s.selection}: ${s.american}`);
    }
  }
  const total_s = markets.reduce((n, m) => n + m.selections.length, 0);
  lines.push(`\n\nTotal: ${markets.length} mercados · ${total_s} selecciones`);
  return lines.join("\n");
}

// ─── PlayDoit ─────────────────────────────────────────────────────────────────
// Altenar GetEventDetails API — plain GET, no auth, $0

async function scrapePlayDoit(url: string): Promise<ScraperResult> {
  const m = url.match(/eventId=(\d+)/);
  if (!m) throw new Error("No eventId in URL. Expected: #page=event&eventId=12345");
  const eventId = m[1];

  const apiUrl = `https://sb2frontend-altenar2.biahosted.com/api/widget/GetEventDetails` +
    `?culture=es-ES&timezoneOffset=240&integration=playdoit2` +
    `&deviceType=1&numFormat=en-GB&countryCode=US` +
    `&eventId=${eventId}&showNonBoosts=false`;

  const r = await fetch(apiUrl);
  if (!r.ok) throw new Error(`Altenar API ${r.status}`);
  const d = await r.json();

  const oddsMap:   Record<number, { name: string; price: number }> = {};
  const mktsMap:   Record<number, { name: string; desktopOddIds?: number[][]; childMarketIds?: number[]; headers?: { odds: { name: string }[] }[] }> = {};
  const childMap:  Record<number, { childName: string; desktopOddIds?: number[][] }> = {};

  for (const o of d.odds       ?? []) oddsMap[o.id]  = o;
  for (const m of d.markets    ?? []) mktsMap[m.id]   = m;
  for (const c of d.childMarkets ?? []) childMap[c.id] = c;

  function american(dec: number): string {
    return dec >= 2 ? `+${Math.round((dec - 1) * 100)}` : `${Math.round(-100 / (dec - 1))}`;
  }

  const ev = d.event ?? {};
  const eventName = `${ev.homeTeamName ?? "Local"} vs ${ev.awayTeamName ?? "Visitante"}`;

  const markets: Market[] = [];

  for (const group of d.marketGroups ?? []) {
    for (const mid of group.marketIds ?? []) {
      const mkt = mktsMap[mid];
      if (!mkt) continue;

      if (mkt.childMarketIds?.length) {
        // Player prop market (Goleador, etc.)
        const colNames: string[] = mkt.headers?.[0]?.odds.map(o => o.name) ?? [];
        const playerSelns: Selection[] = [];

        for (const cid of mkt.childMarketIds) {
          const cm = childMap[cid];
          if (!cm) continue;
          const player = cm.childName ?? "?";
          const prices: string[] = (cm.desktopOddIds ?? []).map(col => {
            const oid = col[0];
            const odd = oid ? oddsMap[oid] : null;
            return odd && odd.price > 1 ? american(odd.price) : "-";
          });
          if (prices.some(p => p !== "-")) {
            const label = colNames.length
              ? `${player} (${prices.join(" / ")})`
              : player;
            const price = prices.find(p => p !== "-") ?? "-";
            playerSelns.push({ selection: label, american: price });
          }
        }
        if (playerSelns.length) markets.push({ name: mkt.name, selections: playerSelns });
      } else {
        // Standard market
        const seen = new Set<number>();
        const selns: Selection[] = [];
        for (const col of mkt.desktopOddIds ?? []) {
          for (const oid of col) {
            if (seen.has(oid)) continue;
            seen.add(oid);
            const odd = oddsMap[oid];
            if (odd && odd.price > 1) selns.push({ selection: odd.name, american: american(odd.price) });
          }
        }
        if (selns.length) markets.push({ name: mkt.name, selections: selns });
      }
    }
  }

  const txt   = buildTxt("PlayDoit", eventName, markets);
  const total_s = markets.reduce((n, m) => n + m.selections.length, 0);
  return { status: "ok", txt, stats: `${markets.length} mercados · ${total_s} selecciones` };
}

// ─── Codere ───────────────────────────────────────────────────────────────────
// Strategy:
//   1. Try direct HTTP fetch of the page (free, works if Cloudflare allows it).
//   2. If CF blocks (< 10 market IDs), fall back to Firecrawl (1 credit) with
//      async fetch JS that returns a Promise so Firecrawl awaits it properly.
//   3. Fetch each /web_nr endpoint directly from Edge Function (NOT CF-protected).

function parseCodereHtml(html: string): { ids: string[]; nameMap: Map<string, string>; eventName: string } {
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const m of html.matchAll(/data-mkt_id="([^"]+)"/g)) {
    for (const id of m[1].split(",")) {
      const t = id.trim();
      if (t && !seen.has(t)) { seen.add(t); ids.push(t); }
    }
  }
  const nameMap = new Map<string, string>();
  for (const m of html.matchAll(/data-mkt_id="([^"]+)"[\s\S]{0,1600}?class="mkt-name">([^<]+)/g)) {
    for (const id of m[1].split(",")) {
      const t = id.trim();
      if (t && !nameMap.has(t)) nameMap.set(t, m[2].trim());
    }
  }
  const titleM    = html.match(/<h1[^>]*>([^<]+)<\/h1>/);
  const eventName = titleM?.[1]?.trim() ?? "";
  return { ids, nameMap, eventName };
}

async function scrapeCodere(url: string): Promise<ScraperResult> {
  const m = url.match(/\/e\/(\d+)\/([^?&#]+)/);
  if (!m) throw new Error("Could not extract event ID from Codere URL");
  const [, evId, evSlug] = m;
  const pageUrl = `https://apuestas.codere.mx/es_MX/e/${evId}/${evSlug}?show_all=Y`;

  const HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
  };

  // ── Step 1: try direct fetch (free) ─────────────────────────────────────────
  let pageHtml = "";
  try {
    const r = await fetch(pageUrl, { headers: HEADERS });
    if (r.ok) pageHtml = await r.text();
  } catch { /* ignore, try Firecrawl */ }

  let { ids, nameMap, eventName } = parseCodereHtml(pageHtml);

  // ── Step 2: Firecrawl fallback if CF blocked us (< 30 IDs) ──────────────────
  if (ids.length < 30) {
    if (!FC_KEY) throw new Error("FIRECRAWL_API_KEY not configured and direct fetch blocked");

    // Return a Promise from the script so Firecrawl awaits it
    const EXTRACT_JS = `
      return fetch(location.href)
        .then(function(r) { return r.text(); })
        .then(function(html) {
          var ids = [], seen = {}, match;
          var mRe = /data-mkt_id="([^"]+)"/g;
          while ((match = mRe.exec(html)) !== null) {
            match[1].split(',').forEach(function(id) {
              id = id.trim();
              if (id && !seen[id]) { seen[id] = true; ids.push(id); }
            });
          }
          var nameMap = {}, nRe = /data-mkt_id="([^"]+)"[\\s\\S]{0,1600}?class="mkt-name">([^<]+)/g;
          while ((match = nRe.exec(html)) !== null) {
            match[1].split(',').forEach(function(id) {
              id = id.trim();
              if (id && !nameMap[id]) nameMap[id] = match[2].trim();
            });
          }
          var titleMatch = html.match(/<h1[^>]*>([^<]+)<\\/h1>/);
          var nameEntries = ids.map(function(id) { return id + '=' + encodeURIComponent(nameMap[id] || ''); }).join('|');
          var span = document.createElement('span');
          span.id = '__codere_ids__';
          span.setAttribute('data-ids', ids.join(','));
          span.setAttribute('data-names', nameEntries);
          span.setAttribute('data-title', encodeURIComponent(titleMatch ? titleMatch[1].trim() : ''));
          document.body.appendChild(span);
        });
    `;

    const fcResp = await fetch("https://api.firecrawl.dev/v1/scrape", {
      method:  "POST",
      headers: { Authorization: `Bearer ${FC_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        url:     pageUrl,
        formats: ["rawHtml"],
        actions: [
          { type: "wait", milliseconds: 3000 },
          { type: "executeJavascript", script: EXTRACT_JS },
          { type: "wait", milliseconds: 3000 },
        ],
      }),
    });
    if (!fcResp.ok) throw new Error(`Firecrawl ${fcResp.status}`);
    const rawHtml = (await fcResp.json()).data.rawHtml as string;

    const spanM = rawHtml.match(/id="__codere_ids__"[^>]*data-ids="([^"]*)"[^>]*data-names="([^"]*)"[^>]*data-title="([^"]*)"/);
    if (!spanM) throw new Error("Codere: market ID extraction failed (Firecrawl span not found)");

    ids = spanM[1].split(",").filter(Boolean);
    nameMap.clear();
    for (const entry of spanM[2].split("|")) {
      const eq = entry.indexOf("=");
      if (eq > 0) {
        const name = decodeURIComponent(entry.slice(eq + 1));
        if (name) nameMap.set(entry.slice(0, eq), name);
      }
    }
    eventName = decodeURIComponent(spanM[3]) || eventName || `Codere ${evId}`;
  }

  if (!ids.length) throw new Error("Codere: no market IDs found");

  // ── Step 3: fetch web_nr for each market (not CF-protected) ─────────────────
  const webNrResults = await Promise.all(
    ids.map(async (id) => {
      try {
        const r = await fetch(
          `https://apuestas.codere.mx/web_nr?key=sportsbook.cms.handlers.get_mkt_content&mkt_id=${id}`,
          { headers: { "User-Agent": "Mozilla/5.0" } }
        );
        if (!r.ok) return { id, html: "" };
        const d = await r.json() as { html?: string };
        return { id, html: d.html ?? "" };
      } catch { return { id, html: "" }; }
    })
  );

  // ── Parse selections from web_nr HTML ────────────────────────────────────────
  const btnRe  = /<button[^>]*>([\s\S]*?)<\/button>/gs;
  const markets: Market[] = [];

  for (const { id: mktId, html } of webNrResults) {
    if (!html) continue;
    const selns: Selection[] = [];
    for (const btn of html.matchAll(btnRe)) {
      const b      = btn[1];
      const selnM  = b.match(/class="seln-name">([^<]+)/);
      const drawM  = b.match(/class="seln-draw-label">([^<]+)/);
      const hcapM  = b.match(/class="seln-hcap">([^<]+)/);
      const priceM = b.match(/class="price us"[^>]*>([^<]+)/);
      if (!priceM) continue;
      const n = selnM ?? drawM;
      if (!n) continue;
      let selection = n[1].trim();
      if (hcapM && selnM) selection += ` (${hcapM[1].trim()})`;
      selns.push({ selection, american: priceM[1].trim() });
    }
    if (selns.length) markets.push({ name: nameMap.get(mktId) ?? `Market_${mktId}`, selections: selns });
  }

  const txt     = buildTxt("Codere", eventName || `Codere ${evId}`, markets);
  const total_s = markets.reduce((n, m) => n + m.selections.length, 0);
  return { status: "ok", txt, stats: `${markets.length} mercados · ${total_s} selecciones` };
}

// ─── Caliente ─────────────────────────────────────────────────────────────────
// Firecrawl — 1 credit, Cloudflare bypass + JS click-all expanders

async function scrapeCaliente(url: string): Promise<ScraperResult> {
  if (!FC_KEY) throw new Error("FIRECRAWL_API_KEY not configured");

  const fcUrl = url.replace(/\?.*/, "") + "?show_all=Y";

  const resp = await fetch("https://api.firecrawl.dev/v1/scrape", {
    method:  "POST",
    headers: { Authorization: `Bearer ${FC_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      url:     fcUrl,
      formats: ["rawHtml"],
      actions: [
        { type: "wait", milliseconds: 5000 },
        { type: "executeJavascript",
          script: "document.querySelectorAll('.expander-button').forEach(b => b.click())" },
        { type: "wait", milliseconds: 12000 },
      ],
    }),
  });
  if (!resp.ok) throw new Error(`Firecrawl ${resp.status}`);
  const html = (await resp.json()).data.rawHtml as string;

  const titleM    = html.match(/<title>([^<]+)<\/title>/);
  const eventName = titleM?.[1]?.trim() ?? "Caliente Event";

  // Locate each .mkt container and parse buttons within it
  const mktRe = /<div class="[^"]*\bmkt\b[^"]*"\s+data-mkt_id="(\d+)"[^>]*>/g;
  const positions: Array<{ id: string; start: number }> = [];
  for (const m of html.matchAll(mktRe)) {
    positions.push({ id: m[1], start: m.index! });
  }

  const btnRe = /<button[^>]*>([\s\S]*?)<\/button>/gs;
  const markets: Market[] = [];

  for (let i = 0; i < positions.length; i++) {
    const { id: mktId, start } = positions[i];
    const end     = positions[i + 1]?.start ?? html.length;
    const content = html.slice(start, end);

    const nameM = content.match(/class="mkt-name">([^<]+)/);
    const name  = nameM?.[1]?.trim() ?? `Market_${mktId}`;

    const selns: Selection[] = [];
    for (const btn of content.matchAll(btnRe)) {
      const b     = btn[1];
      const selnM = b.match(/class="seln-name">([^<]+)/);
      const drawM = b.match(/class="seln-draw-label">([^<]+)/);
      const hcapM = b.match(/class="seln-hcap">([^<]+)/);
      const priceM = b.match(/class="price us"[^>]*>([^<]+)/);
      if (!priceM) continue;
      const n = selnM ?? drawM;
      if (!n) continue;
      let selection = n[1].trim();
      if (hcapM && selnM) selection += ` (${hcapM[1].trim()})`;
      selns.push({ selection, american: priceM[1].trim() });
    }
    if (selns.length) markets.push({ name, selections: selns });
  }

  const txt     = buildTxt("Caliente", eventName, markets);
  const total_s = markets.reduce((n, m) => n + m.selections.length, 0);
  return { status: "ok", txt, stats: `${markets.length} mercados · ${total_s} selecciones` };
}

// ─── 1Win ─────────────────────────────────────────────────────────────────────
// Firecrawl — 1 credit, JS injection extracts markets from SPA DOM
// NOTE: top-parser.com /matches/get has NO odds endpoint (verified 2026-03-18)

async function scrape1Win(url: string): Promise<ScraperResult> {
  if (!FC_KEY) throw new Error("FIRECRAWL_API_KEY not configured");

  const matchM = url.match(/-(\d{7,})(?:[?&/]|$)/);
  if (!matchM) throw new Error("Could not extract matchId from 1Win URL (expected: ...-33470209)");

  // CSS module hashes change on every 1Win deploy — use structure-based extraction instead.
  // Strategy: find containers that hold ≥2 buttons matching the American odds pattern,
  // then locate the nearest title-like child element.
  const EXTRACT_JS = `
    const mkts = [];
    const seen = new WeakSet();

    // Find all buttons whose text ends with an American odds token (+NNN / -NNN)
    const oddsBtns = [...document.querySelectorAll('button')].filter(b => {
      const t = b.textContent.trim().replace(/\\s+/g, ' ');
      return /^.{2,60}[+-]\\d{2,4}$/.test(t);
    });

    for (const btn of oddsBtns) {
      // Walk up looking for the tightest container that holds ≥2 odds buttons
      let el = btn.parentElement;
      for (let depth = 0; depth < 10; depth++) {
        if (!el || seen.has(el)) break;
        const siblings = [...el.querySelectorAll('button')].filter(b => /[+-]\\d{2,4}$/.test(b.textContent.trim()));
        if (siblings.length >= 2) {
          // Check parent holds even more — if so, keep climbing
          const parentSiblings = el.parentElement
            ? [...el.parentElement.querySelectorAll('button')].filter(b => /[+-]\\d{2,4}$/.test(b.textContent.trim()))
            : [];
          if (parentSiblings.length > siblings.length * 2) { el = el.parentElement; continue; }

          // Found the market container — extract title
          const titleEl = [...el.querySelectorAll('*')].find(e =>
            e.children.length === 0 &&
            e.tagName !== 'BUTTON' &&
            e.textContent.trim().length > 2 &&
            e.textContent.trim().length < 80 &&
            !/^[+-]\\d/.test(e.textContent.trim())
          );
          const title = titleEl ? titleEl.textContent.trim() : 'Market';

          const selns = siblings.map(b => {
            const t = b.textContent.trim().replace(/\\s+/g, ' ');
            const m = t.match(/^(.+?)([+-]\\d+)$/);
            return m ? {selection: m[1].trim(), american: m[2]} : null;
          }).filter(Boolean);

          if (selns.length) mkts.push({name: title, selections: selns});
          seen.add(el);
          break;
        }
        el = el.parentElement;
      }
    }

    const div = document.createElement('div');
    div.id = '__bettor_data__';
    div.setAttribute('style', 'display:none');
    div.textContent = JSON.stringify(mkts);
    document.body.appendChild(div);
  `;

  const baseUrl = url.replace(/\?.*/, "");
  const resp = await fetch("https://api.firecrawl.dev/v1/scrape", {
    method:  "POST",
    headers: { Authorization: `Bearer ${FC_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      url:     baseUrl,
      formats: ["rawHtml"],
      actions: [
        { type: "wait", milliseconds: 5000 },
        { type: "scroll", direction: "down", amount: 1500 },
        { type: "wait", milliseconds: 2000 },
        { type: "scroll", direction: "down", amount: 3000 },
        { type: "wait", milliseconds: 2000 },
        { type: "scroll", direction: "down", amount: 6000 },
        { type: "wait", milliseconds: 2000 },
        { type: "executeJavascript", script: EXTRACT_JS },
      ],
    }),
  });
  if (!resp.ok) throw new Error(`Firecrawl ${resp.status}`);
  const html = (await resp.json()).data.rawHtml as string;

  const dataM = html.match(/id="__bettor_data__"[^>]*>(\[[\s\S]*?\])</);
  if (!dataM) throw new Error("1Win: JS extraction failed — page may not have rendered in time");

  const markets: Market[] = JSON.parse(dataM[1]);

  const titleM    = html.match(/<title>([^<]+)<\/title>/);
  const eventName = titleM?.[1]?.trim() ?? `1Win ${matchM[1]}`;

  const txt     = buildTxt("1Win", eventName, markets);
  const total_s = markets.reduce((n, m) => n + m.selections.length, 0);
  return { status: "ok", txt, stats: `${markets.length} mercados · ${total_s} selecciones` };
}

// ─── Scrapers map ─────────────────────────────────────────────────────────────

const SCRAPERS: Record<string, (url: string) => Promise<ScraperResult>> = {
  playdoit: scrapePlayDoit,
  codere:   scrapeCodere,
  caliente: scrapeCaliente,
  "1win":   scrape1Win,
};

// ─── Handler ──────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

  // ── Auth ──────────────────────────────────────────────────────────────────
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return json({ error: "Unauthorized" }, 401);

  const userClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { Authorization: authHeader } },
  });
  const { data: { user }, error: authErr } = await userClient.auth.getUser();
  if (authErr || !user) return json({ error: "Invalid token" }, 401);

  // ── Parse body ────────────────────────────────────────────────────────────
  const body = await req.json().catch(() => ({})) as Record<string, string>;
  const jobs = Object.entries(body).filter(([book, url]) => book in SCRAPERS && url?.trim());

  if (!jobs.length) return json({ error: "No valid URLs provided" }, 400);

  // ── Run scrapers in parallel ───────────────────────────────────────────────
  const results = await Promise.all(
    jobs.map(async ([book, url]) => {
      try {
        const result = await SCRAPERS[book](url.trim());
        return [book, result] as const;
      } catch (err) {
        return [book, { status: "error" as const, error: String(err) }] as const;
      }
    })
  );

  return json(Object.fromEntries(results));
});
