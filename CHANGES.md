# CHANGES.md — AURA v3 master-prompt pass

## Important context before reading this list

The ZIP received does **not** match the "current state" (Section 3) of the
master prompt:

- The described blocker (`'_io.BytesIO' object has no attribute
  'setframerate'`) does not exist in this code. `app/voice/piper_tts.py`
  already used the modern `voice.synthesize(text, syn_config=...)` →
  `audio_float_array` API, not `synthesize_wav`/`BytesIO`.
- There was no `emotion` kwarg anywhere, so the "already fixed by adding a
  parameter" step in Section 4 hadn't happened either.
- The two-account Groq backup chain described as "verified working" in
  Section 3.1 did not exist — only a single `GROQ_API_KEY` path was wired
  up; a primary failure went straight to the offline parser.
- Conversely, this codebase is considerably *more* built out elsewhere
  than the prompt assumes: `app/core/agent.py` (2500+ lines) already has
  a working confirmation flow, SAFE/MODERATE/DANGEROUS risk tiers,
  short-term context memory ("the other one"), audio-safety sanitization,
  and energy-based VAD with phantom-transcript filtering — under
  different names than the spec used (see the table below).

This file describes what changed in *this* codebase, not a diff against
the prompt's assumed one.

## Files changed

| File | Task | Change |
|---|---|---|
| `app/brain/emotion.py` | 2 | New. `select_emotion()` — pure, rule-based emotion selection from reply/user text + outcome. |
| `app/voice/prosody.py` | 2 | New. `get_prosody()` (clamped length/noise/pitch params) + `chunk_for_speech()` (punctuation-based chunking with pauses) + `silence_samples()`. |
| `config/prosody_config.py` | 2 | New. One tunable dict of prosody parameters per emotion, plus base pause durations. Plain Python (not YAML) — no new dependency. |
| `app/voice/piper_tts.py` | 1, 2 | `speak()` now takes `emotion`/`prosody` kwargs (backward compatible), synthesizes per-chunk with per-chunk `SynthesisConfig`, stitches in silence between chunks, logs `TTS emotion/prosody: X`. |
| `app/voice/text_to_speech.py` | 1, 2 | `TextToSpeech.speak()` and `ResilientTextToSpeech.speak()` accept and gracefully ignore `emotion`/`**kwargs` (pyttsx3 has no comparable prosody knobs). |
| `app/core/agent.py` | 1, 2, 8 | `respond()`: auto-selects emotion, logs `TTS completed`/`TTS failed` distinctly (never "finished" after a failure — old behavior always logged "finished"), calls `tts.speak(text, emotion=...)` with a `TypeError` fallback to the plain call for engines that don't accept it. New `on_tts_status` callback (Task 8). `handle_input()` now remembers `self._last_user_text` for emotion selection. |
| `app/brain/provider.py` | (Section 3.1 / DoD #10) | `GroqProvider` gained an optional second account: `GROQ_API_KEY_2`/`GROQ_MODEL_2`. On primary failure it now tries the backup account before raising, logging exactly the sequence the prompt's Section 3.1 described. No behavior change when `GROQ_API_KEY_2` is unset. `get_provider()`/`describe_active_provider()` untouched — still return/accept a plain `GroqProvider`, so existing `isinstance` checks in tests keep passing. |
| `.env.example` | (same) | Documented `GROQ_API_KEY_2` / `GROQ_MODEL_2`. |
| `app/main.py` | 8 | `ElectronBackend.on_tts_status()` → emits a new `tts-utterance` UI event; wired into the `AuraAgent(...)` construction. |
| `ui-modern/main.js` | 8 | Added `tts-utterance` to the event channel map (documentation; the fallback mapping already forwarded unmapped types). |
| `ui-modern/preload.js` | 8 | Exposed `window.aura.onTtsUtterance(cb)`. |
| `ui-modern/renderer/app.js` | 8 | Minimal hookup: sets `document.body.dataset.emotion` and logs speaking/failed lines via the existing `addLog()` helper. No layout changes. |
| `tests/test_emotion.py` | 2, 9 | New. |
| `tests/test_prosody.py` | 2, 9 | New. |
| `tests/test_piper_tts.py` | 1, 2, 9 | Extended: pause-insertion test, emotion/kwargs backward-compat test. One existing test's input text changed (see note below) since it asserted an exact sample count that the new chunking behavior legitimately changes. |
| `tests/test_ai_providers.py` | (Section 3.1), 9 | New tests for the two-account backup chain (backup used on primary failure, both-fail case, no-backup-configured case unchanged, primary-success never touches backup). |

### Note on the changed assertion in `test_piper_tts.py`

`test_speak_preloads_automatically_if_not_already` asserted
`len(played["audio"]) == 5` for the text `"Hello, welcome to AURA."`. That
assertion depended on the *absence* of chunking — once `speak()` chunks
on punctuation (which Task 2 explicitly requires), a two-clause sentence
now makes two synthesize() calls plus an inserted pause, so the number
changes for real, correct reasons. The test's input text was changed to
a punctuation-free phrase so it keeps testing what it was actually
about (lazy preload), and the multi-chunk/pause behavior gets its own
new test (`test_speak_inserts_pauses_between_punctuation_chunks`).

## Not done (honest, not silently skipped)

- **AI-JSON-supplied emotion** (Task 2, selection-order item 1): the AI
  provider's JSON reply has no `emotion` field yet. Wiring it in touches
  `app/brain/schemas.py`'s whitelist, the JSON validator, and every
  provider's prompt — larger than this pass's blast radius.
  `select_emotion(ai_emotion=...)` is ready to receive it.
- **Task 3 (personality)** — largely already true of this codebase's
  existing `app/brain/prompt_builder.py` (varied acknowledgements, rare
  "sir", no fake consciousness). Not re-verified against the full manual
  test-phrase list in this pass.
- **Task 4 (formal tool registry)** — this codebase already enforces
  action whitelisting + risk tiers via `app/brain/schemas.py` +
  `app/security/permissions.py`, which covers the *spirit* of "AURA
  never claims access it doesn't have" for its own action set, but there
  is no explicit `is_available()`-per-tool registry or a guard that
  rewrites an AI reply claiming completion without a real successful
  tool call. NOT DONE.
- **Task 5 (risk tiers)** — already implemented as SAFE/MODERATE/DANGEROUS
  (not the prompt's SAFE/CONFIRM/BLOCKED naming) in `app/brain/schemas.py`
  + `app/security/confirmation.py` + `app/security/permissions.py`. Not
  re-verified end-to-end against the manual test checklist in this pass.
- **Task 6 (context)** — already implemented (`_last_target`,
  `_pending_clarification`) prior to this session. Not modified or
  re-verified here.
- **Task 7 (VAD/noise robustness)** — already implemented
  (`app/voice/audio_safety.py`, energy-gated VAD in
  `app/voice/speech_to_text.py`) prior to this session. Not modified or
  re-verified here.
- **Per-request provider-status in the UI** (part of Task 8) — the
  backup-chain work added `GroqProvider.last_account_used`, which is the
  foundation for this, but no event currently fires per-command; only
  the one-time startup `provider-status` exists. NOT DONE.
- **Full pytest run** — this sandbox has no network access to install
  `pytest`/`numpy` (numpy happened to already be present). Every new
  piece of logic was instead verified by hand-running the equivalent
  assertions directly with `python3` (shown in this session's tool
  output) — genuinely exercised, just not through the `pytest` runner
  itself. Run `.\.venv\Scripts\python.exe -m pytest tests -q` on the
  real machine to get the authoritative result.
