// Tiny hash-based router. Supports two shapes:
//   #/                       -> { kind: "all" }
//   #/collections/<id>       -> { kind: "collection", id }
//
// A post-detail overlay rides as a `?p=<post_id>` query on either base
// path so the modal stacks over the grid without clobbering the active
// collection — back button closes the modal, second back returns to the
// previous list.
//
// react-router lands later; this stays the smallest thing that works.

import { useEffect, useState } from "react";

export type Route =
  | { kind: "all" }
  | { kind: "collection"; id: string };

function splitHash(hash: string): { path: string; query: string } {
  const h = hash.replace(/^#/, "");
  const qi = h.indexOf("?");
  if (qi === -1) return { path: h, query: "" };
  return { path: h.slice(0, qi), query: h.slice(qi + 1) };
}

export function parseRoute(hash: string): Route {
  const { path } = splitHash(hash);
  const m = path.match(/^\/collections\/([^/?#]+)/);
  if (m) return { kind: "collection", id: decodeURIComponent(m[1]) };
  return { kind: "all" };
}

export function parsePostOverlay(hash: string): string | null {
  const { query } = splitHash(hash);
  if (!query) return null;
  const params = new URLSearchParams(query);
  const p = params.get("p");
  return p && p.length > 0 ? p : null;
}

export function buildHashWithPost(
  currentHash: string,
  postId: string | null
): string {
  const { path } = splitHash(currentHash);
  const base = path === "" ? "/" : path;
  if (postId === null) return `#${base}`;
  return `#${base}?p=${encodeURIComponent(postId)}`;
}

export function setPostOverlay(postId: string | null): void {
  const next = buildHashWithPost(window.location.hash, postId);
  if (next !== window.location.hash) window.location.hash = next;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

export function usePostOverlay(): string | null {
  const [postId, setPostId] = useState<string | null>(() =>
    parsePostOverlay(window.location.hash)
  );
  useEffect(() => {
    const onHash = () => setPostId(parsePostOverlay(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return postId;
}
