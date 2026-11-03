# TTS: the contract, your server, and the voice adapter

**No TTS server and no voice ship with OpenAmika.** Her voice comes from any server that speaks the small
Socket.IO contract below; point `settings.json → tts_url` at it. Without one she still works: her replies
appear as text (live subtitles), the Live2D avatar still acts them out, and the launcher shows the TTS row
yellow ("she will not speak").

## The contract the speech layer speaks

OpenAmika's speech layer is a **Socket.IO client** to your TTS server (`settings.json → tts_url`).
With the default settings (voice adapter ON → old-vocabulary voice ids), the wire looks like:

```jsonc
// emit("tts_request_gateway", …)
{
  "task_id":      "uuid",
  "turn_id":      "<brain turn>-<idx>",
  "text":         "【happy2】.今日もおつかれさま。",   // emotion tag first, then the sentence
  "character_id": "Amika2",                              // settings.json → tts_character (adapter may swap it)
  "audio_format": "wav",                                  // browsers decode containers, not raw opus
  "language":     "Japanese"                              // optional: Chinese | English | Japanese
}

// on("audio_ready")   — one per request, broadcast (filter by task_id)
{ "task_id", "turn_id", "audio": <bytes>, "audio_format", "audio_duration", "text", "is_final": true }
// plus "task_queued" (accepted) and "error" { task_id, turn_id, error }
```

- One request per sentence; `turn_id` lets a server keep one turn's sentences in order.
- `GET /ping` answering `ok` is what the launcher and the Setup page poll for health.
- `audio` is the encoded file (WAV by default); `audio_duration` is in seconds.

Anything that implements this works. MekaHime's own reference server, **Vanadis Evo** (a GPT-SoVITS
v2ProPlus serving layer), and Amika's voices are MekaHime assets and are not distributed with this
repository. Wrapping an open TTS engine (for example GPT-SoVITS, MIT) in a ~100-line Socket.IO server that
answers `tts_request_gateway` with `audio_ready` is enough; pick the reference clip from the leading
`【emotion】` tag if your engine supports emotion prompts, or ignore the tag.

The launcher can start your server with everything else: describe it under `services.tts` in
`settings.json` (command, cwd, health URL) and set `enabled: true`.

## Emotion vocabularies: internal vs. old-style voices

- **Internal (V2)**: 69 `register_emotion` keys (`casual_happy`, `whisper_caring`, …). This is the
  contract the brain's templates, both streaming parsers and the Live2D banks are built on; it never
  changes regardless of which voice speaks.
- **Old-style voice ids (V1)**: 67 flat ids with intensity numbers (`happy2`, `tsun3`, `whisper1`, …),
  the vocabulary of MekaHime's older GPT-SoVITS voices.

## The voice adapter (default: ON)

`speech/voice_adapter.py` bridges the internal vocabulary to V1-style voice ids and is **enabled by
default** (defaults in code; `config/voice_map.json` is written when you change it on the Setup page):

- the brain **always** emits the 69-key tag; the adapter rewrites **only the leading tag** of the
  outgoing TTS text into an old id via a validated 69→67 map, and swaps `character_id` to the configured
  voice;
- the `emotion` reported to the browser/avatar **stays the new key**, so face and voice never disagree;
- idempotent (an already-old tag passes through; untagged text gets `【neutral1】`);
- the map is hot-reloaded (mtime-gated, last-good on parse errors) and validated cell-by-cell on read; a
  stale or hand-edited file degrades safely to the defaults;
- Setup → Voice gives you the per-cell grid (changed cells highlight), enable/disable, and reset.

**Your own server:** if it understands the 69 internal keys directly (or ignores tags), switch the adapter
off on Setup → Voice and set `tts_character` in `settings.json` to your voice id (that value is used whenever
the adapter is off). If it uses flat ids like `happy2`, keep the adapter on, set its voice on Setup → Voice
(the adapter sends its own voice id, default `Amika2`) and tune the grid by ear; remapping is a voice-acting
decision per voice.
