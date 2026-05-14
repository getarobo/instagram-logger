import type { Post } from "../api/posts";
import { PostTile } from "./PostTile";

interface Props {
  posts: Post[];
}

export function PostGrid({ posts }: Props) {
  if (posts.length === 0) {
    return (
      <div className="p-8 text-center text-neutral-500">
        No posts yet. The extension will populate posts as it runs.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-1">
      {posts.map((post) => (
        <PostTile key={post.id} post={post} />
      ))}
    </div>
  );
}
