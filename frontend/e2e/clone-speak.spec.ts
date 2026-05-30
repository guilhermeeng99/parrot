import { type Page, type Route, expect, test } from "@playwright/test";

// The clone→speak happy path (CLAUDE.md Testing Rules), end to end against the real
// built SPA. Outside the Tauri webview the app boots straight past the splash/setup
// gate to the Clone screen and talks to the sidecar over http://127.0.0.1:3900, so
// we mock ONLY that HTTP boundary — no Tauri IPC stubbing required.

const SIDECAR = "http://127.0.0.1:3900";
// Cross-origin (preview :4173 → sidecar :3900): every fulfilled response must carry
// CORS or the page's fetch/EventSource rejects.
const CORS = { "access-control-allow-origin": "*" };

/** A minimal valid 44-byte WAV (RIFF/WAVE/fmt /data, zero samples). The bytes are
 *  never parsed by the app (generateSpeech just wraps them in a Blob), but a real
 *  header keeps any incidental `<audio>` probe happy. */
function tinyWav(): Buffer {
  const buf = Buffer.alloc(44);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36, 4); // file size - 8
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16); // fmt chunk size
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(24000, 24); // sample rate
  buf.writeUInt32LE(48000, 28); // byte rate
  buf.writeUInt16LE(2, 32); // block align
  buf.writeUInt16LE(16, 34); // bits per sample
  buf.write("data", 36);
  buf.writeUInt32LE(0, 40); // data size
  return buf;
}

/** Mock the sidecar REST surface the clone→speak path touches. Stateful for
 *  `/profiles`: a created profile shows up in the subsequent list fetch. */
async function mockSidecar(page: Page): Promise<void> {
  const profilesList: unknown[] = [];

  await page.route(`${SIDECAR}/**`, async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        headers: { ...CORS, "content-type": "application/json" },
        body: JSON.stringify(body),
      });

    if (method === "OPTIONS") {
      return route.fulfill({
        status: 204,
        headers: { ...CORS, "access-control-allow-methods": "*", "access-control-allow-headers": "*" },
      });
    }

    // First-run/setup + ancillary status calls — everything ready, nothing to download.
    if (path === "/setup/status") {
      return json({
        models_ready: true,
        missing: [],
        hf_cache_dir: "C:/Users/test/.cache/huggingface",
        disk_free_gb: 100,
        min_free_gb: 10,
        enough_disk: true,
      });
    }
    if (path === "/transcribe/status") {
      // No model downloaded → auto-transcribe no-ops, so the clone path needs no
      // /transcribe call (ref_text stays empty, which is allowed).
      return json({
        models: [{ id: "large-v3", label: "Large v3", size_mb: 3100, downloaded: false }],
        default_model: "large-v3",
        device: "cpu",
        gpu: false,
      });
    }
    if (path === "/engine/status") return json({ active: "omnivoice", device: "cpu" });
    if (path === "/history") return json([]);
    if (path.startsWith("/settings/hf-token")) {
      return json({ source: "app", set: false, masked: null, whoami_user: null, whoami_ok: false });
    }
    if (path === "/generate/progress-stream") {
      return route.fulfill({
        status: 200,
        headers: { ...CORS, "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: ": keepalive\n\n",
      });
    }

    // Profiles — list reflects what's been created this session.
    if (path === "/profiles" && method === "GET") return json(profilesList);
    if (path === "/profiles" && method === "POST") {
      const profile = {
        id: "vp_test_1",
        name: "Test Voice",
        ref_audio_path: "voices/ref.wav",
        ref_text: "",
        language: "Auto",
        instruct: "",
        locked_audio_path: "",
        seed: null,
        is_locked: 0,
        created_at: 1_700_000_000,
      };
      profilesList.push(profile);
      return json(profile);
    }

    // Synthesis — return a WAV body + the X-* metadata headers generateSpeech reads.
    if (path === "/generate" && method === "POST") {
      return route.fulfill({
        status: 200,
        headers: {
          ...CORS,
          "content-type": "audio/wav",
          "x-audio-id": "gen_test_1",
          "x-audio-path": "outputs/gen_test_1.wav",
          "x-gen-time": "1.2",
          "x-audio-duration": "3.4",
          "x-seed": "42",
        },
        body: tinyWav(),
      });
    }

    // Permissive fallback (e.g. an <audio> element fetching a profile/history clip):
    // a 200 keeps the UI happy; the bytes are irrelevant to this flow.
    return route.fulfill({ status: 200, headers: { ...CORS, "content-type": "audio/wav" }, body: tinyWav() });
  });
}

test("clone a voice from an uploaded clip, then make it speak", async ({ page }) => {
  await mockSidecar(page);
  await page.goto("/");

  // Boots past the splash + setup gate to the Clone screen.
  await expect(page.getByRole("heading", { name: "Clone a voice" })).toBeVisible();

  // Upload a reference clip (Upload tab → hidden file input).
  await page.getByRole("button", { name: "Upload" }).click();
  await page
    .locator('input[type="file"]')
    .setInputFiles({ name: "ref.wav", mimeType: "audio/wav", buffer: tinyWav() });

  // The capture form appears; name the voice and save.
  await page.getByPlaceholder("e.g. My narration voice").fill("Test Voice");
  await page.getByRole("button", { name: "Save voice" }).click();

  // The new voice lands in "Your voices" (VoiceCard exposes an "Open <name>" button).
  await expect(page.getByRole("button", { name: "Open Test Voice" })).toBeVisible();

  // Switch to the Speak screen via the header nav.
  await page.locator("header").getByRole("button", { name: "Speak" }).click();
  await expect(page.getByRole("heading", { name: "Speak", exact: true })).toBeVisible();

  // Type text and synthesize.
  await page.getByPlaceholder("Type anything for your voice to say…").fill("Hello from Parrot.");
  await page.locator("main").getByRole("button", { name: "Speak", exact: true }).click();

  // The result card renders the synthesized clip.
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
});
