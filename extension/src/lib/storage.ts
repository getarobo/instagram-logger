// Typed chrome.storage.local wrapper for the shape defined in plan §4.8 + consensus Δ4 + R6

import type { Phase, ResumeCursor, OwnedTab, BurstMetrics } from './types';

export interface ExtensionStorage {
  secret: string;
  phase: Phase;
  awake_window_start: string; // "08:00"
  awake_window_end: string; // "01:00"
  rest_day_iso: string | null; // weekly random rest day; null = not assigned yet
  last_burst_at: string | null;
  next_burst_at: string | null;
  resume_cursor: ResumeCursor;
  burst_history: Array<{
    start: string;
    end: string;
    posts_seen: number;
    media_uploaded: number;
  }>;
  // Consensus Δ4: tab ownership tracking (keyed by tabId as string — chrome.storage keys must be strings)
  extension_owned_tabs: Record<number, OwnedTab>;
  // Consensus R6: per-burst metrics rolling window (max length 7)
  burst_metrics: BurstMetrics[];
  // Consensus AC#21: bypass warm-up gate for fake-IG smoke testing
  test_mode_skip_warmup: boolean;
  // E3 Fix 1: persistent queue for Pass-B collection iteration (survives SW eviction)
  pending_collections: Array<{ slug: string; id: string }>;
  // E3 Fix 2: Instagram username required to build correct saved-posts URLs
  ig_username: string;
}

const DEFAULTS: ExtensionStorage = {
  secret: '',
  phase: 'idle',
  awake_window_start: '08:00',
  awake_window_end: '01:00',
  rest_day_iso: null,
  last_burst_at: null,
  next_burst_at: null,
  resume_cursor: {
    discovery_all: { last_recency_rank: null, scroll_y: 0 },
    discovery_collections: {
      current_collection_id: null,
      last_recency_rank: null,
    },
    enrichment: { last_shortcode_enriched: null },
  },
  burst_history: [],
  extension_owned_tabs: {},
  burst_metrics: [],
  test_mode_skip_warmup: false,
  pending_collections: [],
  ig_username: '',
};

export async function getStorage<K extends keyof ExtensionStorage>(
  keys: K[],
): Promise<Pick<ExtensionStorage, K>> {
  // Build defaults object for requested keys
  const defaults: Partial<ExtensionStorage> = {};
  for (const key of keys) {
    (defaults as Record<string, unknown>)[key] = DEFAULTS[key];
  }
  const result = await chrome.storage.local.get(defaults);
  return result as Pick<ExtensionStorage, K>;
}

export async function setStorage(
  partial: Partial<ExtensionStorage>,
): Promise<void> {
  await chrome.storage.local.set(partial);
}

export async function getSecret(): Promise<string> {
  const { secret } = await getStorage(['secret']);
  return secret;
}

export async function setSecret(secret: string): Promise<void> {
  await setStorage({ secret });
}

// Initialize all storage keys to defaults on first install
export async function initStorageDefaults(): Promise<void> {
  const existing = await chrome.storage.local.get(null);
  const missing: Partial<ExtensionStorage> = {};
  for (const key of Object.keys(DEFAULTS) as (keyof ExtensionStorage)[]) {
    if (!(key in existing)) {
      (missing as Record<string, unknown>)[key] =
        DEFAULTS[key as keyof ExtensionStorage];
    }
  }
  if (Object.keys(missing).length > 0) {
    await chrome.storage.local.set(missing);
  }
}
