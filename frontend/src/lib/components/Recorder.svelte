<script lang="ts">
  // Mic capture for the reference clip (Clone, record path). On stop, emits the
  // recorded Blob. Mic-permission denial is surfaced via onerror so the Clone
  // screen can offer "Upload a file instead" (voice-cloning EDGE-6).
  let {
    onrecorded,
    onerror,
  }: { onrecorded?: (blob: Blob) => void; onerror?: (message: string) => void } = $props();

  import { formatDuration as fmt } from "$lib/format";

  let recording = $state(false);
  let elapsed = $state(0);
  let level = $state(0);

  let recorder: MediaRecorder | null = null;
  let stream: MediaStream | null = null;
  let chunks: Blob[] = [];
  let timer: ReturnType<typeof setInterval> | null = null;
  let raf = 0;
  let analyser: AnalyserNode | null = null;
  let audioCtx: AudioContext | null = null;

  function meter() {
    if (!analyser) return;
    const buf = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
    level = Math.min(1, peak / 96);
    raf = requestAnimationFrame(meter);
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      onerror?.("Parrot can't access your microphone. Allow mic access in your OS settings.");
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      onrecorded?.(new Blob(chunks, { type: recorder?.mimeType || "audio/webm" }));
      cleanup();
    };
    recorder.start();
    recording = true;
    elapsed = 0;
    timer = setInterval(() => (elapsed += 1), 1000);

    audioCtx = new AudioContext();
    const srcNode = audioCtx.createMediaStreamSource(stream);
    analyser = audioCtx.createAnalyser();
    srcNode.connect(analyser);
    meter();
  }

  function stop() {
    recording = false;
    recorder?.stop();
  }

  function cleanup() {
    if (timer) clearInterval(timer);
    cancelAnimationFrame(raf);
    stream?.getTracks().forEach((t) => t.stop());
    audioCtx?.close();
    analyser = null;
    audioCtx = null;
    level = 0;
  }
</script>

<div class="flex flex-col items-center gap-3">
  <button
    type="button"
    class="flex h-16 w-16 items-center justify-center rounded-full bg-action-blue text-2xl text-snow-white transition hover:brightness-105 {recording ? 'parrot-pulse' : ''}"
    onclick={() => (recording ? stop() : start())}
    aria-label={recording ? "Stop recording" : "Start recording"}
  >
    <span aria-hidden="true">{recording ? "■" : "🎙"}</span>
  </button>
  {#if recording}
    <span class="font-mono text-body text-slate-blue">{fmt(elapsed)}</span>
    <div class="h-2 w-40 overflow-hidden rounded-full bg-pale-gray">
      <div class="h-full rounded-full bg-action-blue" style="width:{level * 100}%"></div>
    </div>
  {:else}
    <span class="text-body text-slate-blue">Tap to record — 3–10s is the sweet spot.</span>
  {/if}
</div>
