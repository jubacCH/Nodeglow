/** Latency history helpers (pure, tested). */

export interface HistoryPoint { timestamp: string; success: boolean; latency_ms: number | null }

export interface Point { t: number; ms: number | null; ok: boolean }

/**
 * Sorted points with explicit gaps: where two checks are further apart than
 * 3× the usual interval, a null is inserted so the line breaks instead of
 * bridging a period without data.
 */
export function withGaps(results: HistoryPoint[]): Point[] {
  const pts = results
    .map((r) => ({ t: Date.parse(r.timestamp), ms: r.latency_ms, ok: r.success }))
    .filter((p) => Number.isFinite(p.t))
    .sort((a, b) => a.t - b.t);
  if (pts.length < 3) return pts;
  const deltas = pts.slice(1).map((p, i) => p.t - pts[i].t).sort((a, b) => a - b);
  const median = deltas[Math.floor(deltas.length / 2)] || 60_000;
  const limit = Math.max(median * 3, 180_000);
  const out: Point[] = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (pts[i].t - pts[i - 1].t > limit) out.push({ t: pts[i - 1].t + 1, ms: null, ok: true });
    out.push(pts[i]);
  }
  return out;
}
