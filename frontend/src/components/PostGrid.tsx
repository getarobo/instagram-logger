import type { Post } from "../api/posts";
import { mediaUrl } from "../api/posts";
import { setPostOverlay } from "../lib/hashRoute";

interface Props {
  posts: Post[];
}

export function PostGrid({ posts }: Props) {
  if (posts.length === 0) {
    return (
      <div className="p-8 text-center text-neutral-500">
        No posts yet. Run <code>just login</code> then <code>just sync</code>.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-1">
      {posts.map((post) => {
        const slide = post.slides[0];
        const open = () => setPostOverlay(post.id);
        const onKey = (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        };
        if (!slide) {
          return (
            <button
              key={post.id}
              type="button"
              onClick={open}
              onKeyDown={onKey}
              className="aspect-square bg-neutral-200 flex items-center justify-center text-xs text-neutral-500 hover:bg-neutral-300 focus:outline-none focus:ring-2 focus:ring-neutral-500"
            >
              {post.shortcode}
            </button>
          );
        }
        return (
          <button
            key={post.id}
            type="button"
            onClick={open}
            onKeyDown={onKey}
            className="aspect-square bg-neutral-200 overflow-hidden focus:outline-none focus:ring-2 focus:ring-neutral-500"
          >
            <img
              src={mediaUrl(slide.sha256)}
              alt={post.caption ?? post.shortcode}
              className="w-full h-full object-cover"
              loading="lazy"
            />
          </button>
        );
      })}
    </div>
  );
}
