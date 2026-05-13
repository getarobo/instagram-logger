// Service worker — phase machine + tab orchestration (E3+E4).
// Plan §4.2 phase machine, §4.4 discovery, §4.5 enrichment, §4.6 offscreen media worker.
// Consensus Δ4 tab ownership.
// NEVER chrome.tabs.query against IG host pattern — use extension_owned_tabs map only.

import { api } from './lib/api';
import { getStorage, setStorage, initStorageDefaults } from './lib/storage';
import { uniform, sleep } from './lib/jitter';
import type { Phase, OwnedTab } from './lib/types';

const OFFSCREEN_URL = chrome.runtime.getURL('src/offscreen.html');

const ALARM_HEARTBEAT = 'heartbeat';
const ALARM_WATCH_PEEK = 'watch_peek';
const IG_BASE = 'https://www.instagram.com';

// ---------------------------------------------------------------------------
// R6 heat-detection: per-burst in-memory state
// NOTE: in-memory — resets on SW eviction. Acceptable for E5; comment per spec.
// ---------------------------------------------------------------------------

interface BurstState {
  start_at: string;
  hydration_times: number[]; // ms from content_ready to post_outcome (minus dwell)
  http_fail_count: number;   // non-2xx/304 media fetch responses
  http_total_count: number;  // total media fetch responses
  login_redirects: number;
}

let currentBurst: BurstState | null = null;

/** Timestamp (ms) of when the current post started loading, for hydration timing. */
let currentPostLoadedAt: number | null = null;

// ---------------------------------------------------------------------------
// Install / startup
// ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === 'install') {
    await initStorageDefaults();
  }
  chrome.alarms.create(ALARM_HEARTBEAT, { periodInMinutes: 5 });
});

// Recreate alarm on SW startup (SW can be evicted and restarted).
chrome.alarms.create(ALARM_HEARTBEAT, {
  delayInMinutes: 0.1,
  periodInMinutes: 5,
});

// On SW startup, validate owned tabs (consensus Δ4).
validateOwnedTabs().catch((e) =>
  console.warn('[instagram-logger] BG: tab validation error on startup:', e),
);

// ---------------------------------------------------------------------------
// Alarm handler
// ---------------------------------------------------------------------------

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === ALARM_HEARTBEAT) {
    await handleHeartbeatAlarm();
  } else if (alarm.name === ALARM_POST_TIMEOUT) {
    await handlePostTimeoutAlarm();
  } else if (alarm.name === ALARM_WATCH_PEEK) {
    await handleWatchPeekAlarm();
  }
});

async function handleHeartbeatAlarm(): Promise<void> {
  try {
    const state = await api.getState();
    const { phase } = await getStorage(['phase']);

    // Adopt backend's suggestion when idle
    if (phase === 'idle' && state.phase_suggestion !== 'idle') {
      await setStorage({ phase: state.phase_suggestion });
    }
  } catch (e) {
    console.warn('[instagram-logger] BG: heartbeat poll failed:', e);
  }
}

async function handlePostTimeoutAlarm(): Promise<void> {
  const shortcode = currentEnrichmentShortcode;
  if (!shortcode) return;

  console.warn('[instagram-logger] BG: post_outcome timeout for shortcode:', shortcode);

  const retries = (postRetryCount.get(shortcode) ?? 0) + 1;
  postRetryCount.set(shortcode, retries);

  // Close the post-detail tab
  const tabs = await ownedTabsForRole('post-detail');
  for (const tabId of tabs) await closeOwnedTab(tabId);
  currentEnrichmentShortcode = null;

  if (retries < MAX_POST_RETRIES) {
    // Retry same shortcode
    console.log(`[instagram-logger] BG: retrying shortcode ${shortcode} (attempt ${retries + 1}/${MAX_POST_RETRIES})`);
    // Re-open the tab directly for this shortcode
    await ensureOffscreenDocument();
    currentEnrichmentShortcode = shortcode;
    chrome.alarms.create(ALARM_POST_TIMEOUT, { delayInMinutes: 1 });
    const url = `${IG_BASE}/p/${shortcode}/`;
    await openOwnedTab(url, 'post-detail');
  } else {
    // Exhausted retries — skip to next target (backend retry tracking handles re-queuing)
    console.warn(`[instagram-logger] BG: shortcode ${shortcode} exhausted ${MAX_POST_RETRIES} retries, skipping`);
    postRetryCount.delete(shortcode);
    await startEnrichment();
  }
}

// ---------------------------------------------------------------------------
// Watch phase loop (plan §4.2 / E5)
// ---------------------------------------------------------------------------

/** Schedule next watch_peek alarm with jittered 12-24h delay. */
function scheduleWatchPeek(): void {
  // uniform(12*60, 24*60) minutes = 720..1440 min; chrome.alarms requires integer minutes
  const delayInMinutes = Math.round(uniform(12 * 60, 24 * 60));
  chrome.alarms.create(ALARM_WATCH_PEEK, { delayInMinutes });
  console.log(`[instagram-logger] BG: watch_peek scheduled in ${delayInMinutes} min`);
}

/** Enter watch phase: set phase + schedule first peek (idempotent). */
async function enterWatchPhase(): Promise<void> {
  await setPhase('watch');
  const existing = await chrome.alarms.get(ALARM_WATCH_PEEK);
  if (existing) {
    console.log('[instagram-logger] BG: watch_peek alarm already scheduled, skipping');
    return;
  }
  scheduleWatchPeek();
  console.log('[instagram-logger] BG: entered watch phase');
}

/** Handle the watch_peek alarm: open saved-grid tab, send watch_peek capture. */
async function handleWatchPeekAlarm(): Promise<void> {
  const phase = await getPhase();
  if (phase !== 'watch') {
    // Phase changed away from watch — don't run peek
    console.log(`[instagram-logger] BG: watch_peek alarm fired but phase=${phase}, skipping`);
    return;
  }

  const igUser = await getIgUsername();
  if (!igUser) {
    console.warn('[instagram-logger] BG: watch_peek: ig_username not set, skipping');
    scheduleWatchPeek();
    return;
  }

  const url = `${IG_BASE}/${igUser}/saved/all-posts/`;

  // Open or reuse saved-grid tab
  const existingTabs = await ownedTabsForRole('saved-grid');
  let tabId: number;
  if (existingTabs.length > 0) {
    tabId = existingTabs[0];
    await navigateOwnedTab(tabId, url);
  } else {
    tabId = await openOwnedTab(url, 'saved-grid');
  }

  console.log('[instagram-logger] BG: watch_peek tab opened, awaiting capture_done');
  // The content script will send content_ready → background replies start_capture with mode='watch_peek'
  // capture_done is handled in dispatchMessage('capture_done') → closes tab + re-schedules
}

// ---------------------------------------------------------------------------
// auth_logged_out handler (plan §4.7 / E5)
// ---------------------------------------------------------------------------

async function handleAuthLoggedOut(evidence: string): Promise<void> {
  console.warn('[instagram-logger] BG: auth_logged_out, evidence=', evidence);

  // POST heartbeat to backend
  try {
    await api.heartbeat({ state: 'logged_out', last_error: evidence });
  } catch (e) {
    console.error('[instagram-logger] BG: auth_logged_out heartbeat POST failed:', e);
  }

  // Transition phase
  await setPhase('logged_out');

  // Clear active alarms
  chrome.alarms.clear(ALARM_WATCH_PEEK);
  chrome.alarms.clear(ALARM_POST_TIMEOUT);
  currentEnrichmentShortcode = null;

  // Close all owned tabs gracefully
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  for (const idStr of Object.keys(extension_owned_tabs)) {
    await closeOwnedTab(Number(idStr));
  }
}

// ---------------------------------------------------------------------------
// R6 heat-detection: burst metrics helpers (plan §4.3 / consensus R6)
// ---------------------------------------------------------------------------

/** p50 (median) of a numeric array. Returns 0 for empty arrays. */
function median(arr: number[]): number {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

/** Initialize burst state at the start of an enrichment phase cycle. */
function initBurstState(): void {
  currentBurst = {
    start_at: new Date().toISOString(),
    hydration_times: [],
    http_fail_count: 0,
    http_total_count: 0,
    login_redirects: 0,
  };
  currentPostLoadedAt = Date.now();
}

/** Record HTTP response from offscreen media fetch. */
function recordHttpResponse(status: number): void {
  if (!currentBurst) return;
  currentBurst.http_total_count++;
  // Non-2xx/304 → fail; status 0 = CORS blocked
  if (status === 0 || (status >= 400 && status < 600)) {
    currentBurst.http_fail_count++;
  }
}

/** Record hydration time when post_outcome arrives. */
function recordHydrationTime(): void {
  if (!currentBurst || currentPostLoadedAt === null) return;
  const elapsed = Date.now() - currentPostLoadedAt;
  currentBurst.hydration_times.push(elapsed);
  // Reset for next post
  currentPostLoadedAt = Date.now();
}

interface BurstMetricRecord {
  burst_id: string;
  closed_at: string;
  hydration_p50_ms: number;
  http_4xx_rate: number;
  login_redirects: number;
  posts_seen: number;
  media_uploaded: number;
}

/** Close the current burst, persist metrics, and evaluate R6 triggers. */
async function closeBurst(postsSeenCount: number, mediaUploadedCount: number): Promise<void> {
  if (!currentBurst) return;

  const end_at = new Date().toISOString();
  const hydration_p50_ms = median(currentBurst.hydration_times);
  const http_4xx_rate =
    currentBurst.http_total_count > 0
      ? currentBurst.http_fail_count / currentBurst.http_total_count
      : 0;

  const metric: BurstMetricRecord = {
    burst_id: `burst_${Date.now()}`,
    closed_at: end_at,
    hydration_p50_ms,
    http_4xx_rate,
    login_redirects: currentBurst.login_redirects,
    posts_seen: postsSeenCount,
    media_uploaded: mediaUploadedCount,
  };

  currentBurst = null;

  await recordBurstMetric(metric);
}

/** Append metric, trim to 7, evaluate R6 triggers. */
async function recordBurstMetric(metric: BurstMetricRecord): Promise<void> {
  const { burst_metrics, test_mode_skip_warmup } = await getStorage(['burst_metrics', 'test_mode_skip_warmup']);

  burst_metrics.push(metric as import('./lib/types').BurstMetrics);
  // Keep only last 7
  if (burst_metrics.length > 7) {
    burst_metrics.splice(0, burst_metrics.length - 7);
  }

  await setStorage({ burst_metrics });

  // Evaluate R6 triggers
  const skipWarmup = test_mode_skip_warmup;
  const minBursts = 4;

  // Need at least 4 bursts (or test_mode bypass) to have a baseline
  if (burst_metrics.length < minBursts && !skipWarmup) {
    console.log(`[instagram-logger] BG: R6 evaluation skipped (need ${minBursts} bursts, have ${burst_metrics.length})`);
    return;
  }

  // Baseline: mean of prior bursts (all except the just-appended last one)
  const history = burst_metrics.slice(0, -1);
  const baseline = computeBaseline(history);

  const { hydration_p50_ms, http_4xx_rate, login_redirects } = metric;

  // R6 trigger conditions
  const trigger_a = hydration_p50_ms > 1.5 * baseline.hydration_p50_ms && baseline.hydration_p50_ms > 0;
  const trigger_b = http_4xx_rate > 0.05 && baseline.http_4xx_rate < 0.02;
  const trigger_c = login_redirects > 0;

  if (trigger_a || trigger_b || trigger_c) {
    console.warn('[instagram-logger] BG: R6 heat-detection triggered!', { trigger_a, trigger_b, trigger_c, metric, baseline });

    const metricsPayload = { hydration_p50_ms, http_4xx_rate, login_redirects };

    try {
      await api.heartbeat({ state: 'throttling_suspected', metrics: metricsPayload });
    } catch (e) {
      console.error('[instagram-logger] BG: R6 heartbeat POST failed:', e);
    }

    await setPhase('throttling_suspected');

    // Clear all alarms
    chrome.alarms.clear(ALARM_WATCH_PEEK);
    chrome.alarms.clear(ALARM_POST_TIMEOUT);
    currentEnrichmentShortcode = null;
  }
}

interface BaselineMetrics {
  hydration_p50_ms: number;
  http_4xx_rate: number;
  login_redirects: number;
}

/** Compute mean of metrics from a history slice. */
function computeBaseline(history: BurstMetricRecord[]): BaselineMetrics {
  if (history.length === 0) {
    // test_mode_skip_warmup: assume zero baseline
    return { hydration_p50_ms: 0, http_4xx_rate: 0, login_redirects: 0 };
  }
  const n = history.length;
  return {
    hydration_p50_ms: history.reduce((s, b) => s + b.hydration_p50_ms, 0) / n,
    http_4xx_rate: history.reduce((s, b) => s + b.http_4xx_rate, 0) / n,
    login_redirects: history.reduce((s, b) => s + b.login_redirects, 0) / n,
  };
}

// ---------------------------------------------------------------------------
// Tab ownership (consensus Δ4)
// ---------------------------------------------------------------------------

/** Open a tab, immediately register it in extension_owned_tabs. */
async function openOwnedTab(url: string, role: OwnedTab['role']): Promise<number> {
  const tab = await chrome.tabs.create({ url, active: false });
  const tabId = tab.id!;
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  extension_owned_tabs[tabId] = { tabId, role, createdAt: new Date().toISOString() };
  await setStorage({ extension_owned_tabs });
  return tabId;
}

/** Navigate an existing owned tab to a new URL. */
async function navigateOwnedTab(tabId: number, url: string): Promise<void> {
  await chrome.tabs.update(tabId, { url });
}

/** Close a tab and remove from extension_owned_tabs. */
async function closeOwnedTab(tabId: number): Promise<void> {
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  delete extension_owned_tabs[tabId];
  await setStorage({ extension_owned_tabs });
  try {
    await chrome.tabs.remove(tabId);
  } catch {
    // Tab may already be closed
  }
}

/** Returns all owned tab IDs for a given role. */
export async function ownedTabsForRole(role: OwnedTab['role']): Promise<number[]> {
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  return Object.entries(extension_owned_tabs)
    .filter(([, entry]) => entry.role === role)
    .map(([id]) => Number(id));
}

/** On SW resume: validate each entry in extension_owned_tabs via chrome.tabs.get.
 *  Prune missing or URL-drifted tabs. NEVER chrome.tabs.query against IG at large. */
async function validateOwnedTabs(): Promise<void> {
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  const pruned: Record<number, OwnedTab> = {};

  for (const [idStr, entry] of Object.entries(extension_owned_tabs)) {
    const tabId = Number(idStr);
    try {
      const tab = await chrome.tabs.get(tabId);
      const tabUrl = tab.url ?? '';
      // Verify URL still matches role
      if (urlMatchesRole(tabUrl, entry.role)) {
        pruned[tabId] = entry;
      } else {
        console.log(`[instagram-logger] BG: pruning tab ${tabId} (URL drifted from role ${entry.role})`);
      }
    } catch {
      // Tab no longer exists
      console.log(`[instagram-logger] BG: pruning tab ${tabId} (no longer exists)`);
    }
  }

  await setStorage({ extension_owned_tabs: pruned });
}

function urlMatchesRole(url: string, role: OwnedTab['role']): boolean {
  if (!url) return false;
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  if (role === 'saved-grid') {
    const isIg = u.origin === 'https://www.instagram.com' && /\/saved(\/|$)/.test(u.pathname);
    const isDevFake = __EXT_DEV__
      && (u.origin === 'http://localhost:9090' || u.origin === 'http://127.0.0.1:9090')
      && /\/saved(\/|$)/.test(u.pathname);
    return isIg || isDevFake;
  }
  if (role === 'collection') {
    return u.origin === 'https://www.instagram.com' && /\/saved\/.+/.test(u.pathname);
  }
  if (role === 'post-detail') {
    return /\/p\//.test(u.pathname);
  }
  return false;
}

// ---------------------------------------------------------------------------
// Phase machine helpers
// ---------------------------------------------------------------------------

async function getPhase(): Promise<Phase> {
  const { phase } = await getStorage(['phase']);
  return phase;
}

async function setPhase(phase: Phase): Promise<void> {
  await setStorage({ phase });
}

// ---------------------------------------------------------------------------
// Discovery orchestration
// ---------------------------------------------------------------------------

/** Open (or navigate to) the saved-grid tab for all-posts discovery. */
async function startDiscoveryAll(): Promise<void> {
  const igUser = await getIgUsername();
  if (!igUser) {
    console.warn('[instagram-logger] ig_username not configured — set via popup before starting discovery');
    await setPhase('idle');
    return;
  }

  await setPhase('discovery_all');

  const existingTabs = await ownedTabsForRole('saved-grid');
  const url = `${IG_BASE}/${igUser}/saved/all-posts/`;

  if (existingTabs.length > 0) {
    await navigateOwnedTab(existingTabs[0], url);
  } else {
    await openOwnedTab(url, 'saved-grid');
  }

  console.log('[instagram-logger] BG: discovery_all started →', url);
}

async function startDiscoveryCollections(): Promise<void> {
  const igUser = await getIgUsername();
  if (!igUser) {
    console.warn('[instagram-logger] ig_username not configured — set via popup before starting discovery');
    await setPhase('idle');
    return;
  }

  await setPhase('discovery_collections');

  const existingTabs = await ownedTabsForRole('saved-grid');
  const url = `${IG_BASE}/${igUser}/saved/`;

  if (existingTabs.length > 0) {
    await navigateOwnedTab(existingTabs[0], url);
  } else {
    await openOwnedTab(url, 'saved-grid');
  }

  console.log('[instagram-logger] BG: discovery_collections started →', url);
}

/** Navigate to the next collection and start per-collection capture. */
async function navigateToCollection(slug: string, collectionId: string): Promise<void> {
  const igUser = await getIgUsername();
  if (!igUser) {
    console.warn('[instagram-logger] ig_username not configured — set via popup before starting discovery');
    await setPhase('idle');
    return;
  }

  const existingTabs = await ownedTabsForRole('saved-grid');
  const url = `${IG_BASE}/${igUser}/saved/${slug}/`;

  if (existingTabs.length > 0) {
    await navigateOwnedTab(existingTabs[0], url);
  } else {
    await openOwnedTab(url, 'saved-grid');
  }

  console.log('[instagram-logger] BG: navigating to collection', slug, collectionId);
}

async function getIgUsername(): Promise<string> {
  const { ig_username } = await getStorage(['ig_username']);
  if (!ig_username) {
    console.error('[instagram-logger] BG: ig_username is not set in storage');
  }
  return ig_username;
}

// ---------------------------------------------------------------------------
// Message dispatcher
// ---------------------------------------------------------------------------

async function dispatchMessage(
  msg: Record<string, unknown>,
  _sender: chrome.runtime.MessageSender,
): Promise<unknown> {
  const type = msg.type as string;

  switch (type) {
    case 'start_discovery': {
      const phase = await getPhase();
      if (phase === 'idle') {
        await startDiscoveryAll();
        return { ok: true, phase: 'discovery_all' };
      }
      return { ok: false, phase, reason: 'not idle' };
    }

    case 'content_ready': {
      // Content script announces itself.
      const mode = msg.mode as string;
      const phase = await getPhase();

      // Handle post_detail mode (E4)
      if (mode === 'post_detail') {
        if (phase !== 'enrichment') {
          console.warn(
            `[instagram-logger] BG: content_ready mode=post_detail but phase=${phase}, ignoring`,
          );
          return { type: 'noop' };
        }
        // Reply with start_extract after jittered hydration delay (plan §4.5)
        await sleep(uniform(2000, 5000));
        return { type: 'start_extract' };
      }

      // Watch phase: all_posts grid is a watch_peek
      if (phase === 'watch' && mode === 'all_posts') {
        const reply: Record<string, unknown> = {
          type: 'start_capture',
          mode: 'watch_peek',
          jitter_config: {
            scroll_delay_min_ms: 800,
            scroll_delay_max_ms: 4000,
            end_of_list_delay_min_ms: 2000,
            end_of_list_delay_max_ms: 4000,
          },
        };
        return reply;
      }

      // Validate that mode matches current phase for grid modes
      const expectedMode = phaseToExpectedMode(phase);
      if (expectedMode !== null && mode !== expectedMode) {
        console.warn(
          `[instagram-logger] BG: content_ready mode=${mode} but expected=${expectedMode} (phase=${phase}), ignoring`,
        );
        return { type: 'noop' };
      }

      const reply: Record<string, unknown> = { type: 'start_capture' };

      // For collection mode, supply collection_id from persistent queue
      if (mode === 'collection') {
        const { pending_collections } = await getStorage(['pending_collections']);
        if (pending_collections.length > 0) {
          reply.collection_id = pending_collections[0].id;
        }
      }

      // Jitter config (intra-burst values from plan §4.3)
      reply.jitter_config = {
        scroll_delay_min_ms: 800,
        scroll_delay_max_ms: 4000,
        end_of_list_delay_min_ms: 2000,
        end_of_list_delay_max_ms: 4000,
      };

      return reply;
    }

    case 'shortcodes_batch': {
      const items = msg.items as Array<{
        shortcode: string;
        recency_rank: number;
        thumb_url?: string;
      }>;
      const batchMode = msg.mode as 'all_posts' | 'collection';
      const collectionId = msg.collection_id as string | undefined;

      try {
        await api.postShortcodes({
          source: batchMode,
          ...(collectionId ? { collection_id: collectionId } : {}),
          items,
        });

        // Also POST membership when mode=collection (plan §B.6)
        if (batchMode === 'collection' && collectionId) {
          const membershipItems = items.map((it) => ({
            shortcode: it.shortcode,
            collection_id: collectionId,
          }));
          await api.postMembership(membershipItems);
        }
      } catch (e) {
        console.error('[instagram-logger] BG: shortcodes_batch API error:', e);
        return { ok: false, error: String(e) };
      }

      return { ok: true };
    }

    case 'collections_index': {
      const rawItems = msg.items as Array<{ slug: string; name: string }>;

      // POST to backend collections endpoint.
      // Decision (plan §11 open questions): use slug as id for now.
      // Real IG numeric collection IDs require deeper DOM extraction; deferred.
      const collectionsPayload = rawItems.map((it) => ({
        id: it.slug,
        name: it.name,
        is_all_posts: false,
      }));

      try {
        await api.postCollections(collectionsPayload);
      } catch (e) {
        console.error('[instagram-logger] BG: collections_index API error:', e);
      }

      // Queue collections for per-collection Pass B — persisted to survive SW eviction
      await setStorage({ pending_collections: rawItems.map((it) => ({ slug: it.slug, id: it.slug })) });

      // Start iterating collections one at a time
      await advanceToNextCollection();

      return { ok: true };
    }

    case 'post_outcome': {
      // E4: content script reports enriched or lost outcome for a post
      const shortcode = msg.shortcode as string;
      const outcome = msg.outcome as 'enriched' | 'lost';

      console.log(`[instagram-logger] BG: post_outcome shortcode=${shortcode} outcome=${outcome}`);

      // R6: record hydration time for this post
      recordHydrationTime();

      // Clear the per-post timeout alarm — we got a response
      chrome.alarms.clear(ALARM_POST_TIMEOUT);
      currentEnrichmentShortcode = null;

      if (outcome === 'lost') {
        // POST lost to backend
        try {
          await api.postPost({ shortcode, outcome: 'lost' });
        } catch (e) {
          console.error('[instagram-logger] BG: post_outcome(lost) API error:', e);
        }

        // Close post-detail tab and advance to next target
        const tabs = await ownedTabsForRole('post-detail');
        for (const tabId of tabs) await closeOwnedTab(tabId);

        postRetryCount.delete(shortcode);
        await startEnrichment();

      } else if (outcome === 'enriched') {
        const payload = msg.payload as Record<string, unknown>;

        // POST enriched post to backend
        try {
          await api.postPost({ shortcode, outcome: 'enriched', ...payload });
        } catch (e) {
          console.error('[instagram-logger] BG: post_outcome(enriched) API error:', e);
        }

        // Dispatch media fetches for each slide
        const slides = (payload?.slides as Array<Record<string, unknown>>) ?? [];
        await ensureOffscreenDocument();

        for (let i = 0; i < slides.length; i++) {
          const slide = slides[i];
          const mediaUrl = slide.media_url as string | undefined;
          const mediaType = (slide.media_type as 'image' | 'video') ?? 'image';

          if (!mediaUrl) {
            // No URL — mark failed immediately
            try {
              await api.mediaFailed(shortcode, i, 1, 'no_url');
            } catch {
              // best-effort
            }
            continue;
          }

          const slideRef: SlideRef = {
            post_id: shortcode,
            slide_idx: i,
            media_url: mediaUrl,
            media_type: mediaType,
          };

          const mediaKey = `${shortcode}:${i}`;
          let result: MediaOutcome;

          try {
            result = await dispatchMediaFetch(slideRef);
          } catch {
            result = { outcome: 'transient_fail', http: 0 };
          }

          // R6: record HTTP response for heat detection
          if (result.outcome === 'transient_fail') {
            recordHttpResponse(result.http);
          } else if (result.outcome === 'present') {
            recordHttpResponse(200);
          }

          if (result.outcome === 'present') {
            mediaTransientFails.delete(mediaKey);
          } else if (result.outcome === 'media_failed') {
            // Hard failure — report immediately
            try {
              await api.mediaFailed(shortcode, i, 1, result.reason);
            } catch {
              // best-effort
            }
            mediaTransientFails.delete(mediaKey);
          } else {
            // transient_fail — track for re-visit logic
            const fails = (mediaTransientFails.get(mediaKey) ?? 0) + 1;
            mediaTransientFails.set(mediaKey, fails);

            if (fails >= 3) {
              // After 3 transient fails, mark media_failed (plan §4.6 simplified; re-visit in full impl)
              try {
                await api.mediaFailed(shortcode, i, fails, `transient_http_${result.http}`);
              } catch {
                // best-effort
              }
              mediaTransientFails.delete(mediaKey);
            }
          }
        }

        // Close post-detail tab and advance
        const tabs = await ownedTabsForRole('post-detail');
        for (const tabId of tabs) await closeOwnedTab(tabId);

        postRetryCount.delete(shortcode);
        await startEnrichment();
      }

      return { ok: true };
    }

    case 'auth_logged_out': {
      const evidence = msg.evidence as string ?? 'unknown';
      await handleAuthLoggedOut(evidence);
      // Also increment login_redirects in current burst (mid-burst detection)
      if (currentBurst) {
        currentBurst.login_redirects++;
      }
      return { ok: true };
    }

    case 'capture_done': {
      const doneMode = msg.mode as string;
      const totalSeen = msg.total_seen as number;
      console.log(`[instagram-logger] BG: capture_done mode=${doneMode} total_seen=${totalSeen}`);

      if (doneMode === 'watch_peek') {
        // watch_peek complete: close the tab, re-schedule next peek
        const gridTabs = await ownedTabsForRole('saved-grid');
        for (const tabId of gridTabs) await closeOwnedTab(tabId);
        scheduleWatchPeek();
        console.log('[instagram-logger] BG: watch_peek done, rescheduled');
      } else if (doneMode === 'all_posts') {
        // Transition to discovery_collections
        await startDiscoveryCollections();
      } else if (doneMode === 'collections_index') {
        // collections_index done is handled by collections_index message
      } else if (doneMode === 'collection') {
        // One collection done — pop it from persistent queue and advance
        const { pending_collections } = await getStorage(['pending_collections']);
        if (pending_collections.length > 0) {
          pending_collections.shift();
          await setStorage({ pending_collections });
        }

        // When all collections done, check if we should transition to enrichment
        const updated = await getStorage(['pending_collections']);
        if (updated.pending_collections.length === 0) {
          // All collections exhausted → start enrichment (plan §4.2)
          console.log('[instagram-logger] BG: all collections done, transitioning to enrichment');
          // Close saved-grid tab
          const gridTabs = await ownedTabsForRole('saved-grid');
          for (const tabId of gridTabs) await closeOwnedTab(tabId);
          await startEnrichment();
        } else {
          await advanceToNextCollection();
        }
      }

      return { ok: true };
    }

    case 'pause': {
      const prev = await getPhase();
      if (prev !== 'paused') {
        await setPhase('paused');
        // Also send pause to content script tabs
        await broadcastToOwnedTabs({ type: 'pause' });
      }
      return { ok: true };
    }

    case 'resume': {
      const phase = await getPhase();
      if (phase === 'paused') {
        // Restore to previous active phase (default: idle)
        // E3: restore to discovery_all or discovery_collections based on backend state
        const state = await api.getState().catch(() => null);
        const restored: Phase = (state?.phase_suggestion as Phase | undefined) ?? 'idle';
        await setPhase(restored);
        await broadcastToOwnedTabs({ type: 'resume' });
      }
      return { ok: true };
    }

    default:
      console.warn('[instagram-logger] BG: unknown message type:', type);
      return { ok: false, error: `unknown type: ${type}` };
  }
}

// ---------------------------------------------------------------------------
// Offscreen document management (plan §4.6)
// ---------------------------------------------------------------------------

async function ensureOffscreenDocument(): Promise<void> {
  try {
    // Chrome 116+ supports chrome.offscreen.hasDocument
    const existing = await (chrome.offscreen as unknown as { hasDocument?: () => Promise<boolean> }).hasDocument?.();
    if (existing) return;
  } catch {
    // hasDocument not available in older Chrome — fall through to create
  }

  try {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['BLOBS' as chrome.offscreen.Reason],
      justification: 'Media fetch + SHA-256 hashing for IG saved-post archive',
    });
  } catch (e) {
    // May throw if document already exists (race condition)
    const msg = String(e);
    if (!msg.includes('Only a single offscreen')) {
      throw e;
    }
  }
}

// ---------------------------------------------------------------------------
// Media dispatch to offscreen (plan §4.6)
// ---------------------------------------------------------------------------

interface SlideRef {
  post_id: string;
  slide_idx: number;
  media_url: string;
  media_type: 'image' | 'video' | 'carousel';
}

type MediaOutcome =
  | { outcome: 'present'; sha: string; deduplicated: boolean }
  | { outcome: 'media_failed'; reason: string }
  | { outcome: 'transient_fail'; http: number };

async function dispatchMediaFetch(slide: SlideRef): Promise<MediaOutcome> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      {
        type: 'fetch_media',
        post_id: slide.post_id,
        slide_idx: slide.slide_idx,
        media_url: slide.media_url,
        media_type: slide.media_type,
      },
      (reply) => {
        if (chrome.runtime.lastError) {
          resolve({ outcome: 'transient_fail', http: 0 });
          return;
        }
        resolve(reply as MediaOutcome ?? { outcome: 'transient_fail', http: 0 });
      },
    );
  });
}

// ---------------------------------------------------------------------------
// Enrichment orchestration (plan §4.5)
// ---------------------------------------------------------------------------

/** Per-post transient retry counters (in-memory; resets on SW eviction — acceptable per plan) */
const postRetryCount = new Map<string, number>();
const MAX_POST_RETRIES = 3;

/** Shortcode currently being enriched (for per-post timeout tracking) */
let currentEnrichmentShortcode: string | null = null;

// Track per-media transient-fail counts (in-memory)
const mediaTransientFails = new Map<string, number>();

// Alarm name for per-post extraction timeout (plan §4.5: content script never loaded)
const ALARM_POST_TIMEOUT = 'post_timeout';

async function startEnrichment(): Promise<void> {
  await setPhase('enrichment');
  await ensureOffscreenDocument();

  // Initialize burst state on first invocation in this enrichment cycle
  if (!currentBurst) {
    initBurstState();
  }

  let state: Awaited<ReturnType<typeof api.getState>>;
  try {
    state = await api.getState();
  } catch (e) {
    console.warn('[instagram-logger] BG: startEnrichment: getState failed:', e);
    return;
  }

  // Check priority_target first (manual retry, §4.9 Δ2)
  const target = state.priority_target ?? state.next_enrichment_target;
  if (!target) {
    console.log('[instagram-logger] BG: enrichment complete — transitioning to watch');
    // Close burst before transitioning
    await closeBurst(0, 0);
    await enterWatchPhase();
    return;
  }

  const shortcode = target.shortcode;
  console.log('[instagram-logger] BG: enrichment target:', shortcode);

  currentEnrichmentShortcode = shortcode;

  // Set a 60s timeout alarm — if post_outcome never arrives, retry or skip
  chrome.alarms.create(ALARM_POST_TIMEOUT, { delayInMinutes: 1 });

  // Open or reuse a post-detail tab
  const existingTabs = await ownedTabsForRole('post-detail');
  const url = `${IG_BASE}/p/${shortcode}/`;

  if (existingTabs.length > 0) {
    await navigateOwnedTab(existingTabs[0], url);
  } else {
    await openOwnedTab(url, 'post-detail');
  }
}

async function advanceToNextCollection(): Promise<void> {
  const { pending_collections } = await getStorage(['pending_collections']);

  if (pending_collections.length === 0) {
    // All collections done → start enrichment (handled by capture_done for the final collection)
    // This path is a fallback if called directly with empty queue.
    console.log('[instagram-logger] BG: advanceToNextCollection: queue empty, starting enrichment');
    const gridTabs = await ownedTabsForRole('saved-grid');
    for (const tabId of gridTabs) {
      await closeOwnedTab(tabId);
    }
    await startEnrichment();
    return;
  }

  const next = pending_collections[0];
  await navigateToCollection(next.slug, next.id);
}

function phaseToExpectedMode(phase: Phase): string | null {
  switch (phase) {
    case 'discovery_all':
      return 'all_posts';
    case 'discovery_collections':
      // Could be collections_index or collection
      return null; // Accept any sub-mode
    default:
      return null;
  }
}

async function broadcastToOwnedTabs(msg: unknown): Promise<void> {
  const { extension_owned_tabs } = await getStorage(['extension_owned_tabs']);
  for (const idStr of Object.keys(extension_owned_tabs)) {
    const tabId = Number(idStr);
    chrome.tabs.sendMessage(tabId, msg).catch(() => {
      // Tab may not have content script; ignore
    });
  }
}

// ---------------------------------------------------------------------------
// Message listener (with origin validation per E2 security M3)
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener(
  (
    message: unknown,
    sender: chrome.runtime.MessageSender,
    sendResponse: (response?: unknown) => void,
  ) => {
    // Origin validation
    if (sender.id !== chrome.runtime.id) {
      console.warn('[instagram-logger] BG: reject cross-extension message');
      return false;
    }

    const senderUrl = sender.url ?? sender.tab?.url ?? '';
    const allowedHosts: string[] = ['https://www.instagram.com/'];
    if (__EXT_DEV__) {
      allowedHosts.push('http://localhost:9090/', 'http://127.0.0.1:9090/');
    }
    const isContentScript = allowedHosts.some((h) => senderUrl.startsWith(h));
    // Allow popup messages (no tab URL) and content scripts from allowed hosts
    const isPopupOrSW = !senderUrl || senderUrl.startsWith('chrome-extension://');

    if (!isContentScript && !isPopupOrSW) {
      console.warn('[instagram-logger] BG: reject bad sender URL', senderUrl);
      return false;
    }

    // Validate message shape
    if (!message || typeof message !== 'object' || typeof (message as Record<string, unknown>).type !== 'string') {
      return false;
    }

    const msg = message as Record<string, unknown>;

    dispatchMessage(msg, sender)
      .then((reply) => sendResponse(reply))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));

    return true; // async response
  },
);
