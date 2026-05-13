// Content script for https://www.instagram.com/* (document_idle)
// E5: real logout detection implementation.
// Plan §4.7 — detect login wall, send auth_logged_out to background.
// Defensive: all selectors wrapped in try/catch.

import { redactPath } from '../lib/redact';

// ---------------------------------------------------------------------------
// Logout evidence detection
// ---------------------------------------------------------------------------

type Evidence = 'login_url' | 'login_form' | 'login_cta';

function detectLoggedOut(): Evidence | null {
  // 1. URL path includes /accounts/login/ (definitive — IG redirect on logged-out access)
  try {
    if (location.pathname.includes('/accounts/login/')) {
      return 'login_url';
    }
  } catch (e) {
    console.warn('[instagram-logger] auth-watch: login_url check error:', e);
  }

  // 2. DOM contains both <input name="username"> AND <input name="password"> (login form)
  try {
    const usernameInput = document.querySelector<HTMLInputElement>('input[name="username"]');
    const passwordInput = document.querySelector<HTMLInputElement>('input[name="password"]');
    if (usernameInput && passwordInput) {
      return 'login_form';
    }
  } catch (e) {
    console.warn('[instagram-logger] auth-watch: login_form check error:', e);
  }

  // 3. A visible "Log in" CTA button / anchor
  try {
    // Check for role=button with "Log in" text
    const buttons = document.querySelectorAll<HTMLElement>('[role="button"]');
    for (const btn of buttons) {
      const text = btn.textContent?.trim().toLowerCase() ?? '';
      if (text === 'log in' || text === 'login') {
        return 'login_cta';
      }
    }
    // Check for anchor href=/accounts/login/
    const loginLink = document.querySelector<HTMLAnchorElement>('a[href="/accounts/login/"]');
    if (loginLink) {
      return 'login_cta';
    }
  } catch (e) {
    console.warn('[instagram-logger] auth-watch: login_cta check error:', e);
  }

  return null;
}

// ---------------------------------------------------------------------------
// Send logged-out signal to background
// ---------------------------------------------------------------------------

function sendLoggedOut(evidence: Evidence): void {
  const redactedUrl = redactPath(location.href);
  chrome.runtime.sendMessage(
    {
      type: 'auth_logged_out',
      url: redactedUrl,
      evidence,
    },
    (_reply) => {
      if (chrome.runtime.lastError) {
        // SW may not be active; non-fatal
        console.warn('[instagram-logger] auth-watch: sendMessage error:', chrome.runtime.lastError.message);
      }
    },
  );
}

// ---------------------------------------------------------------------------
// Recheck handler (background can request a fresh detection status)
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener(
  (
    message: unknown,
    _sender: chrome.runtime.MessageSender,
    sendResponse: (response?: unknown) => void,
  ) => {
    if (!message || typeof message !== 'object') return false;
    const msg = message as { type?: string };
    if (msg.type !== 'auth_recheck') return false;

    const evidence = detectLoggedOut();
    sendResponse({ logged_out: evidence !== null, evidence });
    return false;
  },
);

// ---------------------------------------------------------------------------
// Main: run detection on load
// ---------------------------------------------------------------------------

(function main() {
  try {
    const evidence = detectLoggedOut();
    if (evidence !== null) {
      console.log('[instagram-logger] auth-watch: logged-out detected, evidence=', evidence, redactPath(location.href));
      sendLoggedOut(evidence);
    } else {
      console.log('[instagram-logger] auth-watch: session OK', redactPath(location.href));
    }
  } catch (e) {
    console.error('[instagram-logger] auth-watch: fatal error in main:', e);
  }
})();
