export interface Slide {
  sha256: string;
  thumbnail_sha256: string | null;
  media_type: "image" | "video";
  carousel_index: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
}

export interface Author {
  id: string;
  username: string;
  full_name: string | null;
  is_private: boolean;
  profile_pic_url: string | null;
}

export interface PostCollectionRef {
  id: string;
  name: string;
  is_all_posts: boolean;
}

export interface Post {
  id: string;
  shortcode: string;
  caption: string | null;
  media_kind: "image" | "video" | "carousel";
  taken_at: string | null;
  saved_at: string | null;
  first_seen_at: string;
  last_seen_in_saved_at: string;
  is_unsaved: boolean;
  is_source_deleted: boolean;
  state: "placeholder" | "enriched" | "lost";
  slides_total: number;
  slides_present: number;
  slides_failed: number;
  author: Author;
  slides: Slide[];
}

export type IngestPhase =
  | "watch"
  | "discovery_all"
  | "discovery_collections"
  | "enrichment"
  | "idle"
  | "logged_out"
  | "throttling_suspected"
  | "storage_low"
  | "paused"
  | null;

export interface IngestStatus {
  phase: IngestPhase;
  last_heartbeat_at: string | null;
  last_logged_out_at: string | null;
  last_throttling_at: string | null;
  last_storage_low_at: string | null;
  last_alert_at: string | null;
  total_discovered: number;
  total_enriched: number;
  total_lost: number;
  total_placeholder: number;
  total_media_present: number;
  total_media_failed: number;
}

export interface PostDetail extends Post {
  collections: PostCollectionRef[];
}

export interface PostsResponse {
  items: Post[];
  next_cursor: string | null;
}

export async function getIngestStatus(): Promise<IngestStatus> {
  const res = await fetch("/api/ingest/status");
  if (!res.ok) throw new Error(`/api/ingest/status ${res.status}`);
  return res.json();
}

export async function retryPage(postId: string): Promise<{ ok: boolean }> {
  const res = await fetch(`/api/posts/${encodeURIComponent(postId)}/retry-page`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`retry-page ${res.status}`);
  return res.json();
}

export async function retryMedia(
  postId: string,
  slideIdx: number
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `/api/posts/${encodeURIComponent(postId)}/retry-media/${slideIdx}`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`retry-media ${res.status}`);
  return res.json();
}

export async function fetchPosts(collectionId?: string | null): Promise<PostsResponse> {
  const url = collectionId
    ? `/api/posts?collection_id=${encodeURIComponent(collectionId)}`
    : "/api/posts";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

export async function fetchPost(postId: string): Promise<PostDetail> {
  const url = `/api/posts/${encodeURIComponent(postId)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

export function mediaUrl(sha: string): string {
  return `/api/media/${sha}`;
}
