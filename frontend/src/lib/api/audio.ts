import { apiFetch } from "./client";

/** Transcode in-memory WAV bytes to MP3 via the stateless sidecar endpoint
 *  (POST /audio/mp3). Used to export a fresh result that may have no history row
 *  (e.g. the user cleared History). Throws ApiError on failure. */
export async function transcodeWavToMp3(wav: Uint8Array) {
  // Hand fetch a concrete ArrayBuffer slice — a generic Uint8Array<ArrayBufferLike>
  // isn't assignable to BodyInit under TS's stricter typed-array generics.
  const body = wav.buffer.slice(wav.byteOffset, wav.byteOffset + wav.byteLength) as ArrayBuffer;
  const res = await apiFetch("/audio/mp3", {
    method: "POST",
    body,
    headers: { "Content-Type": "audio/wav" },
  });
  return new Uint8Array(await res.arrayBuffer());
}
