# AURA — Personal Desktop AI Assistant

AURA is an always-listening, voice-first personal desktop AI agent for
Windows 11, built as a BCA final-year project. Launch it and it greets
you, starts listening, and stays in conversation with you — actions
("open Chrome", "create a folder called College Project", "delete
notes.txt"), general questions ("what is Docker", "why is the sky
blue"), and small talk ("how are you", "who built you") all flow through
one natural voice/text conversation, not a form with a microphone button.

No AI model, local or cloud, is ever allowed to run a raw shell command —
every desktop action passes through a strict whitelist, a static
security-level table, and (for anything risky) an explicit spoken or
typed confirmation before it executes.

---

## Voice interaction

AURA automatically enters listening mode when launched and returns to
listening after every completed interaction — you never click a button
to "start" a conversation, only to pause one.

```
run.bat
```

```
AURA: "Hello, welcome to AURA. How may I help you?"
● LISTENING

You: "Open Notepad."
AURA: "Notepad is open."
● LISTENING   (automatically, no button press)

You: "Delete notes.txt."
AURA: "Are you sure you want to permanently delete 'Desktop/notes.txt'?
       This cannot be undone."
● WAITING FOR CONFIRMATION

You: "No."
AURA: "Okay, I've cancelled that."
● LISTENING
```

Dangerous actions (delete, shutdown, restart, closing an app) always ask
first, understand natural replies ("yeah", "nope", "go ahead", "not
now" — not just the literal words "yes"/"no"), and default to
**cancellation** if you don't answer within the confirmation timeout
(12 seconds by default) — never to proceeding.

### How a command starts and ends

AURA does not record in fixed one-second chunks. The audio itself decides
where a command begins and ends:

```
no speech yet          -> keep waiting (up to 6s, then give up quietly)
energy crosses gate    -> command started      ● HEARING
speech continues       -> keep recording
~0.8s of real silence  -> command finished, transcribe
```

A short pre-roll is kept so the first syllable isn't clipped, and audio
that turns out to be too short, too quiet, or just one noise spike is
discarded *before* Whisper runs. An empty transcription never reaches
Groq, never runs an action, and never generates a response — AURA simply
goes back to listening.

### Mute, and why the microphone is off while AURA talks

**While AURA is speaking, the microphone does not record.** This is
enforced by state, not by trying to filter AURA's own words out
afterwards — every capture call polls a gate every 50 ms and tears the
stream down the moment AURA leaves a listening state.

The consequence is deliberate: AURA cannot hear you say "mute". To
interrupt a reply, type `mute` (or `stop talking`) in the command box.
Speech stops immediately and AURA returns to listening.

### Sleep and wake

| You say / do | What happens |
|---|---|
| `sleep`, `go to sleep` | AURA goes quiet. Only the wake word is listened for, and Whisper only runs on windows that actually contain sound — so a sleeping AURA costs almost no CPU. |
| `AURA` (the wake word) | Wakes and listens for a command. `"AURA, open Chrome"` in one breath works too. |
| `wake up` | Wakes from sleep *or* from a stopped microphone. |
| `stop listening` / the button | Microphone off entirely. Typing still works. |
| 120s of inactivity | Returns to sleep on its own. |

---

## Architecture

Both the Tkinter GUI and the text-mode console are thin front ends
around **one** orchestrator, `AuraAgent` (`app/core/agent.py`). Neither
front end has its own copy of the pipeline — voice, typed GUI input, and
console input all produce identical behavior, including the
confirmation flow.

```
                          USER
                           |
              Microphone (continuous)  OR  typed text
                           |
                           v
          Speech-to-Text (faster-whisper, local, offline)
                           |
                           v
                    AI PROVIDER LAYER
            ┌──────────────┼───────────────┐
            v               v               v
         Groq                         Fallback
      (cloud AI,                    (deterministic
       general conversation +      parser + a small
       actions)                     set of identity/
       actions)                          greeting replies —
                                          not a general
                                          conversationalist)
            └──────────────┼───────────────┘
                           v
              Schema validation (whitelist only —
              CONVERSE is a normal, safe, whitelisted
              action like any other; no action name
              outside schemas.py is ever recognized)
                           v
              Security layer (SAFE / MODERATE / DANGEROUS
              from a static table — never from what the AI
              said about itself; a DANGEROUS/MODERATE action
              is confirmed regardless of the AI's own
              requires_confirmation hint)
                           v
              AuraAgent state machine
        STARTING → LISTENING ⇄ THINKING → (WAITING_FOR_
        CONFIRMATION →) EXECUTING → SPEAKING → LISTENING
                           v
                    Action Executor
        ┌──────┬──────┬──────┬──────┬─────────┐
        v      v      v      v      v         v
      Files  Apps  System Browser Screenshot CONVERSE
        └──────┴──────┴──────┴──────┴─────────┘
                           v
                        Result
                           v
          Text-to-Speech (pyttsx3, offline, SAPI5,
          auto-selects an English female-sounding voice)
                           v
                  SPEAKER + on-screen chat log
                           v
                  back to LISTENING, automatically
```

### AI provider

```text
AIProvider
    ├── GroqProvider      — Groq cloud AI for conversation + actions
    └── FallbackProvider  — deterministic local parser when Groq is unavailable
```

AURA uses Groq as its only cloud AI provider. Set `GROQ_API_KEY` and
`GROQ_MODEL` in `.env`. If Groq is unavailable, AURA falls back locally
instead of trying another cloud provider.

The provider is configured through `.env`:

```text
AI_PROVIDER=groq
GROQ_API_KEY=your_key
GROQ_MODEL=openai/gpt-oss-20b
```

For offline operation without an AI key, use `AI_PROVIDER=fallback`.

If the configured provider fails for *any* reason — missing/invalid key,
JSON — `intent_parser.py` automatically drops to the fallback parser.
AURA keeps running; it just stops being able to answer open-ended
questions until the provider recovers.

### Conversation vs. action routing

There's no separate "conversation" code path bolted on — general
questions and small talk are just another whitelisted action,
`CONVERSE`, with one parameter (`text`, the natural-language reply to
to answer general questions directly and use `CONVERSE`, or use one of
the desktop-action names for anything that touches files/apps/the
system. This means conversation replies go through the *exact* same
validation → security → execution → response pipeline as `OPEN_APP` or
`DELETE_FILE` — there's no special-cased "free text" path that could
skip the security layer.

The offline fallback parser does **not** attempt general conversation —
it recognizes actions plus a small, explicitly limited set of identity
and greeting phrases (who are you / who built you / how are you / thank
you / good morning-night). Anything else it doesn't recognize returns a
natural "I didn't catch that" reply rather than pretending to understand.

### Security model

```
AI / Fallback Parser
        |
        v
Schema validation   <- whitelist only (schemas.py); rejects any attempt
        |                to smuggle a shell-command-style action name
        v
Security layer      <- SAFE / MODERATE / DANGEROUS from a static table
        |                (permissions.py) — never from what the AI said
        |                about itself. Tested explicitly: even if the
        |                AI's own JSON says "requires_confirmation": false
        |                for DELETE_FILE/SHUTDOWN_SYSTEM, this is overridden.
        v
AuraAgent confirmation  <- spoken/typed question, natural yes/no parsing,
        |                   12-second timeout defaulting to cancellation
        v
Executor             <- a fixed dispatch table (action name -> function).
                         No subprocess call anywhere in the app takes
                         AI-generated text as a command string.
```

Filesystem actions additionally go through `app/utils/path_safety.py`,
which only accepts paths relative to a known safe root (Desktop,
Documents, Downloads, Pictures, Videos, Music), rejects `..` traversal,
and rejects anything resolving outside that root (e.g.
`C:\Windows\System32`).

### "Don't process two commands at once" / "Don't hear yourself"

`AuraAgent` holds a plain lock for the full duration of processing one
command (including any confirmation wait and TTS speech inside it), so a
second command arriving mid-processing is dropped rather than
interleaved. `mute` deliberately bypasses that lock — a mute that had to
queue behind the response it's trying to interrupt would be useless.

Microphone gating is centralised in `AuraAgent._listening_allowed()`,
which is the single source of truth for "may the microphone be recording
right now?". It returns `False` for `SPEAKING`, `THINKING`, `EXECUTING`,
`STOPPED` and `STARTING`. Every blocking STT call receives it as a
`should_continue` callback and polls it on each 50 ms block, so a capture
that was already in flight when AURA started speaking aborts immediately
rather than running on and recording Piper through the speakers.

This is state-based gating, not after-the-fact filtering. (There *is* a
transcript-similarity echo check as a backstop, but it should never be
the thing that saves you.)

### Nothing invalid reaches Whisper

`app/voice/audio_safety.py` sits between the microphone and
faster-whisper. It exists because of three real failures in the logs:

```
RuntimeWarning: overflow encountered in square
RuntimeWarning: invalid value encountered in matmul
Python backend exited with code=3221225477   (access violation)
```

All three come from raw buffers containing NaN/Inf or out-of-range
samples reaching NumPy and then CTranslate2. Every buffer is now
normalised to finite float32 in `[-1.0, 1.0]`; RMS is computed in
float64 so it can't overflow; a handful of corrupt samples are clipped,
while a genuinely integer-scaled buffer is rescaled (clipping that would
turn speech into a square wave). A buffer that can't be salvaged is
discarded and logged, and listening continues. The warnings are fixed at
the source, not suppressed.

---

## Project structure

```
AURA/
├── app/
│   ├── main.py                    # entry point — GUI by default, text-mode fallback,
│   │                                both front ends driven by the one AuraAgent
│   ├── core/
│   │   └── agent.py               # AuraAgent — state machine, continuous listening,
│   │                                confirmation (NLP + timeout), respond() pipeline
│   ├── brain/
│   │   ├── schemas.py             # the whitelist — the only actions AURA can run
│   │   ├── prompt_builder.py      # shared system prompt (identity + action/CONVERSE rules)
│   │   ├── identity.py            # AURA_NAME/OWNER_NAME/etc. from .env
│   │   ├── json_utils.py          # shared JSON-extraction helper
│   │   ├── fallback_parser.py     # deterministic parser + small identity/greeting layer
│   │   ├── persona.py             # rotating phrase pools so local replies don't sound canned
│   │   ├── local_intents.py       # table-driven fast path that skips the network entirely
│   │   └── intent_parser.py       # orchestrates provider -> validation -> security
│   ├── actions/
│   │   ├── executor.py            # dispatch table: validated action -> real function
│   │   ├── app_control.py         # name normalization, fuzzy matching, app discovery
│   │   ├── file_manager.py, system_control.py, browser.py,
│   │   │   screenshot.py, media.py, clipboard.py
│   ├── security/
│   │   ├── permissions.py         # SAFE/MODERATE/DANGEROUS policy, AI-override protection
│   │   └── confirmation.py        # prompt text + natural yes/no/unclear parsing
│   ├── memory/
│   │   ├── database.py            # SQLite: action_log, memory, conversation
│   │   └── memory_manager.py      # friendly wrapper + "last target" context tracking
│   ├── voice/
│   │   ├── audio_safety.py        # sanitises every buffer before it reaches Whisper
│   │   ├── speech_to_text.py      # faster-whisper, VAD capture, abortable streams
│   │   ├── wake_word.py           # pure text wake-word gate (no audio, no model)
│   │   ├── piper_tts.py           # Piper neural TTS, model loaded once at startup
│   │   └── text_to_speech.py      # engine factory + pyttsx3/SAPI5 fallback
│   ├── ui/
│   │   └── main_window.py         # dark Tkinter dashboard — thin view over AuraAgent
│   └── utils/
│       ├── paths.py, path_safety.py, logger.py
├── ui-modern/                     # Electron front end (main.js, preload.js, renderer/)
├── tests/                         # see "Testing" below
├── config/config.example.yaml
├── data/, logs/, screenshots/     # generated at runtime, git-ignored
├── requirements.txt, .env.example, .gitignore, run.bat, Jenkinsfile
└── README.md
```

---

## Requirements

- Windows 11, Python 3.10+, 16 GB RAM, no GPU required
- No paid API required — `AI_PROVIDER=fallback` works with zero setup and zero internet (actions + identity/greetings only)

## Installation

```
git clone <your-repo-url>
cd AURA
run.bat
```

`run.bat` checks Python is on PATH, creates a `.venv` on first run,
installs `requirements.txt`, copies `.env.example` to `.env` if missing,
and starts AURA.

If `faster-whisper`/`sounddevice` fail to install (e.g. missing build
tools), you can skip voice and still use everything else:

```
pip install python-dotenv psutil mss requests pytest pyttsx3
python -m app.main --no-voice
```

---

## Configuration (`.env`)

Copy `.env.example` to `.env` and edit that — never put a key in a `.py`
file. `.env` is git-ignored.

```ini
# --- AI provider -----------------------------------------------------
AI_PROVIDER=groq                  # groq | ollama | fallback
GROQ_API_KEY=                     # blank = AURA runs offline, and says so
GROQ_MODEL=openai/gpt-oss-20b
AURA_LLM_TIMEOUT=15               # keep short: a long timeout is a long silence
                                  # before the offline fallback kicks in

# --- identity / wake word --------------------------------------------
AURA_NAME=AURA                    # this is also the wake word
AURA_OWNER_NAME=
AURA_OWNER_ROLE=Developer
AURA_PROJECT_NAME=AURA Personal Desktop Assistant

# --- listening -------------------------------------------------------
AURA_SESSION_TIMEOUT_SECONDS=120  # inactivity before returning to sleep
AURA_CONFIRMATION_TIMEOUT_SECONDS=12
AURA_VAD_ENERGY_THRESHOLD=0.012   # see tuning note below

# --- speech to text (local Whisper) ----------------------------------
AURA_WHISPER_MODEL=base.en        # tiny.en | base.en | small.en | small | medium

# --- text to speech --------------------------------------------------
AURA_TTS_ENGINE=auto              # piper | pyttsx3 | auto
PIPER_MODEL_PATH=                 # path to a downloaded .onnx voice
PIPER_CONFIG_PATH=                # optional if it sits next to the model
PIPER_LENGTH_SCALE=1.0            # >1.0 slower, <1.0 faster
AURA_TTS_VOICE=                   # pyttsx3 only: blank = auto-select
AURA_TTS_RATE=175
AURA_TTS_VOLUME=1.0
```

**`AURA_VAD_ENERGY_THRESHOLD` is the one setting you will probably need
to tune.** It's the RMS level that counts as "someone started talking",
and the right value depends on your microphone and your room:

- AURA keeps capturing background noise → **raise** it (try `0.02`)
- AURA clips the start of what you say, or doesn't react → **lower** it
  (try `0.008`)

**`AURA_WHISPER_MODEL`** trades accuracy for latency. `base.en` is the
default because it's noticeably faster on CPU and accurate enough for
short desktop commands. `small.en` is more accurate but roughly 2–3×
the transcription time on every utterance.

### Offline behaviour

AURA is designed to stay useful with no internet:

| Subsystem | Needs internet? |
|---|---|
| Whisper speech-to-text | No — runs locally |
| Piper text-to-speech | No — runs locally |
| Desktop actions (apps, files, system, volume) | No |
| Local fast-path commands | No |
| Open-ended conversation, unusual phrasing | Yes (Groq) |

Without a reachable Groq, `intent_parser.py` drops to the deterministic
fallback parser. AURA reports this honestly — the header reads `OFFLINE`
rather than `GROQ`, and the startup log says why (missing key, missing
SDK, or a failed request). It never pretends Groq is working when it
isn't. Availability is checked locally first, so offline commands don't
wait out a connection timeout before falling back.

---

## Running AURA

```
run.bat
```
or
```
python -m app.main            # GUI, auto-listening, default
python -m app.main --text     # console instead of GUI
python -m app.main --no-voice # GUI/console without mic+TTS (typed only)
```

---

## Testing

```
python -m pytest tests/ -v
```

The suite runs entirely without a microphone, speaker, Whisper model,
Groq key, or network — every audio and AI boundary is faked, so the
behaviour under test is AURA's own logic rather than a device's.

What's covered, grouped by the problem it guards against:

- **Audio safety** (`tests/test_audio_safety.py`) — NaN/Inf/out-of-range
  buffers are neutralised, RMS can't overflow, a few corrupt samples are
  clipped without destroying a real recording, an integer-scaled buffer
  is rescaled rather than clipped into a square wave, silence and short
  blips and single noise spikes are rejected before Whisper runs, and
  Whisper's known phantom transcripts ("Thanks for watching!", "[Music]")
  are filtered out — while a transcript containing the wake word never is.
- **App name resolution** (`tests/test_app_control.py`) — every casing of
  "chrome" resolves identically, common mishearings ("task manger",
  "calculater", "control pannel", "command promt") resolve correctly,
  and — the important negative case — an app AURA has never heard of is
  passed through untouched rather than fuzzy-matched onto something the
  user didn't ask for. Also: a missing app offers a next step instead of
  saying "Application not found", and `close_app` refuses both Explorer
  and free-form names it can't map to a process.
- **Agent behaviour** (`tests/test_agent_behavior.py`) — the UI receives
  a response *before* TTS is attempted; a failing Piper costs the audio
  and nothing else; SPEAKING is never a terminal state; a broken UI
  callback doesn't stop the response; typed `mute` stops speech
  immediately and bypasses the busy lock; the microphone gate is closed
  during SPEAKING/THINKING/EXECUTING/STOPPED and a capture in flight can
  observe that it should abort; empty transcriptions never reach the
  provider; sleep/wake survives repeated cycles and recovers from a
  stopped microphone; a clarifying question is answered by the next bare
  phrase, but a real command overrides it and a stale question expires.
- **`AuraAgent` state machine** (`tests/test_agent.py`) — the wake-word
  gate (a bare wake word never reaches the AI provider; "AURA open
  Chrome" passes only "open chrome"; a mid-sentence mention doesn't
  falsely wake), session timeout, voice confirmation with natural
  phrasing, and — critically — a DANGEROUS action's confirmation can't be
  skipped even if the (fake) AI said `requires_confirmation: false`.
- **Provider** (`tests/test_provider.py`, `tests/test_ai_providers.py`) —
  Groq is the default, a legacy `AI_PROVIDER=gemini` resolves to Groq,
  the client is reused across calls rather than rebuilt per command, and
  `describe_active_provider()` reports offline honestly.
- **Security** (`tests/test_security.py`, `tests/test_path_safety.py`,
  `tests/test_schemas.py`) — the action whitelist, `..` traversal
  rejection, safe-root enforcement, and the AI-override protection.
- **GUI smoke** (`tests/test_gui_smoke.py`) — skips automatically without
  a display; runs on Windows.

### What still needs a real Windows machine

No software-only test can confirm these, and I'd rather say so than
imply otherwise:

| Needs confirmation | Why |
|---|---|
| Actual microphone capture and Whisper accuracy | No mic here — only the *failure* path is verified (PortAudio missing → clean `VoiceInputError`) |
| Actual Piper/SAPI5 audio output | No speech engine or speakers in this sandbox |
| Acoustic feedback in a real room | The state gating is verified; speaker volume, mic sensitivity and room acoustics are physical |
| `os.startfile`, `LockWorkStation`, `shutdown`, `taskkill`, `clip.exe` | Windows-only APIs |
| Start Menu / App Paths discovery, and the installed-app list | Needs a real Windows registry and Start Menu |
| Whether `AURA_VAD_ENERGY_THRESHOLD=0.012` suits *your* mic | It's a starting point, not a universal default — see Configuration |

### Suggested first run on Windows

Roughly in this order, and don't answer "yes" to a real shutdown until
everything else passes:

1. `run.bat` — window opens, greeting is spoken, header shows `GROQ`
   (or `OFFLINE` if you haven't set a key yet).
2. Type `help` — confirm the capability list appears **and** is spoken.
3. Type `open task manager`, then `open chrome`, then `open task manger`
   (deliberate typo) — all three should work.
4. Type `list installed apps` — the full list should appear on screen
   while only a short summary is spoken.
5. Say "AURA" — confirm it wakes; then say "open notepad".
6. While AURA is speaking a long reply, type `mute` — speech should stop
   at once.
7. Say "sleep", then "AURA" again — confirm it goes quiet and comes back.
8. Check `logs/aura.log` for the state transitions and the "Microphone
   disabled / may resume" lines around each TTS.

## Known limitations

Stated plainly rather than buried:

- **AURA cannot hear "mute".** The microphone is deliberately off while
  it speaks, so interrupting is a typed/UI action. This is the design,
  not an oversight — the alternative is Whisper transcribing Piper.
- **File operations are sandboxed** to Desktop, Documents, Downloads,
  Pictures, Videos and Music. "Copy this file from C: to D:" will be
  refused. Widening this would mean giving an LLM-driven pipeline write
  access to the whole filesystem, which isn't a trade I'd make quietly;
  edit `app/utils/paths.py` if you want different roots.
- **The offline fallback parser is deterministic, not clever.** It
  handles actions plus a handful of identity/greeting phrases. Genuine
  conversation, humour and unusual phrasing need Groq — offline AURA is
  a competent command runner, not a companion.
- **`close_app` force-kills** via `taskkill /F` — no graceful
  save-and-exit — and refuses names it can't map to a known process
  rather than guessing at a `.exe`.
- **App discovery is best-effort.** Start Menu shortcuts and the App
  Paths registry cover most installed software, but an app that
  registers neither won't be found. AURA says so rather than guessing.
- **Whisper is still local and CPU-bound.** `base.en` keeps latency
  reasonable, but transcription is the slowest step in the voice path.
  Cloud STT would be faster; that was explicitly out of scope here.
- **Fuzzy matching is intentionally conservative.** It fixes "task
  manger", but it won't rescue a badly mangled transcription — and it
  deliberately won't guess at destructive commands.
- **No single-`.exe` packaging** — run via Python.

## Future scope

Wake-word detection, calendar/reminders, clipboard manager, PDF/document
summarization, local file question-answering, a plugin/skill system, a
proper settings panel (spec section 32's provider/voice/timeout
controls currently live in `.env`, not an in-app UI), and packaging into
a standalone `.exe` — deliberately deferred so the core assistant stays
demonstrable and understandable for a college defense.
