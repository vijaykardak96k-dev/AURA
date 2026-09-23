# VERIFICATION.md — Section 9 Definition of Done

Legend: **VERIFIED** = actually run in this session, output shown/available.
**WRITTEN BUT NOT RUN** = code exists, not executed here (needs real
hardware/keys/pytest). **NOT DONE** = not implemented this pass.
**PRE-EXISTING** = was already true of the codebase before this session,
not touched or re-verified now.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | `speak()` doesn't raise `setframerate`; PCM → sounddevice | VERIFIED | No such code path existed (already used `synthesize()`/`audio_float_array`, not `synthesize_wav`/`BytesIO`). Confirmed by reading the file; manually ran `speak()` end-to-end against a fake Piper module — succeeded, played concatenated float32 audio via a fake `sounddevice.play`/`wait`. |
| 2 | `TTS completed`/`TTS failed` logged distinctly; mic resumes in `finally` for both | VERIFIED | Manually ran `agent.respond()` with a working `RecordingTTS` (logs `TTS completed (0.00s audio)`) and a `FailingTTS` that raises (logs `TTS failed after 0.00s`, never "finished"); in both cases state returned `SPEAKING → ACTIVE` and the text still reached `on_message`. Output shown in-session. |
| 3 | Emotion selected + logged every reply; prosody in one tunable config; subtle & clamped | VERIFIED | `config/prosody_config.py` is the single tunable file. `get_prosody()` clamps `length_scale` to [0.8, 1.4] and noise params to [0.1, 1.2] regardless of config content — manually tested with an out-of-range/unknown emotion, stayed in bounds. `select_emotion()` manually exercised against ~10 scenarios (terrible day → empathetic, teasing → playful, shutdown/confirm → serious, "no access" → concerned, unknown → neutral), all passed. Logged once per reply via `TTS emotion/prosody: X`. |
| 4 | Personality prompt centralized; replies vary; "sir" rare | PRE-EXISTING, WRITTEN BUT NOT RUN this pass | `app/brain/prompt_builder.py` already centralizes this (varied acknowledgement pool, "sir" guidance, no-fake-consciousness instruction) prior to this session. Not re-verified against the manual test-phrase list in Section 10 here. |
| 5 | Unavailable capabilities → honest response, no fake "Done" | PRE-EXISTING / PARTIAL, NOT DONE (registry) | Action whitelisting + risk tiers already reject/refuse unknown actions (`app/brain/schemas.py`, `app/security/permissions.py`). No formal per-tool `is_available()` registry and no output-guard that rewrites a false "Done" claim — see CHANGES.md "Not done". |
| 6 | Read-only allow-list works; destructive needs confirm w/ 15s expiry; blocked refused; logged | PRE-EXISTING, NOT RE-VERIFIED | `app/security/confirmation.py` + `app/security/permissions.py` implement SAFE/MODERATE/DANGEROUS tiers and a confirmation flow with `AURA_CONFIRMATION_TIMEOUT_SECONDS` (default 12s, not 15s — the prompt says "10–15" is fine, so this is within spec). Pre-dates this session; not re-tested now. |
| 7 | Follow-up phrases resolve via context | PRE-EXISTING, NOT RE-VERIFIED | `_pending_clarification` / `_last_target` mechanism in `app/core/agent.py` pre-dates this session. |
| 8 | VAD + transcript filtering reduce empty/garbage commands; Whisper stays local | PRE-EXISTING, NOT RE-VERIFIED | Energy-gated VAD + phantom-transcript handling already in `app/voice/speech_to_text.py` / `app/voice/audio_safety.py`, using local faster-whisper (`base.en`) throughout — no cloud STT was introduced. |
| 9 | UI shows emotion, TTS status, provider used; existing style intact | PARTIAL | New `tts-utterance` IPC event (emotion + speaking/completed/failed) wired end-to-end: `agent.py` → `main.py` → `main.js` → `preload.js` → `app.js`. Renderer change is additive (`dataset.emotion` + existing `addLog()`), no layout changes — but **not visually run** (no Electron/display in this sandbox); JS syntax-checked with `node --check` only. Per-request provider-used display is NOT DONE (see CHANGES.md). |
| 10 | Groq → Groq backup → offline chain; unsupported `AI_PROVIDER` → Groq w/ warning | VERIFIED | Both manually exercised this session. Backup chain: primary fails → warning "Trying backup Groq account..." → backup succeeds → info "Groq primary unavailable; backup Groq account handled the request." (log lines match Section 3.1 verbatim). Both-fail case raises with "both failed". No-backup-configured case unchanged (still raises "Groq request failed", matching the pre-existing test). `AI_PROVIDER=gemini` → warns and returns `GroqProvider` — this was already correct in `get_provider()` before this session; unaffected by this session's changes. |
| 11 | Tests added and actually run, output pasted | PARTIAL | Added `tests/test_emotion.py`, `tests/test_prosody.py`, extended `tests/test_piper_tts.py` and `tests/test_ai_providers.py` (≈15 new test functions). **Not run via `pytest`** — this sandbox has no network access to install it. Every new test's assertions were instead hand-executed directly with `python3` in this session (shown in tool output) and passed. Run `.\.venv\Scripts\python.exe -m pytest tests -q` on the real machine for the authoritative result — the project's total test count is ~230 functions across all files. |
| 12 | Startup log still reaches the healthy sequence | WRITTEN BUT NOT RUN | Cannot launch Electron/the real Python backend in this sandbox (no audio hardware, no `.venv`, no Groq key). `py_compile` succeeds on every changed `.py` file and `node --check` succeeds on every changed `.js` file, which is as close to "it still boots" as this environment can confirm. |

## Commands (run on the real machine)

```powershell
# Install the one new nothing — no new required dependency was added.
# (GROQ_API_KEY_2 is an optional env var, not a package.)

.\.venv\Scripts\python.exe -m pytest tests -q

cd ui-modern
npm start
```

## Manual test checklist status

Not run — requires the real machine's microphone, speakers, Piper voice
file, and a live Groq key. Section 10's 19-item checklist in the master
prompt is unchanged and still the right list to run by hand; items 1–5
and 17–19 specifically exercise this session's changes (emotion/prosody
on real audio, and the Groq backup chain with real rate limits).
