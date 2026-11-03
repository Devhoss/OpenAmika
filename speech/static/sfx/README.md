# Sound effects (dormant)

OpenAmika ships NO sound-effect audio. The LLM contract still allows the nine SFX tags
(【Sigh】【Laugh】…, po_contract.SFX_NAMES) and the parser still strips them from spoken
text — but with this folder empty, resolve_sfx() finds nothing and every SFX is skipped
silently (no idx consumed, no playback).

To enable SFX, drop .wav files here and restart the speech layer. Naming (tts_sfx.py):
either `<register>_<SfxName>.wav` / `<register>_<SfxName>_2.wav` (per-register variants)
or the register-free `<SfxName>_SFX_<n>.wav` convention.
