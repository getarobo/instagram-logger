import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import Lightbox from "yet-another-react-lightbox";
import Counter from "yet-another-react-lightbox/plugins/counter";
import Thumbnails from "yet-another-react-lightbox/plugins/thumbnails";
import Video from "yet-another-react-lightbox/plugins/video";
import "yet-another-react-lightbox/styles.css";
import "yet-another-react-lightbox/plugins/counter.css";
import "yet-another-react-lightbox/plugins/thumbnails.css";

import {
  fetchPost,
  mediaUrl,
  type PostDetail,
  type Slide,
} from "../api/posts";
import { setPostOverlay } from "../lib/hashRoute";

interface Props {
  postId: string;
}

function slideToYarl(slide: Slide) {
  if (slide.media_type === "video") {
    return {
      type: "video" as const,
      width: slide.width ?? 1080,
      height: slide.height ?? 1080,
      poster: slide.thumbnail_sha256
        ? mediaUrl(slide.thumbnail_sha256)
        : undefined,
      sources: [{ src: mediaUrl(slide.sha256), type: "video/mp4" }],
    };
  }
  return {
    type: "image" as const,
    src: mediaUrl(slide.sha256),
    width: slide.width ?? undefined,
    height: slide.height ?? undefined,
  };
}

function PostFooter({ post }: { post: PostDetail }) {
  const saved = post.saved_at ?? post.first_seen_at;
  return (
    <div className="absolute bottom-0 inset-x-0 bg-black/70 text-white p-4 text-sm space-y-1 pointer-events-auto z-[1100] max-h-[40vh] overflow-y-auto">
      <div className="flex items-center gap-2">
        <span className="font-semibold">@{post.author.username}</span>
        <span className="text-white/60 text-xs">saved {saved}</span>
        <a
          href={`https://www.instagram.com/p/${post.shortcode}/`}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-xs text-white/60 hover:text-white underline"
        >
          open on IG
        </a>
      </div>
      {post.caption && (
        <div className="whitespace-pre-wrap text-white/90">{post.caption}</div>
      )}
      {post.collections.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {post.collections.map((c) => (
            <span
              key={c.id}
              className="px-2 py-0.5 rounded-full bg-white/15 text-xs"
            >
              {c.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function PostModal({ postId }: Props) {
  const close = () => setPostOverlay(null);
  const detail = useQuery({
    queryKey: ["post", postId],
    queryFn: () => fetchPost(postId),
  });

  // 404 from a stale hash (deleted post, etc) -> close cleanly so the
  // user lands back on the grid instead of staring at a stuck spinner.
  useEffect(() => {
    if (detail.isError && String(detail.error ?? "").includes(" 404")) {
      close();
    }
  }, [detail.isError, detail.error]);

  if (!detail.data) {
    return (
      <Lightbox
        open
        close={close}
        slides={[]}
        render={{
          slide: () => (
            <div className="text-white p-8">
              {detail.isError ? "Failed to load post." : "Loading…"}
            </div>
          ),
        }}
      />
    );
  }

  const post = detail.data;
  const yarlSlides = post.slides.map(slideToYarl);
  const showThumbs = post.slides.length > 1;
  const plugins = [Counter, Video, ...(showThumbs ? [Thumbnails] : [])];

  return (
    <Lightbox
      open
      close={close}
      slides={yarlSlides}
      plugins={plugins}
      counter={{ container: { style: { top: 0, bottom: "unset" } } }}
      render={{
        slideFooter: () => <PostFooter post={post} />,
      }}
    />
  );
}
