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
  author: Author;
  slides: Slide[];
}

export interface PostDetail extends Post {
  collections: PostCollectionRef[];
}

export interface PostsResponse {
  items: Post[];
  next_cursor: string | null;
}

export interface AuthStatus {
  state: "NEEDS_FIRST_LOGIN" | "CHALLENGE_PENDING" | "LOGGED_IN" | "SESSION_EXPIRED";
  challenge_kind: "sms" | "email" | "totp" | null;
  last_error: string | null;
}

export async function fetchPosts(collectionId?: string | null): Promise<PostsResponse> {
  const url = collectionId
    ? `/api/posts?collection_id=${encodeURIComponent(collectionId)}`
    : "/api/posts";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const res = await fetch("/api/auth/status");
  if (!res.ok) throw new Error(`/api/auth/status ${res.status}`);
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
