import { useQuery } from "@tanstack/react-query";
import { getIngestStatus, type IngestPhase } from "../api/posts";

function phaseBadgeClass(phase: IngestPhase): string {
  if (!phase) return "bg-neutral-100 text-neutral-600";
  if (["logged_out"].includes(phase)) return "bg-red-100 text-red-700";
  if (["throttling_suspected", "storage_low", "paused"].includes(phase))
    return "bg-amber-100 text-amber-700";
  return "bg-green-100 text-green-700";
}

export function IngestPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["ingest", "status"],
    queryFn: getIngestStatus,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="p-8 text-center text-neutral-500">Loading ingest status…</div>
    );
  }

  if (isError || !data) {
    return (
      <div className="p-8 text-center text-red-600">Failed to load ingest status.</div>
    );
  }

  const phase = data.phase ?? "idle";
  const badgeClass = phaseBadgeClass(data.phase);
  const lostCount = data.total_lost;

  return (
    <div className="max-w-xl mx-auto p-6 space-y-6">
      <h1 className="text-xl font-semibold">Ingest Status</h1>

      {/* Phase */}
      <section className="space-y-1">
        <div className="text-xs uppercase tracking-wider text-neutral-500">Current Phase</div>
        <span className={`inline-block px-3 py-1 rounded-full text-sm font-medium ${badgeClass}`}>
          {phase}
        </span>
      </section>

      {/* Counts table */}
      <section className="space-y-1">
        <div className="text-xs uppercase tracking-wider text-neutral-500">Counts</div>
        <table className="w-full text-sm border-collapse">
          <tbody>
            {[
              ["Discovered", data.total_discovered],
              ["Enriched", data.total_enriched],
              ["Lost", data.total_lost],
              ["Placeholder", data.total_placeholder],
              ["Media present", data.total_media_present],
              ["Media failed", data.total_media_failed],
            ].map(([label, value]) => (
              <tr key={label as string} className="border-b border-neutral-100">
                <td className="py-1 text-neutral-600">{label}</td>
                <td className="py-1 text-right font-mono text-neutral-800">{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Timestamps */}
      <section className="space-y-1">
        <div className="text-xs uppercase tracking-wider text-neutral-500">Timestamps</div>
        <dl className="text-sm space-y-0.5">
          {(
            [
              ["Last heartbeat", data.last_heartbeat_at],
              ["Last logged out", data.last_logged_out_at],
              ["Last throttling", data.last_throttling_at],
              ["Last storage low", data.last_storage_low_at],
              ["Last alert", data.last_alert_at],
            ] as [string, string | null][]
          ).map(([label, ts]) => (
            <div key={label} className="flex gap-2">
              <dt className="text-neutral-500 w-36 shrink-0">{label}:</dt>
              <dd className="text-neutral-800 font-mono text-xs break-all">
                {ts ?? "—"}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* Recent bursts placeholder */}
      <section className="space-y-1">
        <div className="text-xs uppercase tracking-wider text-neutral-500">Recent Bursts</div>
        <p className="text-sm text-neutral-400">No recent bursts available yet.</p>
      </section>

      {/* Tombstones link */}
      {lostCount > 0 && (
        <section>
          <a
            href="#/"
            className="text-sm text-blue-600 hover:underline"
          >
            View tombstones ({lostCount}) ↗
          </a>
          <p className="text-xs text-neutral-400 mt-0.5">
            Filter by lost posts in the main grid.
          </p>
        </section>
      )}
    </div>
  );
}
