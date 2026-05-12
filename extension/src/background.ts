// Service worker — phase machine + tab orchestration (E3).
// Plan §4.2 phase machine, §4.4 discovery, consensus Δ4 tab ownership.
// NEVER chrome.tabs.query against IG host pattern — use extension_owned_tabs map only.

import { api } from './lib/api';
import { getStorage, setStorage, initStorageDefaults } from './lib/storage';
import type { Phase, OwnedTab } from './lib/types';

const ALARM_HEARTBEAT = 'heartbeat';
const IG_BASE = 'https://www.instagram.com';

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
      // Content script announces itself. Respond with start_capture.
      const mode = msg.mode as string;
      const phase = await getPhase();

      // Validate that mode matches current phase
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

    case 'capture_done': {
      const doneMode = msg.mode as string;
      const totalSeen = msg.total_seen as number;
      console.log(`[instagram-logger] BG: capture_done mode=${doneMode} total_seen=${totalSeen}`);

      if (doneMode === 'all_posts') {
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
        await advanceToNextCollection();
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

async function advanceToNextCollection(): Promise<void> {
  const { pending_collections } = await getStorage(['pending_collections']);

  if (pending_collections.length === 0) {
    // All collections done → idle
    console.log('[instagram-logger] BG: all collections done, transitioning to idle');
    await setPhase('idle');

    // Close the saved-grid tab
    const tabs = await ownedTabsForRole('saved-grid');
    for (const tabId of tabs) {
      await closeOwnedTab(tabId);
    }
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
