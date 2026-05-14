import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Post } from "../api/posts";
import { mediaUrl, retryPage, retryMedia } from "../api/posts";
import { setPostOverlay } from "../lib/hashRoute";

interface Props {
  post: Post;
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export function PostTile({ post }: Props) {
  const queryClient = useQueryClient();
  const [retryQueued, setRetryQueued] = useState(false);

  const open = () => setPostOverlay(post.id);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  const handleRetryPage = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRetryQueued(true);
    try {
      await retryPage(post.id);
      await queryClient.invalidateQueries({ queryKey: ["posts"] });
    } finally {
      setRetryQueued(false);
    }
  };

  const handleRetryMedia = async (e: React.MouseEvent) => {
    e.stopPropagation();
    // TODO: API doesn't expose per-slide failed state; retry slide 0.
    // Backend retry-media is idempotent if it's already present.
    const slideIdx = 0;
    setRetryQueued(true);
    try {
      await retryMedia(post.id, slideIdx);
      await queryClient.invalidateQueries({ queryKey: ["posts"] });
    } finally {
      setRetryQueued(false);
    }
  };

  const retryBtn = (handler: (e: React.MouseEvent) => void) => (
    <button
      type="button"
      onClick={handler}
      disabled={retryQueued}
      aria-label={retryQueued ? "Retry queued, please wait" : "Retry"}
      className="absolute bottom-1 right-1 px-2 py-0.5 rounded text-xs font-medium bg-white/90 text-neutral-800 hover:bg-white disabled:opacity-60 shadow"
    >
      {retryQueued ? "Queued…" : "Retry"}
    </button>
  );

  // --- placeholder (skeleton) state ---
  if (post.state === "placeholder") {
    return (
      <div className="aspect-square bg-neutral-200 animate-pulse relative" />
    );
  }

  // --- lost (tombstone) state ---
  if (post.state === "lost") {
    const lastSeen = post.saved_at ?? post.first_seen_at;
    return (
      <div className="aspect-square bg-neutral-300 flex flex-col items-center justify-center gap-1 text-center p-2 relative">
        <span className="text-xs font-medium text-neutral-600 truncate max-w-full">
          @{post.author.username}
        </span>
        <span className="text-xs text-neutral-500">
          Last seen: {relativeTime(lastSeen)}
        </span>
        {retryBtn(handleRetryPage)}
      </div>
    );
  }

  // --- enriched state ---
  const slide = post.slides[0];

  // enriched, no slides loaded yet (slides_total set but none present)
  if (!slide) {
    const hasFailed = post.slides_failed > 0;
    return (
      <div className="aspect-square bg-neutral-200 flex items-center justify-center text-xs text-neutral-500 relative">
        {post.shortcode}
        {hasFailed && retryBtn(handleRetryMedia)}
      </div>
    );
  }

  // enriched with slides — show first slide, overlay broken-image badge for failures
  const hasFailed = post.slides_failed > 0;
  return (
    <button
      type="button"
      onClick={open}
      onKeyDown={onKey}
      className="aspect-square bg-neutral-200 overflow-hidden focus:outline-none focus:ring-2 focus:ring-neutral-500 relative"
    >
      <img
        src={mediaUrl(slide.sha256)}
        alt={post.caption ?? post.shortcode}
        className="w-full h-full object-cover"
        loading="lazy"
      />
      {hasFailed && (
        <div className="absolute inset-0 bg-black/40 flex flex-col items-center justify-center gap-1">
          <span className="text-white text-xs font-medium">
            {post.slides_failed} slide{post.slides_failed > 1 ? "s" : ""} failed
          </span>
          {retryBtn(handleRetryMedia)}
        </div>
      )}
    </button>
  );
}
