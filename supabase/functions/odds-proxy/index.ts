/**
 * odds-proxy — Supabase Edge Function (Deno)
 *
 * Flow:
 *   1. Verify JWT
 *   2. Check active subscription for the requested league
 *   3. Return cached odds if fresh
 *   4. Fetch from The Odds API, cache, return
 *   5. On API failure: return last stale cache with flag
 */

import { createClient } from 'npm:@supabase/supabase-js@2';

const ODDS_API_KEY = Deno.env.get('ODDS_API_KEY')!;
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SUPABASE_SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;

// Cache TTL in minutes
const CACHE_TTL: Record<string, number> = {
  nba:    15,
  nfl:    15,
  soccer: 30,
};

// The Odds API sport keys
const SPORT_KEYS: Record<string, string> = {
  nba:    'basketball_nba',
  nfl:    'americanfootball_nfl',
  soccer: 'soccer_usa_mls', // default; expand for Liga MX in v2
};

const CORS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Headers': 'authorization, content-type',
};

Deno.serve(async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: CORS });
  }

  // ---- 1. Verify JWT ----
  const authHeader = req.headers.get('Authorization');
  if (!authHeader?.startsWith('Bearer ')) {
    return json({ error: 'Unauthorized' }, 401);
  }

  const jwt = authHeader.slice(7);

  // Create user-scoped client (respects RLS)
  const userClient = createClient(SUPABASE_URL, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: authHeader } },
  });

  const { data: { user }, error: authErr } = await userClient.auth.getUser(jwt);
  if (authErr || !user) {
    return json({ error: 'Invalid token' }, 401);
  }

  // ---- 2. Check subscription ----
  const url    = new URL(req.url);
  const league = url.searchParams.get('league');

  if (!league || !SPORT_KEYS[league]) {
    return json({ error: 'Invalid league. Use: nba | nfl | soccer' }, 400);
  }

  // Service client for cache reads/writes and subscription checks
  const serviceClient = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

  const { data: sub } = await serviceClient
    .from('subscriptions')
    .select('id')
    .eq('user_id', user.id)
    .eq('status', 'active')
    .gt('current_period_end', new Date().toISOString())
    .or(`league.eq.${league},league.eq.bundle`)
    .maybeSingle();

  if (!sub) {
    return json({ error: 'No active subscription for this league' }, 403);
  }

  // ---- 3. Check cache ----
  const marketKey = `player_props`;
  const { data: cached } = await serviceClient
    .from('cached_odds')
    .select('data, expires_at, fetched_at')
    .eq('league', league)
    .eq('market', marketKey)
    .gt('expires_at', new Date().toISOString())
    .maybeSingle();

  if (cached) {
    return json({ stale: false, source: 'cache', data: cached.data });
  }

  // ---- 4. Fetch from The Odds API ----
  const ttlMinutes = CACHE_TTL[league] ?? 15;
  const sportKey   = SPORT_KEYS[league];
  const oddsUrl    = `https://api.the-odds-api.com/v4/sports/${sportKey}/odds/?` +
    new URLSearchParams({
      apiKey:     ODDS_API_KEY,
      regions:    'us',
      markets:    'player_points,player_rebounds,player_assists',
      oddsFormat: 'american',
    });

  try {
    const oddsRes = await fetch(oddsUrl, { signal: AbortSignal.timeout(8000) });

    if (!oddsRes.ok) {
      throw new Error(`Odds API ${oddsRes.status}`);
    }

    const freshData = await oddsRes.json();
    const expiresAt = new Date(Date.now() + ttlMinutes * 60 * 1000).toISOString();

    // Upsert into cache
    await serviceClient
      .from('cached_odds')
      .upsert(
        { league, market: marketKey, data: freshData, expires_at: expiresAt },
        { onConflict: 'league,market' }
      );

    return json({ stale: false, source: 'api', data: freshData });

  } catch (err) {
    // ---- 5. API failure — return stale cache if available ----
    const { data: stale } = await serviceClient
      .from('cached_odds')
      .select('data')
      .eq('league', league)
      .eq('market', marketKey)
      .order('fetched_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (stale) {
      return json({ stale: true, source: 'stale_cache', data: stale.data });
    }

    console.error('odds-proxy error:', err);
    return json({ error: 'Sin datos disponibles ahora mismo. Intenta más tarde.' }, 503);
  }
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}
