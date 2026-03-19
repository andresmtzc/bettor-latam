/* ============================================
   cheatsheet.js — odds fetch + render
   Runs after app.js confirms auth
   ============================================ */

const ODDS_PROXY_URL = `${SUPABASE_URL}/functions/v1/odds-proxy`;

// ---- State ----
let activeSport  = 'nba';
let activeFilter = 'all';
let sortField    = 'edge';
let sortDir      = 'desc';
let propsData    = [];

// ---- Init (called by app.js after auth confirmed) ----
async function initCheatsheet() {
  setDateHeader();
  selectSport('nba');
}

function setDateHeader() {
  const el = document.getElementById('cheatsheet-date');
  if (!el) return;
  const now = new Date();
  const opts = { weekday: 'long', day: 'numeric', month: 'short' };
  el.textContent = `Hoy, ${now.toLocaleDateString('es-MX', opts)}`;
}

// ---- Sport selection ----
function selectSport(sport) {
  activeSport  = sport;
  activeFilter = 'all';

  // Update tabs
  document.querySelectorAll('.sport-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sport === sport);
  });

  // Reset filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === 'all');
  });

  loadProps();
}

// ---- Filter ----
function setFilter(filter) {
  activeFilter = filter;

  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });

  renderProps(propsData);
}

// ---- Sort ----
function sortBy(field) {
  if (sortField === field) {
    sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sortField = field;
    sortDir   = 'desc';
  }
  renderProps(propsData);
}

// ---- Load props from edge function ----
async function loadProps() {
  showState('loading');

  // Check subscription first
  const hasSub = await checkSubscription(activeSport);
  if (!hasSub) {
    showPaywall(activeSport);
    return;
  }

  hidePaywall();

  try {
    const headers = await getAuthHeaders();
    if (!headers) {
      window.location.href = 'login.html';
      return;
    }

    const res = await fetch(`${ODDS_PROXY_URL}?league=${activeSport}`, { headers });

    if (res.status === 401) { window.location.href = 'login.html'; return; }
    if (res.status === 403) { showPaywall(activeSport); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const json = await res.json();

    // Edge function returns { stale: bool, data: [...] }
    if (json.stale) {
      showToast('Mostrando datos anteriores — reintentando pronto');
    }

    propsData = json.data || [];
    renderProps(propsData);

  } catch (err) {
    showState('error');
    const errEl = document.getElementById('error-msg');
    if (errEl) errEl.textContent = err.message;
  }
}

// ---- Check subscription ----
async function checkSubscription(sport) {
  try {
    const { data, error } = await supabase
      .from('subscriptions')
      .select('id')
      .eq('league', sport)
      .eq('status', 'active')
      .gt('current_period_end', new Date().toISOString())
      .maybeSingle();

    if (error) throw error;
    return !!data;
  } catch {
    return false;
  }
}

// ---- Paywall ----
function showPaywall(sport) {
  showState('empty');
  const banner = document.getElementById('paywall-banner');
  if (!banner) return;
  banner.style.display = 'block';

  const labels = { nba: 'NBA', nfl: 'NFL', soccer: 'Fútbol' };
  const msgEl = document.getElementById('paywall-msg');
  if (msgEl) msgEl.textContent = `Desbloquea ${labels[sport] || 'esta liga'} — $99 MXN/mes`;

  // Show sticky CTA on mobile
  const cta = document.getElementById('sticky-cta');
  if (cta) cta.classList.add('visible');
}

function hidePaywall() {
  const banner = document.getElementById('paywall-banner');
  if (banner) banner.style.display = 'none';
  const cta = document.getElementById('sticky-cta');
  if (cta) cta.classList.remove('visible');
}

// Expose for inline button
window.handleSubscribe = function(league) {
  handleSubscribe(league || activeSport);
};

// ---- Render ----
function renderProps(raw) {
  if (!raw || raw.length === 0) {
    showState('empty');
    return;
  }

  let props = computeEdge(raw);
  props = filterProps(props);
  props = sortProps(props);

  if (props.length === 0) {
    showState('empty');
    return;
  }

  renderTable(props);
  renderCards(props);
  showState('data');
}

// ---- Edge computation ----
// The Odds API returns bookmaker odds — we compute implied probability
// and compare to consensus to find edge.
function computeEdge(oddsData) {
  const results = [];

  for (const event of oddsData) {
    for (const market of (event.bookmakers?.[0]?.markets || [])) {
      for (const outcome of market.outcomes) {
        const price  = outcome.price; // American odds (e.g. -115)
        const point  = outcome.point; // Line (e.g. 27.5)
        const edge   = calcEdge(oddsData, market.key, outcome.name, point, outcome.description);

        results.push({
          player:     outcome.description || outcome.name,
          team:       extractTeam(event, outcome),
          sport:      activeSport,
          prop:       marketLabel(market.key),
          line:       point,
          edge:       edge,
          odds:       price,
          direction:  outcome.name, // 'Over' or 'Under'
          hot:        edge >= 10,
        });
      }
    }
  }

  return results;
}

// Compare one bookmaker's implied prob to the consensus across all books
function calcEdge(oddsData, marketKey, direction, point, player) {
  const allPrices = [];

  for (const event of oddsData) {
    for (const book of (event.bookmakers || [])) {
      const market = book.markets?.find(m => m.key === marketKey);
      if (!market) continue;
      const outcome = market.outcomes?.find(
        o => o.name === direction && o.description === player && o.point === point
      );
      if (outcome?.price != null) allPrices.push(outcome.price);
    }
  }

  if (allPrices.length < 2) return 0;

  const implied = americanToImplied(allPrices[0]);
  const consensus = allPrices.slice(1).reduce((sum, p) => sum + americanToImplied(p), 0) / (allPrices.length - 1);

  return Math.round((implied - consensus) * 100);
}

function americanToImplied(american) {
  if (american > 0) return 100 / (american + 100);
  return Math.abs(american) / (Math.abs(american) + 100);
}

function marketLabel(key) {
  const map = {
    'player_points':      'Puntos',
    'player_rebounds':    'Rebotes',
    'player_assists':     'Asistencias',
    'player_threes':      'Triples',
    'player_blocks':      'Bloqueos',
    'player_steals':      'Robos',
    'player_pass_tds':    'TDs pase',
    'player_rush_yards':  'Yardas carrera',
    'player_reception_yards': 'Yardas recepción',
    'player_receptions':  'Recepciones',
    'player_anytime_td':  'Anytime TD',
  };
  return map[key] || key;
}

function extractTeam(event, outcome) {
  // Try to infer team from event home/away teams
  const home = event.home_team || '';
  const away = event.away_team || '';
  const name = outcome.description || '';
  // Simple heuristic — will be replaced by players table lookup
  return home || away || '—';
}

// ---- Filtering ----
function filterProps(props) {
  if (activeFilter === 'hot') return props.filter(p => p.hot);
  if (activeFilter === 'pos') return props.filter(p => p.edge > 0);
  return props;
}

// ---- Sorting ----
function sortProps(props) {
  return [...props].sort((a, b) => {
    const mult = sortDir === 'desc' ? -1 : 1;
    if (sortField === 'edge') return mult * (a.edge - b.edge);
    if (sortField === 'line') return mult * ((a.line ?? 0) - (b.line ?? 0));
    return 0;
  });
}

// ---- Table render (desktop) ----
function renderTable(props) {
  const tbody = document.getElementById('prop-table-body');
  if (!tbody) return;

  tbody.innerHTML = props.map(p => `
    <tr>
      <td>
        <div class="player-cell">
          <div class="player-avatar">
            <img
              src="${playerAvatarUrl(p)}"
              alt="${esc(p.player)}"
              onerror="this.src='${teamLogoUrl(p)}'; this.onerror=function(){this.parentElement.textContent='${sportIcon(p.sport)}'}"
            />
          </div>
          <div>
            <div class="player-name">${esc(p.player)}</div>
            <div class="player-meta">${esc(p.team)} · ${p.sport.toUpperCase()}</div>
          </div>
        </div>
      </td>
      <td style="color: var(--color-text-muted);">${esc(p.prop)}</td>
      <td class="num">${p.line != null ? p.line : '—'}</td>
      <td>${edgeBadge(p)}</td>
      <td class="num" style="color: var(--color-text-muted);">${p.direction} ${formatOdds(p.odds)}</td>
    </tr>
  `).join('');
}

// ---- Mobile cards render ----
function renderCards(props) {
  const container = document.getElementById('prop-cards');
  if (!container) return;

  container.innerHTML = props.map(p => `
    <div class="prop-card">
      <div class="player-avatar">
        <img
          src="${playerAvatarUrl(p)}"
          alt="${esc(p.player)}"
          onerror="this.src='${teamLogoUrl(p)}'; this.onerror=function(){this.parentElement.textContent='${sportIcon(p.sport)}'}"
        />
      </div>
      <div class="prop-card-main">
        <div class="prop-card-row">
          <span class="prop-card-name">${esc(p.player)}</span>
          ${edgeBadge(p)}
        </div>
        <div class="prop-card-meta">${esc(p.prop)} · ${p.direction} ${p.line != null ? p.line : '—'}</div>
        <div class="prop-card-nums">
          <span>${esc(p.team)}</span>
          <span>${formatOdds(p.odds)}</span>
        </div>
      </div>
    </div>
  `).join('');
}

// ---- Edge badge HTML ----
function edgeBadge(p) {
  if (p.hot)        return `<span class="edge-badge edge-hot">🔥 +${p.edge}%</span>`;
  if (p.edge > 0)   return `<span class="edge-badge edge-pos">▲ +${p.edge}%</span>`;
  if (p.edge < 0)   return `<span class="edge-badge edge-neg">▼ ${p.edge}%</span>`;
  return `<span class="edge-badge edge-neutral">—</span>`;
}

// ---- Image URLs ----
// These use the players table (fetched from Supabase) for ID lookup.
// Falls back gracefully if no match.
let playerCache = {};

async function preloadPlayers(names) {
  const missing = names.filter(n => !playerCache[n]);
  if (!missing.length) return;

  try {
    const { data } = await supabase
      .from('players')
      .select('name, nba_id, espn_id, team_abbrev, sport')
      .in('name', missing);

    for (const row of (data || [])) {
      playerCache[row.name] = row;
    }
  } catch {
    // Non-fatal — fallback chain handles missing players
  }
}

function playerAvatarUrl(p) {
  const info = playerCache[p.player];
  if (!info) return '';
  if (info.nba_id)  return `https://cdn.nba.com/headshots/nba/latest/1040x760/${info.nba_id}.png`;
  if (info.espn_id) return `https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/${info.espn_id}.png`;
  return teamLogoUrl(p);
}

function teamLogoUrl(p) {
  const info = playerCache[p.player];
  const abbrev = info?.team_abbrev;
  if (!abbrev) return '';
  if (p.sport === 'nba') return `https://a.espncdn.com/i/teamlogos/nba/500/${abbrev}.png`;
  if (p.sport === 'nfl') return `https://a.espncdn.com/i/teamlogos/nfl/500/${abbrev}.png`;
  return '';
}

function sportIcon(sport) {
  if (sport === 'nba')    return '🏀';
  if (sport === 'nfl')    return '🏈';
  if (sport === 'soccer') return '⚽';
  return '🎯';
}

// ---- Utilities ----
function formatOdds(american) {
  if (american == null) return '—';
  return american > 0 ? `+${american}` : `${american}`;
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- UI state machine ----
function showState(state) {
  document.getElementById('loading-state').style.display = state === 'loading' ? 'block' : 'none';
  document.getElementById('table-wrap').style.display    = state === 'data'    ? 'block' : 'none';
  document.getElementById('prop-cards').style.display   = state === 'data'    ? 'flex'  : 'none';
  document.getElementById('empty-state').style.display  = state === 'empty'   ? 'block' : 'none';
  document.getElementById('error-state').style.display  = state === 'error'   ? 'block' : 'none';
}
