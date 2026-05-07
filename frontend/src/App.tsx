import { useQuery } from "@tanstack/react-query";
import { fetchAuthStatus, fetchPosts } from "./api/posts";
import { useCollections } from "./api/collections";
import { PostGrid } from "./components/PostGrid";
import { CollectionList } from "./components/CollectionList";
import { PostModal } from "./components/PostModal";
import { useRoute, usePostOverlay } from "./lib/hashRoute";

export default function App() {
  const route = useRoute();
  const overlayPostId = usePostOverlay();
  const auth = useQuery({ queryKey: ["auth", "status"], queryFn: fetchAuthStatus });

  const collectionId = route.kind === "collection" ? route.id : null;
  const collections = useCollections();
  const activeName =
    route.kind === "collection"
      ? collections.data?.items.find((c) => c.id === route.id)?.name ?? route.id
      : "All";

  // SESSION_EXPIRED still has a populated archive — let the grid render
  // so the user can browse historical posts while they re-import a session.
  const authState = auth.data?.state;
  const postsEnabled =
    authState === "LOGGED_IN" ||
    authState === "NEEDS_FIRST_LOGIN" ||
    authState === "SESSION_EXPIRED";

  const posts = useQuery({
    queryKey: ["posts", { collection_id: collectionId }],
    queryFn: () => fetchPosts(collectionId),
    enabled: postsEnabled,
  });

  const showFirstLogin = authState === "NEEDS_FIRST_LOGIN";
  const showSessionExpired = authState === "SESSION_EXPIRED";

  return (
    <div className="min-h-full grid grid-cols-[200px_1fr]">
      <aside className="border-r border-neutral-200 py-3 sticky top-0 self-start">
        <div className="px-4 pb-2 text-xs uppercase tracking-wider text-neutral-500">
          Collections
        </div>
        <CollectionList active={route} />
      </aside>

      <div>
        <header className="px-4 py-3 border-b border-neutral-200 flex items-center justify-between">
          <h1 className="text-lg font-semibold">{activeName}</h1>
          <div className="text-xs text-neutral-500">{authState ?? "…"}</div>
        </header>
        {showSessionExpired && (
          <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-sm text-amber-900">
            Instagram session expired — automated sync is paused. Run{" "}
            <code className="px-1 bg-amber-100 rounded">just import-session</code>{" "}
            to paste a fresh sessionid from your browser, or{" "}
            <code className="px-1 bg-amber-100 rounded">just login</code> if your
            password flow works. Cached posts below remain browsable.
          </div>
        )}
        <main>
          {auth.isLoading ? (
            <div className="p-8 text-center text-neutral-500">Loading…</div>
          ) : showFirstLogin ? (
            <div className="p-8 text-center text-neutral-600 space-y-2">
              <p>No Instagram session yet.</p>
              <p>
                Recommended: run <code>just import-session</code> (paste a
                sessionid cookie from a logged-in instagram.com tab), then{" "}
                <code>just sync</code>.
              </p>
              <p className="text-sm text-neutral-500">
                Or <code>just login</code> for password-based login if your
                device isn't flagged. Or <code>just sync-fake</code> for the
                offline fixture.
              </p>
            </div>
          ) : posts.isLoading ? (
            <div className="p-8 text-center text-neutral-500">Loading posts…</div>
          ) : posts.isError ? (
            <div className="p-8 text-center text-red-600">
              Failed to load posts: {String(posts.error)}
            </div>
          ) : (
            <PostGrid posts={posts.data?.items ?? []} />
          )}
        </main>
      </div>
      {overlayPostId && <PostModal postId={overlayPostId} />}
    </div>
  );
}
