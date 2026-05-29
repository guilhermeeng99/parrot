"""Adapter over the vendored Apache-2.0 `omnivoice` model lib.

`model_manager` constructs exactly one of these and calls `.synthesize(...)`. The
adapter owns the real `torch`/`omnivoice` imports, device placement, the voice-
clone prompt step (voice-cloning.md §3), seeding, and converting the model's
output tensor into the mono float32 [-1, 1] numpy array the rest of the sidecar
expects at `self.sampling_rate` (24 kHz).

This file is the genuinely-unverifiable ML boundary: it runs only in a build with
the `engine` extra + the vendored model lib + downloaded weights, never in the
mocked test venv. The exact `omnivoice` call surface is reconciled against the
vendored lib's API at integration time; the *contract* (the method names/shapes
below) is what the rest of Parrot is built against and tested with.
"""

import logging

import numpy as np

log = logging.getLogger(__name__)


class OmniVoiceBackend:
    def __init__(self, device: str):
        import torch  # type: ignore
        from omnivoice import OmniVoiceModel  # type: ignore

        self._torch = torch
        self.device = device
        # Weights resolve from the HF cache (downloaded on first run); the cache
        # location + Windows path-length fix are set in app.config before this.
        self._model = OmniVoiceModel.from_pretrained().to(device).eval()
        self.sampling_rate = int(getattr(self._model, "sampling_rate", 24000))
        self._prompt_cache: dict[str, object] = {}

    def _prompt(self, ref_audio_path: str | None, ref_text: str | None):
        """Build (and cache) the voice-clone conditioning prompt for a reference."""
        if not ref_audio_path:
            return None
        key = f"{ref_audio_path}|{ref_text or ''}"
        cached = self._prompt_cache.get(key)
        if cached is None:
            cached = self._model.create_voice_clone_prompt(ref_audio_path, ref_text or "")
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
    ) -> np.ndarray:
        if seed is not None:
            self._torch.manual_seed(int(seed))

        # Forward only the advanced knobs that were explicitly set (the model
        # supplies its own defaults otherwise — synthesis.md "forwarded only when set").
        extra = {
            k: v
            for k, v in {
                "t_shift": t_shift,
                "layer_penalty_factor": layer_penalty_factor,
                "position_temperature": position_temperature,
                "class_temperature": class_temperature,
                "duration": duration,
            }.items()
            if v is not None
        }

        with self._torch.inference_mode():
            wav = self._model.generate(
                text=text,
                prompt=self._prompt(ref_audio_path, ref_text),
                instruct=instruct or None,
                language=language,
                speed=speed,
                num_step=num_step,
                guidance_scale=guidance_scale,
                denoise=denoise,
                postprocess_output=postprocess_output,
                **extra,
            )

        return self._to_mono_float32(wav)

    def _to_mono_float32(self, wav) -> np.ndarray:
        """Coerce a torch/np tensor of any channel layout to mono float32 [-1, 1]."""
        if hasattr(wav, "detach"):
            wav = wav.detach().to("cpu").float().numpy()
        arr = np.asarray(wav, dtype=np.float32)
        if arr.ndim > 1:  # (C, T) or (T, C) → mono
            arr = arr.mean(axis=0) if arr.shape[0] < arr.shape[-1] else arr.mean(axis=-1)
        return arr.reshape(-1)
