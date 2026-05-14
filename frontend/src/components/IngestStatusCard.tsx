import { useQuery } from "@tanstack/react-query";
import { getIngestStatus, type IngestPhase } from "../api/posts";

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
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

function withinDay(iso: string | null): boolean {
  if (!iso) return false;
  return Date.now() - new Date(iso).getTime() < 24 * 60 * 60 * 1000;
}

function phaseBadgeClass(phase: IngestPhase): string {
  if (!phase) return "bg-neutral-100 text-neutral-600";
  if (["logged_out"].includes(phase)) return "bg-red-100 text-red-700";
  if (["throttling_suspected", "storage_low", "paused"].includes(phase))
    return "bg-amber-100 text-amber-700";
  return "bg-green-100 text-green-700";
}

export function IngestStatusCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["ingest", "status"],
    queryFn: getIngestStatus,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="mx-4 my-2 px-4 py-3 rounded border border-neutral-200 bg-neutral-50 text-sm text-neutral-500 animate-pulse">
        Loading ingest status…
      </div>
    );
  }

  if (isError || !data) {
    return null;
  }

  const phase = data.phase ?? "idle";
  const badgeClass = phaseBadgeClass(data.phase);

  return (
    <div className="mx-4 my-2 rounded border border-neutral-200 bg-neutral-50 text-sm">
      {/* Alert banners */}
      {withinDay(data.last_logged_out_at) && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-red-800 text-xs rounded-t">
          Instagram session expired — log in via Chrome to resume
        </div>
      )}
      {withinDay(data.last_throttling_at) && !withinDay(data.last_logged_out_at) && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-amber-800 text-xs rounded-t">
          Possible throttling detected
        </div>
      )}
      {withinDay(data.last_storage_low_at) && (
        <div className="px-4 py-2 bg-orange-50 border-b border-orange-200 text-orange-800 text-xs">
          Media disk usage near cap
        </div>
      )}

      {/* Main content */}
      <div className="px-4 py-3 space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-neutral-500">Phase:</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badgeClass}`}>
            {phase}
          </span>
        </div>

        <div className="text-neutral-700">
          {data.total_enriched} / {data.total_discovered} enriched
        </div>

        <div className="text-neutral-500 text-xs">
          {data.total_placeholder} placeholder · {data.total_lost} lost
        </div>

        {data.last_heartbeat_at && (
          <div className="text-neutral-400 text-xs">
            Last heartbeat: {relativeTime(data.last_heartbeat_at)}
          </div>
        )}
      </div>
    </div>
  );
}
