// Content script for https://www.instagram.com/p/<shortcode>/
// E4: real payload extraction + outcome detection.
// Plan §4.5 enrichment, consensus M1 (URL allowlist), M4 (username redaction).

import { uniform, sleep } from '../lib/jitter';
import { redactPath } from '../lib/redact';

// ---------------------------------------------------------------------------
// URL scheme allowlist (consensus M1)
// ---------------------------------------------------------------------------

function sanitizeUrl(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  try {
    const u = new URL(raw, location.href);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return undefined;
    return u.href;
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Username redaction for logs (consensus M4)
// ---------------------------------------------------------------------------

function logUrl(url: string): string {
  return redactPath(url);
}

// ---------------------------------------------------------------------------
// Pause / resume support (mirrors saved-grid.ts pattern)
// ---------------------------------------------------------------------------

let paused = false;

chrome.runtime.onMessage.addListener((msg: unknown) => {
  if (msg && typeof msg === 'object' && 'type' in msg) {
    const m = msg as { type: string };
    if (m.type === 'pause') {
      paused = true;
      console.log('[instagram-logger] post-detail: paused');
    } else if (m.type === 'resume') {
      paused = false;
      console.log('[instagram-logger] post-detail: resumed');
    }
  }
});

async function waitWhilePaused(): Promise<void> {
  while (paused) {
    await sleep(500);
  }
}

// ---------------------------------------------------------------------------
// Post container selectors
// ---------------------------------------------------------------------------

const POST_CONTAINER_SELECTORS = [
  'article[role="presentation"]',
  'article',
  '[data-testid="post"]',
  '#post',
  'div[role="dialog"] article',
  'main article',
];

function findPostContainer(): Element | null {
  for (const sel of POST_CONTAINER_SELECTORS) {
    const el = document.querySelector(sel);
    if (el) return el;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Hydration wait: poll for post container + 500ms of no mutations (up to 8s)
// ---------------------------------------------------------------------------

async function waitForHydration(): Promise<Element | null> {
  const TOTAL_TIMEOUT_MS = 8000;
  const STABLE_DURATION_MS = 500;
  const POLL_INTERVAL_MS = 100;

  const deadline = Date.now() + TOTAL_TIMEOUT_MS;
  let lastMutationAt = Date.now();
  let container: Element | null = null;

  // MutationObserver to track DOM activity
  const observer = new MutationObserver(() => {
    lastMutationAt = Date.now();
    container = findPostContainer();
  });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true });

  // Initial check
  container = findPostContainer();

  try {
    while (Date.now() < deadline) {
      await sleep(POLL_INTERVAL_MS);
      await waitWhilePaused();

      container = findPostContainer();
      if (container && (Date.now() - lastMutationAt) >= STABLE_DURATION_MS) {
        // DOM is stable and container found
        break;
      }
    }
  } finally {
    observer.disconnect();
  }

  return container;
}

// ---------------------------------------------------------------------------
// Outcome detection: 'lost' signals
// ---------------------------------------------------------------------------

function isLostPage(): boolean {
  const text = document.body.innerText ?? '';
  if (text.includes("Sorry, this page isn't available")) return true;
  if (text.includes("Sorry, this page is not available")) return true;
  if (location.pathname.startsWith('/accounts/login/')) return true;
  if (location.pathname.startsWith('/accounts/login')) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Extraction helpers
// ---------------------------------------------------------------------------

interface SlidePayload {
  carousel_index: number;
  media_url?: string;
  thumb_url?: string;
  media_type: 'image' | 'video';
  width?: number;
  height?: number;
  duration_seconds?: number;
}

interface AuthorPayload {
  username?: string;
  full_name?: string;
  avatar_url?: string;
  is_private?: boolean;
}

interface PostPayload {
  shortcode: string;
  caption?: string;
  author?: AuthorPayload;
  taken_at?: string;
  media_kind?: 'image' | 'video' | 'carousel';
  slides: SlidePayload[];
  location?: string;
  raw_html_snippet?: string;
}

/** Extract caption text from post container */
function extractCaption(container: Element): string | undefined {
  // Try <h1> in container
  const h1 = container.querySelector('h1');
  if (h1?.textContent?.trim()) return h1.textContent.trim();

  // Try dialog first paragraph
  const dialogP = document.querySelector('div[role="dialog"] p');
  if (dialogP?.textContent?.trim()) return dialogP.textContent.trim();

  // Try article-level spans/divs with substantive text (> 10 chars)
  const spans = container.querySelectorAll('span, p');
  for (const span of spans) {
    const t = span.textContent?.trim() ?? '';
    if (t.length > 10) return t;
  }

  return undefined;
}

/** Extract author info from post container */
function extractAuthor(container: Element): AuthorPayload {
  const author: AuthorPayload = {};

  // Username from URL (most reliable)
  // The post URL is /p/<shortcode>/ — author is in page content
  // Try aria-label pattern or header link
  const headerLinks = container.querySelectorAll<HTMLAnchorElement>('a[href]');
  for (const link of headerLinks) {
    const href = link.getAttribute('href') ?? '';
    // Author link: /<username>/  (single segment, not /p/)
    const m = href.match(/^\/([A-Za-z0-9._]+)\/?$/);
    if (m && m[1] !== 'p') {
      author.username = m[1];

      // full_name from h2 or nearby element
      const h2 = container.querySelector('h2');
      if (h2?.textContent?.trim()) {
        author.full_name = h2.textContent.trim();
      }

      // Avatar from img with username in alt
      const imgs = container.querySelectorAll<HTMLImageElement>('img');
      for (const img of imgs) {
        const alt = img.getAttribute('alt') ?? '';
        if (alt.toLowerCase().includes(m[1].toLowerCase())) {
          const src = sanitizeUrl(img.getAttribute('src'));
          if (src) {
            author.avatar_url = src;
            break;
          }
        }
      }
      break;
    }
  }

  // is_private: check for lock icon or private indicator
  const bodyText = document.body.innerText ?? '';
  author.is_private = bodyText.includes('This account is private') || bodyText.includes('Private');

  return author;
}

/** Extract taken_at from <time datetime> */
function extractTakenAt(container: Element): string | undefined {
  const timeEl = container.querySelector<HTMLTimeElement>('time[datetime]');
  if (timeEl) {
    const dt = timeEl.getAttribute('datetime');
    if (dt) return dt;
  }
  return undefined;
}

/** Extract slides from post container */
function extractSlides(container: Element): SlidePayload[] {
  const slides: SlidePayload[] = [];

  const imgs = Array.from(container.querySelectorAll<HTMLImageElement>('img'));
  const videos = Array.from(container.querySelectorAll<HTMLVideoElement>('video'));

  // Filter out small avatar/thumbnail images (width < 100 if known)
  const contentImgs = imgs.filter((img) => {
    // Skip tiny images (avatars, icons)
    if (img.naturalWidth > 0 && img.naturalWidth < 80) return false;
    if (img.naturalHeight > 0 && img.naturalHeight < 80) return false;
    // Skip images that are in nav/header (heuristic)
    const parent = img.closest('header, nav');
    if (parent) return false;
    return true;
  });

  if (videos.length > 0) {
    // Video post
    for (let i = 0; i < videos.length; i++) {
      const video = videos[i];
      const srcRaw = video.getAttribute('src') ?? video.currentSrc;
      const mediaUrl = sanitizeUrl(srcRaw);

      // Poster as thumb
      const posterRaw = video.getAttribute('poster');
      const thumbUrl = sanitizeUrl(posterRaw);

      const duration = isFinite(video.duration) ? video.duration : undefined;

      slides.push({
        carousel_index: i,
        media_url: mediaUrl,
        thumb_url: thumbUrl,
        media_type: 'video',
        width: video.videoWidth || undefined,
        height: video.videoHeight || undefined,
        duration_seconds: duration,
      });
    }
    // Also add any remaining images as additional carousel slides
    if (videos.length > 0 && contentImgs.length > 0) {
      const startIdx = videos.length;
      for (let i = 0; i < contentImgs.length; i++) {
        const img = contentImgs[i];
        const srcRaw = img.getAttribute('src') ?? '';
        const srcsetRaw = img.getAttribute('srcset');
        let mediaUrl = sanitizeUrl(srcRaw);
        if (srcsetRaw) {
          const first = srcsetRaw.split(',')[0].trim().split(/\s+/)[0];
          const sanitized = sanitizeUrl(first);
          if (sanitized) mediaUrl = sanitized;
        }
        slides.push({
          carousel_index: startIdx + i,
          media_url: mediaUrl,
          thumb_url: mediaUrl,
          media_type: 'image',
          width: img.naturalWidth || undefined,
          height: img.naturalHeight || undefined,
        });
      }
    }
  } else {
    // Image or carousel
    for (let i = 0; i < contentImgs.length; i++) {
      const img = contentImgs[i];
      const srcRaw = img.getAttribute('src') ?? '';
      const srcsetRaw = img.getAttribute('srcset');

      let mediaUrl = sanitizeUrl(srcRaw);
      // Prefer highest-res from srcset
      if (srcsetRaw) {
        const first = srcsetRaw.split(',')[0].trim().split(/\s+/)[0];
        const sanitized = sanitizeUrl(first);
        if (sanitized) mediaUrl = sanitized;
      }

      slides.push({
        carousel_index: i,
        media_url: mediaUrl,
        thumb_url: mediaUrl,
        media_type: 'image',
        width: img.naturalWidth || undefined,
        height: img.naturalHeight || undefined,
      });
    }
  }

  return slides;
}

/** Determine media_kind from slides */
function inferMediaKind(
  slides: SlidePayload[],
  container: Element,
): 'image' | 'video' | 'carousel' {
  if (slides.length > 1) return 'carousel';
  if (slides.length === 1 && slides[0].media_type === 'video') return 'video';
  // Check for video element in container even if no slides extracted
  if (container.querySelector('video')) return 'video';
  return 'image';
}

/** Extract location if visible */
function extractLocation(container: Element): string | undefined {
  // Look for a link to /explore/locations/
  const locLink = container.querySelector<HTMLAnchorElement>(
    'a[href^="/explore/locations/"]',
  );
  if (locLink?.textContent?.trim()) return locLink.textContent.trim();

  // Look for a span/div with aria-label containing "location"
  const locEl = container.querySelector('[aria-label*="location" i], [data-testid*="location" i]');
  if (locEl?.textContent?.trim()) return locEl.textContent.trim();

  return undefined;
}

/** Truncate raw HTML snippet to ~50KB */
function truncateHtml(html: string): string {
  const MAX_BYTES = 50 * 1024;
  if (html.length <= MAX_BYTES) return html;
  return html.slice(0, MAX_BYTES) + '<!-- truncated -->';
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  // 1. Verify URL matches /p/<shortcode>/
  const pathMatch = location.pathname.match(/^\/p\/([A-Za-z0-9_-]+)\/?$/);
  if (!pathMatch) {
    console.log('[instagram-logger] post-detail: URL does not match /p/<shortcode>/, exiting', logUrl(location.href));
    return;
  }

  const shortcode = pathMatch[1];
  console.log('[instagram-logger] post-detail: loaded for shortcode', shortcode, logUrl(location.href));

  // 2. Send content_ready and wait for start_extract (30s timeout)
  let gotStart = false;
  try {
    const reply = await Promise.race<unknown>([
      new Promise((resolve) => {
        chrome.runtime.sendMessage(
          { type: 'content_ready', mode: 'post_detail', shortcode },
          (r) => {
            if (chrome.runtime.lastError) {
              console.warn('[instagram-logger] post-detail: sendMessage error:', chrome.runtime.lastError.message);
              resolve(null);
              return;
            }
            resolve(r);
          },
        );
      }),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 30_000)),
    ]);

    if (reply && typeof reply === 'object' && (reply as Record<string, unknown>).type === 'start_extract') {
      gotStart = true;
    }
  } catch (e) {
    console.warn('[instagram-logger] post-detail: error waiting for start_extract:', e);
  }

  if (!gotStart) {
    console.warn('[instagram-logger] post-detail: no start_extract received within 30s, exiting');
    return;
  }

  console.log('[instagram-logger] post-detail: start_extract received, beginning hydration wait');

  // 3. Hydration wait
  const container = await waitForHydration();

  // 4. Outcome detection
  if (isLostPage() || container === null) {
    console.log('[instagram-logger] post-detail: outcome=lost (shortcode=%s)', shortcode);
    chrome.runtime.sendMessage({ type: 'post_outcome', shortcode, outcome: 'lost' }, () => {
      if (chrome.runtime.lastError) {
        console.warn('[instagram-logger] post-detail: post_outcome(lost) error:', chrome.runtime.lastError.message);
      }
    });
    return;
  }

  // 5. Extract payload (best-effort)
  let payload: PostPayload;
  try {
    const slides = extractSlides(container);
    const mediaKind = inferMediaKind(slides, container);
    const author = extractAuthor(container);
    const caption = extractCaption(container);
    const takenAt = extractTakenAt(container);
    const loc = extractLocation(container);
    const rawHtml = truncateHtml(container.outerHTML);

    payload = {
      shortcode,
      caption,
      author,
      taken_at: takenAt,
      media_kind: mediaKind,
      slides,
      location: loc,
      raw_html_snippet: rawHtml,
    };
  } catch (e) {
    console.error('[instagram-logger] post-detail: extraction error:', e);
    // Treat extraction failure as a transient issue (not lost) — send lost to trigger retry
    chrome.runtime.sendMessage({ type: 'post_outcome', shortcode, outcome: 'lost' }, () => {
      if (chrome.runtime.lastError) {
        console.warn('[instagram-logger] post-detail: post_outcome(lost/err) error:', chrome.runtime.lastError.message);
      }
    });
    return;
  }

  // 6. Jittered dwell before reporting (plan §4.3: uniform(1500, 8000)ms)
  await sleep(uniform(1500, 8000));
  await waitWhilePaused();

  // 7. Send enriched outcome
  console.log('[instagram-logger] post-detail: outcome=enriched shortcode=%s slides=%d', shortcode, payload.slides.length);
  chrome.runtime.sendMessage(
    { type: 'post_outcome', shortcode, outcome: 'enriched', payload },
    () => {
      if (chrome.runtime.lastError) {
        console.warn('[instagram-logger] post-detail: post_outcome(enriched) error:', chrome.runtime.lastError.message);
      }
    },
  );
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

main().catch((e) => {
  console.error('[instagram-logger] post-detail: fatal error:', e);
});
