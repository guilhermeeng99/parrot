/** Format a duration in seconds as `m:ss` (e.g. 75 → "1:15").
 *
 *  Non-finite or negative input clamps to "0:00" — a probe `<audio>` element
 *  reports `NaN` for duration before its metadata loads. Shared by AudioPlayer
 *  and Recorder so the seconds→`m:ss` format lives in exactly one place. */
export function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const r = Math.floor(seconds % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
}
