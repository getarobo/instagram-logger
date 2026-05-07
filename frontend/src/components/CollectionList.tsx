import { useCollections } from "../api/collections";
import type { Route } from "../lib/hashRoute";

interface Props {
  active: Route;
}

export function CollectionList({ active }: Props) {
  const { data, isLoading, isError } = useCollections();

  if (isLoading) {
    return <div className="px-4 py-2 text-xs text-neutral-500">Loading…</div>;
  }
  if (isError || !data) {
    return <div className="px-4 py-2 text-xs text-red-600">Failed to load collections</div>;
  }

  const items = data.items;

  const isActive = (id: string | null) => {
    if (active.kind === "all") return id === null || id === "all_posts";
    return active.kind === "collection" && active.id === id;
  };

  return (
    <nav className="flex flex-col gap-px text-sm">
      <a
        href="#/"
        className={
          "px-4 py-2 hover:bg-neutral-100 " +
          (isActive(null) ? "font-semibold bg-neutral-100" : "")
        }
      >
        All
      </a>
      {items.map((c) => {
        const id = c.is_all_posts ? null : c.id;
        const href = c.is_all_posts ? "#/" : `#/collections/${encodeURIComponent(c.id)}`;
        if (c.is_all_posts) return null; // already rendered as "All"
        return (
          <a
            key={c.id}
            href={href}
            className={
              "px-4 py-2 hover:bg-neutral-100 flex items-center justify-between " +
              (isActive(id) ? "font-semibold bg-neutral-100" : "")
            }
          >
            <span>{c.name}</span>
            <span className="text-xs text-neutral-500">{c.post_count}</span>
          </a>
        );
      })}
    </nav>
  );
}
