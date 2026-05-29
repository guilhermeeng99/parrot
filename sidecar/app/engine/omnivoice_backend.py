"""Adapter over the Apache-2.0 `omnivoice` model lib (PyPI: ``omnivoice``, k2-fsa).

`model_manager` constructs exactly one of these and calls `.synthesize(...)`. The
adapter owns the real `torch`/`omnivoice` imports, device placement, the voice-
clone conditioning step (voice-cloning.md §3), seeding, and converting the
model's output into the mono float32 [-1, 1] numpy array the rest of the sidecar
expects at `self.sampling_rate` (24 kHz).

This file is the genuinely-unverifiable ML boundary: it runs only in a build with
the `engine` extra (which carries `omnivoice`) + downloaded weights, never in the
mocked test venv. The `synthesize(...)` signature below is the *contract* the rest
of Parrot is built and tested against; here it is mapped onto the real `omnivoice`
0.1.x surface, which was reconciled against the installed lib at integration time:

  - construction:   ``OmniVoice.from_pretrained(repo, dtype=...).to(device)``
  - voice clone:    ``model.create_voice_clone_prompt(ref_audio, ref_text)``
  - synthesis:      ``model.generate(text, voice_clone_prompt=..., generation_config=...)``
                    returns ``list[np.ndarray]`` (one mono 24 kHz waveform per text)
  - advanced knobs: live on ``OmniVoiceGenerationConfig`` (num_step, guidance_scale,
                    t_shift, layer_penalty_factor, position_temperature,
                    class_temperature, denoise, postprocess_output), NOT on generate().
"""

import logging
from collections.abc import Callable
from contextlib import contextmanager

import numpy as np

from .. import config

log = logging.getLogger(__name__)


class OmniVoiceBackend:
    def __init__(self, device: str):
        import torch  # type: ignore
        from omnivoice import OmniVoice  # type: ignore

        self._torch = torch
        self.device = device
        # float16 halves memory on CUDA; CPU stays float32 (half is slow/partially
        # unsupported for CPU ops). Weights resolve from the HF cache populated on
        # first run — HF_HOME/HF_HUB_CACHE are set in config.prepare_environment()
        # before any huggingface import, so from_pretrained loads offline.
        dtype = torch.float16 if device == "cuda" else torch.float32
        model = OmniVoice.from_pretrained(config.DEFAULT_MODEL_REPO, dtype=dtype)
        self._model = model.to(device).eval()
        self.sampling_rate = int(getattr(self._model, "sampling_rate", 24000))
        self._prompt_cache: dict[str, object] = {}

    def _prompt(self, ref_audio_path: str | None, ref_text: str | None):
        """Build (and cache) the reusable voice-clone prompt for a reference.

        `ref_text` is forwarded as None when empty: omnivoice falls back to ASR
        auto-transcription in that case, which needs `load_asr_model()` first.
        Parrot's UX steers users to supply a transcript (it improves cloning), so
        the empty path is left to the model's own handling rather than silently
        wiring up an unloaded ASR model here.
        """
        if not ref_audio_path:
            return None
        key = f"{ref_audio_path}|{ref_text or ''}"
        cached = self._prompt_cache.get(key)
        if cached is None:
            cached = self._model.create_voice_clone_prompt(ref_audio_path, ref_text or None)
            self._prompt_cache[key] = cached
        return cached

    def synthesize(
        self,
        text: str,
        *,
        ref_audio_path: str | None = None,
        ref_text: str | None = None,
        instruct: str | None = None,
        language: str | None = None,
        seed: int | None = None,
        speed: float = 1.0,
        duration: float | None = None,
        num_step: int = 16,
        guidance_scale: float = 2.0,
        denoise: bool = True,
        postprocess_output: bool = True,
        t_shift: float | None = None,
        layer_penalty_factor: float | None = None,
        position_temperature: float | None = None,
        class_temperature: float | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        from omnivoice import OmniVoiceGenerationConfig  # type: ignore

        if seed is not None:
            self._torch.manual_seed(int(seed))

        # The always-present knobs carry their Parrot defaults; the rest are
        # forwarded only when explicitly set so the model's own defaults apply
        # otherwise (synthesis.md "forwarded only when set").
        gen_kwargs = {
            "num_step": num_step,
            "guidance_scale": guidance_scale,
            "denoise": denoise,
            "postprocess_output": postprocess_output,
        }
        for name, value in (
            ("t_shift", t_shift),
            ("layer_penalty_factor", layer_penalty_factor),
            ("position_temperature", position_temperature),
            ("class_temperature", class_temperature),
        ):
            if value is not None:
                gen_kwargs[name] = value
        gen_config = OmniVoiceGenerationConfig(**gen_kwargs)

        # CRITICAL: build the clone prompt INSIDE inference_mode. The reference
        # encoding (esp. a long ref clip) otherwise creates autograd-tracked
        # tensors whose graph is retained through every generation step — that
        # balloons VRAM into an OOM. The step counter wraps ONLY generate() so
        # the prompt's forward passes aren't mistaken for diffusion steps.
        with self._torch.inference_mode():
            prompt = self._prompt(ref_audio_path, ref_text)
            with self._step_counter(progress_cb, num_step):
                audios = self._model.generate(
                    text=text,
                    language=language or None,
                    instruct=instruct or None,
                    speed=speed,
                    duration=duration,
                    voice_clone_prompt=prompt,
                    generation_config=gen_config,
                )

        # generate() returns one waveform per input text; we synthesize one.
        wav = audios[0] if isinstance(audios, (list, tuple)) else audios
        return self._to_mono_float32(wav)

    @contextmanager
    def _step_counter(self, progress_cb: Callable[[int, int], None] | None, total: int):
        """Report diffusion-step progress by observing the model's forward passes.

        omnivoice's sampler exposes no callback, but it invokes the model once per
        step — so we attach a **forward pre-hook** that counts calls and forwards
        the count to `progress_cb`. A pre-hook is a pure observer: it does NOT
        replace `forward` and holds no tensors, so it can't defeat
        `inference_mode` or retain per-step activations (an earlier approach that
        rebound `forward` did, blowing up VRAM). The hook is always removed (even
        on error); a no-progress call (cb is None) is a zero-cost no-op. `total` is
        `num_step`; the bus clamps any overshoot from prep/chunk passes.
        """
        if progress_cb is None:
            yield
            return

        calls = {"n": 0}

        def _hook(_module, _args):
            calls["n"] += 1
            try:
                progress_cb(calls["n"], total)
            except Exception:  # progress is best-effort; never break synthesis
                pass

        handle = self._model.register_forward_pre_hook(_hook)
        try:
            yield
        finally:
            handle.remove()

    def _to_mono_float32(self, wav) -> np.ndarray:
        """Coerce a torch/np tensor of any channel layout to mono float32 [-1, 1]."""
        if hasattr(wav, "detach"):
            wav = wav.detach().to("cpu").float().numpy()
        arr = np.asarray(wav, dtype=np.float32)
        if arr.ndim > 1:  # (C, T) or (T, C) → mono
            arr = arr.mean(axis=0) if arr.shape[0] < arr.shape[-1] else arr.mean(axis=-1)
        return arr.reshape(-1)
