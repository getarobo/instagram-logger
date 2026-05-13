// URL redaction helper (consensus M4): replaces /<user>/ with /<user>/ in log output.
// Used in content scripts to avoid logging real usernames.

/**
 * Returns a redacted URL string safe for logging.
 * Replaces the first path segment (username) with <user>.
 * E.g. https://www.instagram.com/janedoe/saved/all-posts/ →
 *      https://www.instagram.com/<user>/saved/all-posts/
 */
export function redactPath(url: string): string {
  try {
    const u = new URL(url);
    return `${u.origin}${u.pathname.replace(/^\/[^/]+\//, '/<user>/')}`;
  } catch {
    return '<invalid>';
  }
}
