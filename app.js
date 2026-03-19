/* ============================================
   app.js — Supabase client + auth logic
   Shared across all pages
   ============================================ */

// ---- Config (replace with your Supabase project values) ----
const SUPABASE_URL    = 'https://YOUR_PROJECT.supabase.co';
const SUPABASE_ANON   = 'YOUR_ANON_KEY';
const STRIPE_PRICE_IDS = {
  nba:    'price_NBA_ID',
  nfl:    'price_NFL_ID',
  soccer: 'price_SOCCER_ID',
  bundle: 'price_BUNDLE_ID',
};

// ---- Supabase client ----
// Loaded via CDN in each HTML file (added below via script tag injection)
let supabase;

(function initSupabase() {
  const script = document.createElement('script');
  script.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js';
  script.onload = () => {
    supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON);
    onSupabaseReady();
  };
  document.head.appendChild(script);
})();

// ---- Auth state ----
let currentUser = null;
let currentSession = null;

async function onSupabaseReady() {
  // Get current session
  const { data: { session } } = await supabase.auth.getSession();
  currentSession = session;
  currentUser    = session?.user ?? null;

  // Listen for auth changes
  supabase.auth.onAuthStateChange((_event, session) => {
    currentSession = session;
    currentUser    = session?.user ?? null;
  });

  // Page-specific auth guards
  const page = getCurrentPage();

  if (page === 'cheatsheet') {
    if (!currentUser) {
      window.location.href = 'login.html';
      return;
    }
    // cheatsheet.js will init itself after this
    if (typeof initCheatsheet === 'function') initCheatsheet();
  }

  if (page === 'login' && currentUser) {
    window.location.href = 'cheatsheet.html';
  }

  // Show user email in nav if present
  const emailEl = document.getElementById('user-email');
  if (emailEl && currentUser) {
    emailEl.textContent = currentUser.email;
  }
}

function getCurrentPage() {
  const path = window.location.pathname;
  if (path.includes('cheatsheet')) return 'cheatsheet';
  if (path.includes('login'))      return 'login';
  return 'landing';
}

// ---- Sign out ----
async function handleSignout() {
  await supabase.auth.signOut();
  window.location.href = 'index.html';
}

// ---- Stripe checkout ----
async function handleSubscribe(league) {
  if (!currentUser) {
    window.location.href = 'login.html';
    return;
  }

  const priceId = league ? STRIPE_PRICE_IDS[league] : STRIPE_PRICE_IDS.bundle;

  try {
    // Call Supabase edge function to create Stripe checkout session
    const { data, error } = await supabase.functions.invoke('create-checkout', {
      body: {
        price_id:    priceId,
        success_url: window.location.origin + '/cheatsheet.html?subscribed=1',
        cancel_url:  window.location.origin + '/cheatsheet.html',
      },
    });
    if (error) throw error;
    window.location.href = data.url;
  } catch (err) {
    showToast('Error al abrir el pago. Intenta de nuevo.', 'error');
    console.error('Stripe checkout error:', err);
  }
}

// ---- Toast helper ----
function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast show' + (type ? ' ' + type : '');
  setTimeout(() => { el.className = 'toast'; }, 3500);
}

// ---- Get auth headers for edge function calls ----
async function getAuthHeaders() {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) return null;
  return { Authorization: `Bearer ${session.access_token}` };
}
