// Content script for /<you>/saved/* pages.
// E3: real scroll-and-extract implementation.
// Plan §4.4 — Pass A (all_posts) + Pass B (collection) + collections_index.
// Consensus Δ4: tab ownership invariants are enforced in background.ts.

import { uniform, sleep } from '../lib/jitter';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type CaptureMode = 'all_posts' | 'collection' | 'collections_index';

interface StartCapturePayload {
  collection_id?: string;
  jitter_config?: {
    scroll_delay_min_ms?: number;
    scroll_delay_max_ms?: number;
    end_of_list_delay_min_ms?: number;
    end_of_list_delay_max_ms?: number;
  };
}

interface ShortcodeItem {
  shortcode: string;
  recency_rank: number;
  thumb_url?: string;
}

interface CollectionItem {
  slug: string;
  name: string;
}

// ---------------------------------------------------------------------------
// URL → mode detection
// ---------------------------------------------------------------------------

function detectMode(): CaptureMode | null {
  const path = location.pathname;
  // /<you>/saved/all-posts/
  if (/^\/[^/]+\/saved\/all-posts\/?$/.test(path)) {
    return 'all_posts';
  }
  // /<you>/saved/<slug>/ — not all-posts, not bare /saved/
  if (/^\/[^/]+\/saved\/[^/]+\/?$/.test(path)) {
    return 'collection';
  }
  // /<you>/saved/ (bare index)
  if (/^\/[^/]+\/saved\/?$/.test(path)) {
    return 'collections_index';
  }
  return null;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const mode = detectMode();
  if (mode === null) {
    console.log('[instagram-logger] saved-grid: URL does not match any known mode, exiting', location.pathname);
    return;
  }

  console.log('[instagram-logger] saved-grid loaded', mode, location.href);

  // Send content_ready and wait for start_capture reply (30s timeout)
  let startPayload: StartCapturePayload | null = null;
  try {
    startPayload = await Promise.race<StartCapturePayload | null>([
      new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: 'content_ready', mode, url: location.href }, (reply) => {
          if (chrome.runtime.lastError) {
            console.warn('[instagram-logger] saved-grid: content_ready sendMessage error:', chrome.runtime.lastError.message);
            resolve(null);
            return;
          }
          if (reply && reply.type === 'start_capture') {
            resolve(reply as StartCapturePayload);
          } else {
            resolve(null);
          }
        });
      }),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 30_000)),
    ]);
  } catch (e) {
    console.warn('[instagram-logger] saved-grid: error waiting for start_capture:', e);
  }

  if (startPayload === null) {
    console.warn('[instagram-logger] saved-grid: no start_capture received within 30s, exiting');
    return;
  }

  const collectionId: string | undefined = startPayload.collection_id;
  const jitterCfg = startPayload.jitter_config ?? {};
  const scrollDelayMin = jitterCfg.scroll_delay_min_ms ?? 800;
  const scrollDelayMax = jitterCfg.scroll_delay_max_ms ?? 4000;
  const eolDelayMin = jitterCfg.end_of_list_delay_min_ms ?? 2000;
  const eolDelayMax = jitterCfg.end_of_list_delay_max_ms ?? 4000;

  // Guard: verify mode matches expected
  if (mode === 'collection' && !collectionId) {
    console.warn('[instagram-logger] saved-grid: mode=collection but no collection_id provided, exiting');
    return;
  }

  if (mode === 'collections_index') {
    await captureCollectionsIndex();
    return;
  }

  // modes: all_posts | collection
  await captureGrid(mode, collectionId, scrollDelayMin, scrollDelayMax, eolDelayMin, eolDelayMax);
}

// ---------------------------------------------------------------------------
// Pause handling
// ---------------------------------------------------------------------------

let paused = false;

chrome.runtime.onMessage.addListener((msg: unknown) => {
  if (msg && typeof msg === 'object' && 'type' in msg) {
    const m = msg as { type: string };
    if (m.type === 'pause') {
      paused = true;
      console.log('[instagram-logger] saved-grid: paused');
    } else if (m.type === 'resume') {
      paused = false;
      console.log('[instagram-logger] saved-grid: resumed');
    }
  }
});

async function waitWhilePaused(): Promise<void> {
  while (paused) {
    await sleep(500);
  }
}

// ---------------------------------------------------------------------------
// Grid capture (all_posts + collection)
// ---------------------------------------------------------------------------

async function captureGrid(
  mode: 'all_posts' | 'collection',
  collectionId: string | undefined,
  scrollDelayMin: number,
  scrollDelayMax: number,
  eolDelayMin: number,
  eolDelayMax: number,
): Promise<void> {
  const seen = new Set<string>();
  let recencyRank = 0;
  let pendingBatch: ShortcodeItem[] = [];
  const BATCH_SIZE = 50;

  // MutationObserver: watch for new post-link nodes
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          extractShortcodesFromElement(node as Element, seen, pendingBatch, () => recencyRank++);
        }
      }
    }
  });

  // Also extract any already-rendered shortcodes before scroll starts
  extractShortcodesFromElement(document.body, seen, pendingBatch, () => recencyRank++);

  observer.observe(document.body, { childList: true, subtree: true });

  // Flush helper
  async function flushBatch(): Promise<void> {
    if (pendingBatch.length === 0) return;
    const batch = pendingBatch.splice(0, pendingBatch.length);
    try {
      await new Promise<void>((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            type: 'shortcodes_batch',
            items: batch,
            mode,
            ...(collectionId ? { collection_id: collectionId } : {}),
          },
          (reply) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
              return;
            }
            if (reply && reply.ok) {
              resolve();
            } else {
              reject(new Error('shortcodes_batch reply was not ok'));
            }
          },
        );
      });
    } catch (e) {
      console.error('[instagram-logger] saved-grid: batch flush error:', e);
      // Re-add to front of pending for retry on next flush
      pendingBatch.unshift(...batch);
    }
  }

  // Scroll loop with end-of-list detection
  let stableCount = 0;
  let lastScrollHeight = 0;
  const EOL_STABLE_THRESHOLD = 5;

  while (true) {
    await waitWhilePaused();

    // Flush if batch is large enough
    if (pendingBatch.length >= BATCH_SIZE) {
      await flushBatch();
    }

    const currentHeight = document.documentElement.scrollHeight;
    window.scrollTo(0, currentHeight);

    await sleep(uniform(scrollDelayMin, scrollDelayMax));

    const newHeight = document.documentElement.scrollHeight;

    if (newHeight === lastScrollHeight) {
      stableCount++;
      console.log(`[instagram-logger] saved-grid: scroll height stable (${stableCount}/${EOL_STABLE_THRESHOLD})`);
      if (stableCount >= EOL_STABLE_THRESHOLD) {
        // End of list detected
        break;
      }
      // Wait longer between end-of-list probes
      await sleep(uniform(eolDelayMin, eolDelayMax));
    } else {
      stableCount = 0;
      lastScrollHeight = newHeight;
    }
  }

  observer.disconnect();

  // Flush remaining items
  if (pendingBatch.length > 0) {
    await flushBatch();
  }

  // Signal capture done
  const totalSeen = seen.size;
  try {
    await new Promise<void>((resolve, reject) => {
      chrome.runtime.sendMessage(
        {
          type: 'capture_done',
          mode,
          ...(collectionId ? { collection_id: collectionId } : {}),
          total_seen: totalSeen,
        },
        (_reply) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          resolve();
        },
      );
    });
  } catch (e) {
    console.error('[instagram-logger] saved-grid: capture_done send error:', e);
  }

  console.log(`[instagram-logger] saved-grid: capture_done mode=${mode} total_seen=${totalSeen}`);
}

// ---------------------------------------------------------------------------
// Extract shortcodes from DOM element
// ---------------------------------------------------------------------------

function extractShortcodesFromElement(
  root: Element,
  seen: Set<string>,
  pending: ShortcodeItem[],
  nextRank: () => number,
): void {
  // Look for <a href="/p/<shortcode>/"> anchors
  const anchors = root.querySelectorAll<HTMLAnchorElement>('a[href]');
  // Also check if root itself is an anchor
  const allAnchors: HTMLAnchorElement[] = [];
  if (root instanceof HTMLAnchorElement) {
    allAnchors.push(root);
  }
  allAnchors.push(...Array.from(anchors));

  for (const anchor of allAnchors) {
    try {
      const href = anchor.getAttribute('href') ?? '';
      const match = href.match(/^\/p\/([A-Za-z0-9_-]+)\/?$/);
      if (!match) continue;
      const shortcode = match[1];
      if (seen.has(shortcode)) continue;
      seen.add(shortcode);

      // Extract thumb_url from child img
      let thumbUrl: string | undefined;
      try {
        const img = anchor.querySelector<HTMLImageElement>('img');
        if (img) {
          const srcset = img.getAttribute('srcset');
          if (srcset) {
            // First entry from srcset
            const first = srcset.split(',')[0].trim().split(/\s+/)[0];
            thumbUrl = first || undefined;
          }
          if (!thumbUrl) {
            thumbUrl = img.getAttribute('src') ?? undefined;
          }
        }
      } catch (imgErr) {
        console.log('[instagram-logger] saved-grid: img extract error:', imgErr);
      }

      const rank = nextRank();
      pending.push({ shortcode, recency_rank: rank, ...(thumbUrl ? { thumb_url: thumbUrl } : {}) });
    } catch (e) {
      console.log('[instagram-logger] saved-grid: anchor extract error:', e);
    }
  }
}

// ---------------------------------------------------------------------------
// Collections index capture
// ---------------------------------------------------------------------------

async function captureCollectionsIndex(): Promise<void> {
  const items: CollectionItem[] = [];
  const seen = new Set<string>();

  // Look for <a href="/<you>/saved/<slug>/"> patterns
  const anchors = document.querySelectorAll<HTMLAnchorElement>('a[href]');
  for (const anchor of anchors) {
    try {
      const href = anchor.getAttribute('href') ?? '';
      // Match /<username>/saved/<slug>/ — slug is not "all-posts"
      const match = href.match(/^\/[^/]+\/saved\/([^/]+)\/?$/);
      if (!match) continue;
      const slug = match[1];
      if (slug === 'all-posts') continue;
      if (seen.has(slug)) continue;
      seen.add(slug);

      // Extract collection name from span text inside anchor
      let name = slug;
      try {
        const span = anchor.querySelector('span');
        if (span && span.textContent) {
          name = span.textContent.trim() || slug;
        } else if (anchor.textContent) {
          name = anchor.textContent.trim() || slug;
        }
      } catch {
        // keep slug as name
      }

      items.push({ slug, name });
    } catch (e) {
      console.log('[instagram-logger] saved-grid: collection anchor error:', e);
    }
  }

  console.log('[instagram-logger] saved-grid: collections_index found', items.length, 'collections');

  try {
    await new Promise<void>((resolve, reject) => {
      chrome.runtime.sendMessage(
        { type: 'collections_index', items },
        (_reply) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          resolve();
        },
      );
    });
  } catch (e) {
    console.error('[instagram-logger] saved-grid: collections_index send error:', e);
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

main().catch((e) => {
  console.error('[instagram-logger] saved-grid: fatal error:', e);
});
