import { useQuery } from "@tanstack/react-query";

export interface Collection {
  id: string;
  name: string;
  is_all_posts: boolean;
  post_count: number;
  cover_post_id: string | null;
}

export interface CollectionsResponse {
  items: Collection[];
}

export async function fetchCollections(): Promise<CollectionsResponse> {
  const res = await fetch("/api/collections");
  if (!res.ok) throw new Error(`/api/collections ${res.status}`);
  return res.json();
}

export function useCollections() {
  return useQuery({
    queryKey: ["collections"],
    queryFn: fetchCollections,
  });
}
