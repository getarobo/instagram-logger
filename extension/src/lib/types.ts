// Shared types matching backend contracts (backend/api/ingest_extension.py + ingest_state.py)

export type Phase =
  | 'idle'
  | 'discovery_all'
  | 'discovery_collections'
  | 'enrichment'
  | 'watch'
  | 'paused'
  | 'logged_out'
  | 'throttling_suspected'
  | 'storage_low';

// GET /api/ingest/extension/state response
export interface ExtensionStateResponse {
  phase_suggestion: Phase;
  total_discovered: number;
  total_enriched: number;
  total_lost: number;
  total_placeholder: number;
  collections_known: CollectionInfo[];
  next_enrichment_target?: { shortcode: string } | null;
  next_retry_target?: { shortcode: string; reason: string } | null;
  priority_target?: { shortcode: string; reason: string } | null;
  last_heartbeat_at?: string | null;
  last_phase?: string | null;
  last_logged_out_at?: string | null;
}

export interface CollectionInfo {
  id: string;
  name: string;
  is_all_posts?: boolean;
  last_seen_at: string;
}

// POST /api/ingest/extension/heartbeat body
export interface HeartbeatBody {
  state:
    | 'ok'
    | 'logged_out'
    | 'throttling_suspected'
    | 'storage_low'
    | 'selectors_broken'
    | 'extraction_failed';
  phase?: Phase;
  burst?: {
    id: string;
    started_at: string;
    closed_at: string;
    posts_seen: number;
    media_uploaded: number;
  };
  metrics?: BurstMetricsPayload;
  last_error?: string;
}

// Metrics payload sent in heartbeat (R6)
export interface BurstMetricsPayload {
  hydration_p50_ms: number;
  http_4xx_rate: number;
  login_redirects: number;
}

// chrome.storage.local tab ownership entry (consensus Δ4)
export interface OwnedTab {
  tabId: number;
  role: 'saved-grid' | 'post-detail' | 'collection';
  createdAt: string;
}

// Per-burst metrics stored in chrome.storage.local (consensus R6)
export interface BurstMetrics {
  burst_id: string;
  closed_at: string;
  hydration_p50_ms: number;
  http_4xx_rate: number;
  login_redirects: number;
  posts_seen: number;
  media_uploaded: number;
}

// Heartbeat metrics payload for alert states (R6 / E5)
// Subset of BurstMetrics — just the 3 numbers sent in the heartbeat body.
export interface HeartbeatMetricsPayload {
  hydration_p50_ms: number;
  http_4xx_rate: number;
  login_redirects: number;
}

// Resume cursor stored in chrome.storage.local (plan §4.8)
export interface ResumeCursor {
  discovery_all: { last_recency_rank: number | null; scroll_y: number };
  discovery_collections: {
    current_collection_id: string | null;
    last_recency_rank: number | null;
  };
  enrichment: { last_shortcode_enriched: string | null };
}
