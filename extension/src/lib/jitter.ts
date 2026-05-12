// Awake-window + burst-pacing helpers (plan §4.3)
// Functions are defined here; they will be called by E3+ schedulers.

export interface JitterConfig {
  awake_window_start_hour: number; // 8  (08:00 local)
  awake_window_end_hour: number; // 1  (01:00 next day, i.e. hour 25 mod 24)
  burst_duration_min_s: number; // 180
  burst_duration_max_s: number; // 900
  inter_burst_gap_min_s: number; // 30 * 60
  inter_burst_gap_max_s: number; // 180 * 60
}

export const DEFAULT_JITTER: JitterConfig = {
  awake_window_start_hour: 8,
  awake_window_end_hour: 1, // 01:00 next-day; treated as 25 when start > end
  burst_duration_min_s: 180,
  burst_duration_max_s: 900,
  inter_burst_gap_min_s: 30 * 60,
  inter_burst_gap_max_s: 180 * 60,
};

/**
 * Returns true if `now` falls within the awake window.
 *
 * Awake window is 08:00 – 01:00 (next day), i.e.:
 *   start_hour=8, end_hour=1 → active when hour >= 8 OR hour < 1
 */
export function isWithinAwakeWindow(now: Date, cfg: JitterConfig): boolean {
  const hour = now.getHours() + now.getMinutes() / 60;
  if (cfg.awake_window_start_hour <= cfg.awake_window_end_hour) {
    // Normal range (e.g., 08:00–17:00): active when start <= hour < end
    return hour >= cfg.awake_window_start_hour && hour < cfg.awake_window_end_hour;
  }
  // Overnight range (e.g., 08:00–01:00 next day): active when hour >= start OR hour < end
  return hour >= cfg.awake_window_start_hour || hour < cfg.awake_window_end_hour;
}

/** Uniform random float in [min, max). */
export function uniform(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

/**
 * Computes the next burst start time.
 * Adds a jittered inter-burst gap to `now`, then checks the awake window.
 * If the computed time falls outside the window, advances to the next
 * window-open boundary.
 */
export function nextBurstAt(now: Date, cfg: JitterConfig): Date {
  const gapMs = uniform(cfg.inter_burst_gap_min_s, cfg.inter_burst_gap_max_s) * 1000;
  const candidate = new Date(now.getTime() + gapMs);

  if (isWithinAwakeWindow(candidate, cfg)) {
    return candidate;
  }

  // Advance to next window open (start_hour on the next or same day)
  const next = new Date(candidate);
  next.setHours(cfg.awake_window_start_hour, 0, 0, 0);
  // If setting the hour puts us in the past (same day, already past that hour)
  // advance one day.
  if (next <= candidate) {
    next.setDate(next.getDate() + 1);
  }
  return next;
}

/** Returns a jittered burst duration in milliseconds. */
export function burstDurationMs(cfg: JitterConfig): number {
  return uniform(cfg.burst_duration_min_s, cfg.burst_duration_max_s) * 1000;
}

/** Awaitable sleep. */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
