import React from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPosts } from "./api/posts";
import { useCollections } from "./api/collections";
import { PostGrid } from "./components/PostGrid";
import { CollectionList } from "./components/CollectionList";
import { PostModal } from "./components/PostModal";
import { IngestStatusCard } from "./components/IngestStatusCard";
import { IngestPage } from "./routes/IngestPage";
import { useRoute, usePostOverlay } from "./lib/hashRoute";

function useHashStartsWith(prefix: string): boolean {
  const [v, setV] = React.useState(() => window.location.hash.startsWith(prefix));
  React.useEffect(() => {
    const onChange = () => setV(window.location.hash.startsWith(prefix));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [prefix]);
  return v;
}

export default function App() {
  const route = useRoute();
  const overlayPostId = usePostOverlay();

  const collectionId = route.kind === "collection" ? route.id : null;
  const collections = useCollections();
  const activeName =
    route.kind === "collection"
      ? (collections.data?.items.find((c) => c.id === route.id)?.name ?? route.id)
      : "All";

  const posts = useQuery({
    queryKey: ["posts", { collection_id: collectionId }],
    queryFn: () => fetchPosts(collectionId),
  });

  const isIngestRoute = useHashStartsWith("#/ingest");

  return (
    <div className="min-h-full grid grid-cols-[200px_1fr]">
      <aside className="border-r border-neutral-200 py-3 sticky top-0 self-start">
        <div className="px-4 pb-2 text-xs uppercase tracking-wider text-neutral-500">
          Collections
        </div>
        <CollectionList active={route} />
        <div className="px-4 pt-4 border-t border-neutral-100 mt-3">
          <a
            href="#/ingest"
            className="block text-xs text-neutral-500 hover:text-neutral-800 py-1"
          >
            Ingest status
          </a>
        </div>
      </aside>

      <div>
        <header className="px-4 py-3 border-b border-neutral-200 flex items-center justify-between">
          <h1 className="text-lg font-semibold">
            {isIngestRoute ? "Ingest" : activeName}
          </h1>
        </header>

        {isIngestRoute ? (
          <IngestPage />
        ) : (
          <>
            <IngestStatusCard />
            <main>
              {posts.isLoading ? (
                <div className="p-8 text-center text-neutral-500">Loading posts…</div>
              ) : posts.isError ? (
                <div className="p-8 text-center text-red-600">
                  Failed to load posts: {String(posts.error)}
                </div>
              ) : (
                <PostGrid posts={posts.data?.items ?? []} />
              )}
            </main>
          </>
        )}
      </div>
      {overlayPostId && <PostModal postId={overlayPostId} />}
    </div>
  );
}
