/**
 * stripe-webhook — Supabase Edge Function (Deno)
 *
 * Flow:
 *   1. Verify Stripe signature (reject immediately on failure)
 *   2. Handle relevant event types
 *   3. Return 200 always so Stripe doesn't retry valid events
 *
 * Events handled:
 *   - checkout.session.completed     → upsert active subscription
 *   - customer.subscription.updated  → sync status + period end
 *   - customer.subscription.deleted  → mark cancelled
 *   - invoice.payment_failed         → log (optionally notify user)
 */

import Stripe from 'npm:stripe@14';
import { createClient } from 'npm:@supabase/supabase-js@2';

const stripe = new Stripe(Deno.env.get('STRIPE_SECRET_KEY')!, {
  apiVersion: '2024-04-10',
});

const SUPABASE_URL         = Deno.env.get('SUPABASE_URL')!;
const SUPABASE_SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const WEBHOOK_SECRET       = Deno.env.get('STRIPE_WEBHOOK_SECRET')!;

// Stripe price ID → league mapping
// Populate after creating Stripe products
const PRICE_TO_LEAGUE: Record<string, string> = {
  [Deno.env.get('STRIPE_PRICE_NBA')    ?? '']:    'nba',
  [Deno.env.get('STRIPE_PRICE_NFL')    ?? '']:    'nfl',
  [Deno.env.get('STRIPE_PRICE_SOCCER') ?? '']:    'soccer',
  [Deno.env.get('STRIPE_PRICE_BUNDLE') ?? '']:    'bundle',
};

const CORS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Headers': 'stripe-signature, content-type',
};

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: CORS });
  }

  // ---- 1. Verify Stripe signature ----
  const body      = await req.text();
  const signature = req.headers.get('stripe-signature');

  if (!signature) {
    return new Response('Missing signature', { status: 400 });
  }

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(body, signature, WEBHOOK_SECRET);
  } catch (err) {
    console.error('Webhook signature verification failed:', err);
    return new Response('Invalid signature', { status: 400 });
  }

  const db = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

  // ---- 2. Handle events ----
  try {
    switch (event.type) {

      case 'checkout.session.completed': {
        const session = event.data.object as Stripe.Checkout.Session;
        await handleCheckoutCompleted(db, session);
        break;
      }

      case 'customer.subscription.updated': {
        const sub = event.data.object as Stripe.Subscription;
        await handleSubscriptionUpdated(db, sub);
        break;
      }

      case 'customer.subscription.deleted': {
        const sub = event.data.object as Stripe.Subscription;
        await handleSubscriptionDeleted(db, sub);
        break;
      }

      case 'invoice.payment_failed': {
        const invoice = event.data.object as Stripe.Invoice;
        console.warn('Payment failed for subscription:', invoice.subscription);
        // TODO: send notification email to user via Supabase Edge Function / Resend
        break;
      }

      default:
        // Acknowledge but ignore unhandled events
        console.log('Unhandled event type:', event.type);
    }
  } catch (err) {
    // Log but still return 200 — Stripe will retry if we return non-200
    // Log the error and let ops investigate
    console.error(`Error handling event ${event.type}:`, err);
  }

  // ---- 3. Always 200 ----
  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
});

// ---- checkout.session.completed ----
// Fired when a user completes Stripe Checkout.
// session.metadata.user_id is set by create-checkout edge function.
async function handleCheckoutCompleted(
  db: ReturnType<typeof createClient>,
  session: Stripe.Checkout.Session,
) {
  const userId = session.metadata?.user_id;
  if (!userId) {
    console.error('checkout.session.completed: missing user_id in metadata');
    return;
  }

  // Get the subscription from Stripe to get line item details
  const stripeSubId = session.subscription as string;
  const stripeSub   = await stripe.subscriptions.retrieve(stripeSubId);

  const priceId = stripeSub.items.data[0]?.price.id;
  const league  = priceId ? PRICE_TO_LEAGUE[priceId] : null;

  if (!league) {
    console.error('checkout.session.completed: unknown price_id', priceId);
    return;
  }

  // If bundle, upsert all three leagues
  const leagues = league === 'bundle' ? ['nba', 'nfl', 'soccer'] : [league];

  for (const l of leagues) {
    const { error } = await db.from('subscriptions').upsert(
      {
        user_id:                userId,
        league:                 l,
        status:                 'active',
        stripe_subscription_id: stripeSubId,
        stripe_customer_id:     session.customer as string,
        current_period_end:     new Date(stripeSub.current_period_end * 1000).toISOString(),
      },
      { onConflict: 'stripe_subscription_id' }
    );

    if (error) console.error('upsert subscription error:', error);
  }
}

// ---- customer.subscription.updated ----
async function handleSubscriptionUpdated(
  db: ReturnType<typeof createClient>,
  sub: Stripe.Subscription,
) {
  const status = sub.status === 'active' ? 'active'
    : sub.status === 'past_due'          ? 'past_due'
    : 'cancelled';

  const { error } = await db
    .from('subscriptions')
    .update({
      status,
      current_period_end: new Date(sub.current_period_end * 1000).toISOString(),
    })
    .eq('stripe_subscription_id', sub.id);

  if (error) console.error('subscription.updated error:', error);
}

// ---- customer.subscription.deleted ----
async function handleSubscriptionDeleted(
  db: ReturnType<typeof createClient>,
  sub: Stripe.Subscription,
) {
  const { error } = await db
    .from('subscriptions')
    .update({ status: 'cancelled' })
    .eq('stripe_subscription_id', sub.id);

  if (error) console.error('subscription.deleted error:', error);
}
