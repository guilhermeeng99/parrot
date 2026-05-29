# Parrot — Licensing

## Decision (2026-05-29): Path B — Apache-2.0

Parrot is a **genuine OSI open-source project licensed under Apache-2.0.** It is **not** a code fork of OmniVoice Studio. Parrot:

- reuses **only** the **Apache-2.0** `omnivoice` model library for inference, and
- **reimplements its own application** (Svelte UI, Rust shell, Python FastAPI sidecar) from the specs in this `docs/` folder.

OmniVoice Studio may be used as a **design reference** (architecture, API shapes, behaviors), but its **FSL-1.1-ALv2 application code must not be copied** into Parrot. Ideas and architecture are not copyrightable; specific code expression is. When in doubt, reimplement from the spec rather than paste.

Why Path B: a clean license story, commercial freedom, no non-compete clause, and no dependency on OmniVoice's per-release Apache-conversion timeline. Apache-2.0 (over MIT) gives an explicit patent grant and matches the bundled model lib's license.

## Obligations

- Ship a root **`LICENSE`** (Apache-2.0) — Parrot's own code.
- Ship a root **`NOTICE`** crediting the Apache-2.0 `omnivoice` model library (Han Zhu / k2-fsa) and preserving its license/notices, as Apache-2.0 §4 requires.
- Do not reuse the OmniVoice name or logo (no trademark grant, and Parrot is its own brand anyway).

## ⚠️ Open item — model *weights* license (independent of the above)

The license on the model **code** (`omnivoice`, Apache-2.0) is not necessarily the license on the model **weights** downloaded at first run. Weights can carry a separate license (e.g. OpenRAIL-M, CC-BY-NC) that may restrict commercial use **regardless of Apache-2.0 on the code or on Parrot**.

**Before relying on Parrot commercially, confirm the weights' license** on the model's Hugging Face repo (the `license` field of the *weights* repo, not just the code). If the weights are non-commercial, neither the app license nor Path B changes that — only swapping to a commercially-licensed model does. This does not block open-source release; it bounds commercial use.

## Background — why not Path A (the fork)

OmniVoice's **app code** is **FSL-1.1-ALv2**: *source-available*, free for personal/non-commercial use, with a non-compete clause, converting to Apache-2.0 two years after each release. Forking that code would force Parrot to inherit FSL — meaning Parrot could not be advertised as OSI open-source, and a commercial Parrot would be a likely "Competing Use" needing a license from the OmniVoice author. Path B avoids all of this by not copying FSL code. (The `omnivoice` *model lib* is separately Apache-2.0 and is fine to reuse.)

## Sources

- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) — Parrot's license; see §4 for the NOTICE requirement.
- [Functional Source License](https://fsl.software/) — the license on OmniVoice's app code (why it is not reused).
- OmniVoice model card on Hugging Face — confirm the **weights** license here before any commercial use.
