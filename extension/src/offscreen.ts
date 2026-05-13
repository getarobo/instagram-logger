// Offscreen document — media-fetch worker (plan §4.6, E4).
// Single concurrency token: all fetchAndUpload calls are serialized via Promise chain queue.
// Consensus M1: URL scheme allowlist enforced before each fetch.

import { sha256Hex } from './lib/hash';
import { getSecret } from './lib/storage';
import { uniform, sleep } from './lib/jitter';

const BACKEND_BASE = 'http://127.0.0.1:8000';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FetchMediaMessage {
  type: 'fetch_media';
  post_id: string;
  slide_idx: number;
  media_url: string;
  media_type: 'image' | 'video' | 'carousel';
}

type MediaOutcome =
  | { outcome: 'present'; sha: string; deduplicated: boolean }
  | { outcome: 'media_failed'; reason: string }
  | { outcome: 'transient_fail'; http: number };

// ---------------------------------------------------------------------------
// Concurrency queue — one media in flight at a time across the extension
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let queue: Promise<any> = Promise.resolve();

function enqueue(fn: () => Promise<MediaOutcome>): Promise<MediaOutcome> {
  const next = queue.then(() => fn());
  // Allow queue tail to advance even if this slot errors
  queue = next.catch(() => undefined);
  return next;
}

// ---------------------------------------------------------------------------
// URL scheme allowlist helper (consensus M1)
// ---------------------------------------------------------------------------

function isAllowedUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Secret header helper
// ---------------------------------------------------------------------------

async function secretHeader(): Promise<Record<string, string>> {
  const secret = await getSecret();
  if (!secret) throw new Error('No ingest secret configured');
  return { 'X-Ingest-Secret': secret };
}

// ---------------------------------------------------------------------------
// HEAD /api/ingest/extension/media/exists
// ---------------------------------------------------------------------------

async function checkExists(sha: string): Promise<boolean> {
  const headers = await secretHeader();
  const resp = await fetch(
    `${BACKEND_BASE}/api/ingest/extension/media/exists?sha=${encodeURIComponent(sha)}`,
    { method: 'HEAD', headers },
  );
  if (resp.status === 204) return true;
  if (resp.status === 404) return false;
  throw new Error(`HEAD /media/exists → ${resp.status}`);
}

// ---------------------------------------------------------------------------
// POST /api/ingest/extension/media (multipart)
// ---------------------------------------------------------------------------

async function uploadMedia(
  blob: Blob,
  sha: string,
  mime: string,
  postId: string,
  slideIdx: number,
): Promise<Response> {
  const headers = await secretHeader();
  const fd = new FormData();
  fd.append('file', blob, `${sha}.bin`);
  fd.append('sha256', sha);
  fd.append('mime', mime);
  fd.append('post_id', postId);
  fd.append('slide_idx', String(slideIdx));
  return fetch(`${BACKEND_BASE}/api/ingest/extension/media`, {
    method: 'POST',
    headers,
    body: fd,
  });
}

// ---------------------------------------------------------------------------
// Per-slide processor
// ---------------------------------------------------------------------------

async function processOne(msg: FetchMediaMessage): Promise<MediaOutcome> {
  const { post_id, slide_idx, media_url } = msg;

  // Step 1: URL scheme allowlist (consensus M1)
  if (!isAllowedUrl(media_url)) {
    return { outcome: 'media_failed', reason: 'invalid_url_scheme' };
  }

  // Step 2: Fetch with credentials (carries IG cookies)
  let resp: Response;
  let blob: Blob;

  try {
    resp = await fetch(media_url, { credentials: 'include' });
    if (resp.ok) {
      blob = await resp.blob();
    } else {
      // Non-ok but not a TypeError — retry once with no-cors
      throw new TypeError('non-ok response, try no-cors');
    }
  } catch {
    // Retry once with mode: 'no-cors' (opaque blob — backend re-hashes from bytes)
    try {
      const opaqueResp = await fetch(media_url, { mode: 'no-cors', credentials: 'include' });
      // Opaque response: status=0, body readable
      blob = await opaqueResp.blob();
      // If blob is empty (truly blocked), treat as transient_fail
      if (blob.size === 0) {
        return { outcome: 'transient_fail', http: 0 };
      }
    } catch {
      return { outcome: 'transient_fail', http: 0 };
    }
  }

  // Step 3: Compute sha256
  const sha = await sha256Hex(blob);

  // Step 4: Dedup check
  let exists: boolean;
  try {
    exists = await checkExists(sha);
  } catch {
    exists = false;
  }

  if (exists) {
    // Release blob before returning
    blob = new Blob();
    return { outcome: 'present', sha, deduplicated: true };
  }

  // Step 5: Upload
  let uploadResp: Response;
  try {
    uploadResp = await uploadMedia(blob, sha, blob.type || 'application/octet-stream', post_id, slide_idx);
  } finally {
    // Memory hygiene: release blob reference regardless of upload outcome
    blob = new Blob();
  }

  if (uploadResp.status === 200) {
    return { outcome: 'present', sha, deduplicated: false };
  }
  if (uploadResp.status === 413) {
    return { outcome: 'media_failed', reason: 'too_large' };
  }
  if (uploadResp.status >= 400 && uploadResp.status < 500) {
    return { outcome: 'media_failed', reason: `http_${uploadResp.status}` };
  }
  // 5xx or unexpected
  return { outcome: 'transient_fail', http: uploadResp.status };
}

// ---------------------------------------------------------------------------
// Message listener
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener(
  (
    message: unknown,
    _sender: chrome.runtime.MessageSender,
    sendResponse: (response?: unknown) => void,
  ) => {
    if (
      !message ||
      typeof message !== 'object' ||
      (message as Record<string, unknown>).type !== 'fetch_media'
    ) {
      return false;
    }

    const msg = message as FetchMediaMessage;

    enqueue(async () => {
      const result = await processOne(msg);
      // Jittered delay between fetches (plan §4.3: uniform(400, 1800)ms)
      await sleep(uniform(400, 1800));
      return result;
    })
      .then((result) => sendResponse(result))
      .catch((e) =>
        sendResponse({ outcome: 'transient_fail', http: 0, error: String(e) }),
      );

    return true; // async response
  },
);

console.log('[instagram-logger] offscreen worker ready');
