// Fetch wrapper that adds X-Ingest-Secret header from chrome.storage.local

import { getSecret } from './storage';
import type { ExtensionStateResponse, HeartbeatBody } from './types';

const BACKEND_BASE = 'http://127.0.0.1:8000';

class IngestApi {
  async getState(): Promise<ExtensionStateResponse> {
    return this.fetchJson('GET', '/api/ingest/extension/state') as Promise<ExtensionStateResponse>;
  }

  async heartbeat(body: HeartbeatBody): Promise<{ ok: boolean; phase: string }> {
    return this.fetchJson('POST', '/api/ingest/extension/heartbeat', body) as Promise<{ ok: boolean; phase: string }>;
  }

  async postCollections(
    items: Array<{ id: string; name: string; is_all_posts?: boolean }>,
  ): Promise<{ ok: boolean }> {
    return this.fetchJson('POST', '/api/ingest/extension/collections', items) as Promise<{ ok: boolean }>;
  }

  async postShortcodes(body: {
    source: 'all_posts' | 'collection';
    collection_id?: string;
    items: Array<{
      shortcode: string;
      recency_rank: number;
      thumb_url?: string;
      position?: number;
    }>;
  }): Promise<{ ok: boolean; count: number }> {
    return this.fetchJson('POST', '/api/ingest/extension/shortcodes', body) as Promise<{ ok: boolean; count: number }>;
  }

  async postMembership(
    items: Array<{ shortcode: string; collection_id: string }>,
  ): Promise<{ ok: boolean; upserted: number }> {
    return this.fetchJson('POST', '/api/ingest/extension/membership', items) as Promise<{ ok: boolean; upserted: number }>;
  }

  async postPost(body: {
    shortcode: string;
    outcome: 'enriched' | 'lost';
    [key: string]: unknown;
  }): Promise<{ ok: boolean }> {
    return this.fetchJson('POST', '/api/ingest/extension/post', body) as Promise<{ ok: boolean }>;
  }

  /** Alias used by E4 background enrichment flow. */
  async postPostPayload(body: {
    shortcode: string;
    outcome: 'enriched' | 'lost';
    [key: string]: unknown;
  }): Promise<{ ok: boolean }> {
    return this.postPost(body);
  }

  async postMedia(
    blob: Blob,
    sha: string,
    mime: string,
    postId: string,
    slideIdx: number,
  ): Promise<{ ok: boolean }> {
    const secret = await getSecret();
    if (!secret) throw new Error('No ingest secret configured');
    const fd = new FormData();
    fd.append('file', blob, `${sha}.bin`);
    fd.append('sha256', sha);
    fd.append('mime', mime);
    fd.append('post_id', postId);
    fd.append('slide_idx', String(slideIdx));
    const resp = await fetch(`${BACKEND_BASE}/api/ingest/extension/media`, {
      method: 'POST',
      headers: { 'X-Ingest-Secret': secret },
      body: fd,
    });
    if (!resp.ok) throw new Error(`POST /media → ${resp.status}`);
    return { ok: true };
  }

  async mediaFailed(
    postId: string,
    slideIdx: number,
    attempts: number,
    lastError?: string,
  ): Promise<{ ok: boolean }> {
    return this.fetchJson('POST', '/api/ingest/extension/media-failed', {
      post_id: postId,
      slide_idx: slideIdx,
      attempts,
      last_error: lastError,
    }) as Promise<{ ok: boolean }>;
  }

  async mediaExists(sha: string): Promise<boolean> {
    const secret = await getSecret();
    if (!secret) throw new Error('No ingest secret configured');
    const resp = await fetch(
      `${BACKEND_BASE}/api/ingest/extension/media/exists?sha=${encodeURIComponent(sha)}`,
      {
        method: 'HEAD',
        headers: { 'X-Ingest-Secret': secret },
      },
    );
    if (resp.status === 204) return true;
    if (resp.status === 404) return false;
    throw new Error(`HEAD /media/exists → ${resp.status}`);
  }

  async postResume(): Promise<{ ok: boolean }> {
    return this.fetchJson('POST', '/api/ingest/extension/resume', {}) as Promise<{ ok: boolean }>;
  }

  async postMediaFailed(body: {
    post_id: string;
    slide_idx: number;
    attempts: number;
    last_error?: string;
  }): Promise<{ ok: boolean }> {
    return this.fetchJson(
      'POST',
      '/api/ingest/extension/media-failed',
      body,
    ) as Promise<{ ok: boolean }>;
  }

  private async fetchJson(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<unknown> {
    const secret = await getSecret();
    if (!secret) throw new Error('No ingest secret configured');
    const headers: HeadersInit = { 'X-Ingest-Secret': secret };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const resp = await fetch(`${BACKEND_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status}`);
    return resp.status === 204 ? null : await resp.json();
  }
}

export const api = new IngestApi();
