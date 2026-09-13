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

While AURA is speaking, thinking, or executing something, it isn't
listening — so it can't hear and misinterpret its own voice. It resumes
listening automatically once it's done.

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
         Gemini          Ollama          Fallback
      (cloud, free    (local LLM,      (deterministic
       tier, general    optional,       parser + a small
       conversation +    fully offline)  set of identity/
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

### AI providers

```python
AIProvider (interface)
    ├── GeminiProvider   — Google Gemini free-tier REST API
    ├── OllamaProvider   — local LLM via Ollama (optional, fully offline)
    └── FallbackProvider — deterministic parser + a handful of hardcoded
                            identity/greeting replies (NOT a general
                            conversationalist — that's Gemini/Ollama's job)
```

One environment variable switches providers, no code changes:

```
AI_PROVIDER=gemini    # cloud AI — conversation AND actions
AI_PROVIDER=ollama    # local AI — conversation AND actions, fully offline
AI_PROVIDER=fallback  # actions + identity/greetings only, zero setup
```

If the configured provider fails for *any* reason — missing/invalid key,
quota exceeded, timeout, network down, Ollama not running, malformed
JSON — `intent_parser.py` automatically drops to the fallback parser.
AURA keeps running; it just stops being able to answer open-ended
questions until the provider recovers.

### Conversation vs. action routing

There's no separate "conversation" code path bolted on — general
questions and small talk are just another whitelisted action,
`CONVERSE`, with one parameter (`text`, the natural-language reply to
speak). Gemini and Ollama are instructed (`app/brain/prompt_builder.py`)
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
interleaved. The continuous voice loop only records while state is
`LISTENING` or `WAITING_FOR_CONFIRMATION` — every intermediate state
(`THINKING`/`SPEAKING`/`EXECUTING`) is automatically excluded, so AURA
can't record and misinterpret its own voice output; a short pause after
speaking adds extra margin before listening resumes.

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
│   │   ├── provider.py            # AIProvider interface + Gemini/Ollama/Fallback
│   │   ├── prompt_builder.py      # shared system prompt (identity + action/CONVERSE rules)
│   │   ├── identity.py            # AURA_NAME/OWNER_NAME/etc. from .env
│   │   ├── json_utils.py          # shared JSON-extraction helper
│   │   ├── fallback_parser.py     # deterministic parser + small identity/greeting layer
│   │   └── intent_parser.py       # orchestrates provider -> validation -> security
│   ├── actions/
│   │   ├── executor.py            # dispatch table: validated action -> real function
│   │   ├── file_manager.py, app_control.py, system_control.py,
│   │   │   browser.py, screenshot.py
│   ├── security/
│   │   ├── permissions.py         # SAFE/MODERATE/DANGEROUS policy, AI-override protection
│   │   └── confirmation.py        # prompt text + natural yes/no/unclear parsing
│   ├── memory/
│   │   ├── database.py            # SQLite: action_log, memory, conversation
│   │   └── memory_manager.py      # friendly wrapper + "last target" context tracking
│   ├── voice/
│   │   ├── speech_to_text.py      # faster-whisper, continuous record+transcribe
│   │   └── text_to_speech.py      # pyttsx3, voice auto-selection, offline SAPI5
│   ├── ui/
│   │   └── main_window.py         # dark Tkinter dashboard — thin view over AuraAgent
│   └── utils/
│       ├── paths.py, path_safety.py, logger.py
├── tests/                         # 106 tests under Xvfb (98 + 8 GUI) — see "Testing"
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

```
AI_PROVIDER=fallback              # gemini | ollama | fallback

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b-instruct

AURA_LLM_TIMEOUT=20

AURA_NAME=AURA
AURA_OWNER_NAME=                  # e.g. "Vijay Kardak" — blank = "its developer"
AURA_OWNER_ROLE=Developer
AURA_PROJECT_NAME=AURA Personal Desktop Assistant

AURA_TTS_VOICE=                   # blank = auto-select an English female-sounding voice
AURA_TTS_RATE=175
AURA_TTS_VOLUME=1.0

AURA_LISTEN_SECONDS=5
AURA_CONFIRMATION_TIMEOUT_SECONDS=12
```

Never commit `.env` — it's git-ignored. For Gemini: get a free-tier key
at https://aistudio.google.com/app/apikey. For Ollama: install from
https://ollama.com/download, then `ollama pull qwen2.5:3b-instruct`.

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
python -m pytest tests/ -v                  # 98 pass, 8 GUI tests skip (no display)
xvfb-run -a python -m pytest tests/ -v      # 106 pass (Linux/CI, with a virtual display)
python -m pytest tests/ -v                  # on Windows, GUI tests run automatically (real display)
```

**106 tests, all passing**, including everything from the original
9-stage build (schema whitelist, path-traversal rejection, the AI
security-override, SQLite persistence, mocked Gemini/Ollama HTTP
success/quota/timeout/malformed-JSON paths) plus, from this voice-agent
upgrade:

- **`AuraAgent` state machine** (`tests/test_agent.py`) — using fake
  STT/TTS/provider, no hardware needed: continuous listening starts and
  resumes after each command, listening pauses during
  thinking/speaking/executing, a second command is dropped while one is
  in progress, voice confirmation accepts natural phrasing ("yeah",
  "nope", "go ahead"), confirmation times out to cancellation, and —
  critically — a DANGEROUS action's confirmation can't be skipped even
  if the (fake) AI said `requires_confirmation: false`
- **Natural yes/no/unclear parsing** (`tests/test_security.py` /
  `confirmation.py`) — the expanded phrase lists from the spec, plus
  ambiguous replies re-prompting instead of guessing
- **Identity** (`tests/test_fallback_parser.py`,
  `tests/test_ai_providers.py`) — "who are you"/"who built you" using
  configured `.env` values, both via the offline parser and embedded in
  the Gemini/Ollama system prompt
- **TTS voice selection** (`tests/test_tts_voice_selection.py`) — pure
  function tested against fake voice lists (English+female scoring,
  explicit `AURA_TTS_VOICE` override, graceful empty-list handling) —
  doesn't need a real SAPI5 install to verify the *logic*
- **GUI smoke tests via Xvfb** (`tests/test_gui_smoke.py`) — the real
  dark-themed window constructs and renders; a command flows through to
  the conversation log; a DANGEROUS action's Yes/No row appears and
  correctly executes or cancels via `agent.confirm()`, driven through a
  genuine `root.mainloop()` (not just direct function calls)

I also ran full scripted sessions directly against `app/main.py --text`
over real stdin (not mocks) confirming: the greeting is spoken/printed
automatically on startup with no button press; a declined-then-confirmed
delete really removes the file from disk; a 2-second confirmation
timeout really auto-cancels with no reply; `close notepad` (MODERATE)
asks for confirmation; unrecognized input gets a natural response
instead of silence; and the Gemini API key never appears in
`logs/aura.log` even when a (fake) key is configured and the request
genuinely fails over to the fallback parser.

### What's verified vs. what needs Windows

This sandbox is Linux with Xvfb (virtual display) but **no real Windows
APIs, no physical microphone, no speakers, no SAPI5**. Still needing
confirmation on a real Windows 11 machine:

| Feature | Why it can't be verified here |
|---|---|
| Actual continuous microphone recording | No mic in this sandbox — `record_audio()`'s failure path was confirmed (real `OSError: PortAudio library not found` → clean `VoiceInputError`), not that recording *works* |
| Actual SAPI5 voice output + female-voice auto-selection | No speech engine here — `pick_best_voice()`'s selection *logic* is unit-tested against fake voice lists; the real enumeration/output needs Windows |
| `os.startfile`, `LockWorkStation`, `shutdown /s`/`/r`/`/a`, `taskkill` | Windows-only APIs |
| App path auto-detection (Chrome/Edge/VS Code) | Those apps aren't installed in this sandbox |
| GUI visual appearance on a real screen | Only rendered via Xvfb (virtual), not visually inspected by a human |
| Gemini/Ollama against a real key/server | Provider tests use mocked HTTP — the request/response/error-handling logic is verified, not a live account |
| "AURA doesn't hear itself" in a room with real speakers+mic | The state-machine logic (don't record during SPEAKING) is verified; real acoustic feedback/echo has physical factors (speaker volume, mic sensitivity, room) no software test can cover |

**Practical checklist for Windows 11** (do these roughly in this order,
and don't try real shutdown/restart with "yes" until everything else
passes):
1. `run.bat` — confirm the window opens, looks right, and AURA speaks
   the greeting within a second or two of appearing.
2. Confirm it's already listening — no button press needed.
3. Say "who are you" / "who built you" — confirm spoken + on-screen
   answers, and that the owner name matches your `.env`.
4. Say "how are you" — confirm a natural spoken reply, then confirm it
   automatically starts listening again afterward.
5. Say "open notepad" — confirm Notepad opens, then confirm automatic
   re-listening.
6. Say "close notepad" — confirm it asks for confirmation; say "no";
   confirm Notepad is still open and AURA returns to listening.
7. Create then delete a throwaway file/folder via voice — confirm the
   Yes/No flow both ways, and that declining truly leaves it untouched.
8. Say "shutdown my computer" and then **wait without answering** —
   confirm it cancels itself after ~12 seconds and says so.
9. Only once all of the above pass: say "shutdown my computer" and
   confirm with "yes" if you're prepared for it to actually happen.
10. Click STOP LISTENING — confirm the mic actually stops (say something
    and confirm AURA doesn't react); click START LISTENING — confirm it
    resumes.
11. Ask something open-ended ("what is Docker") with `AI_PROVIDER=gemini`
    and a real key — confirm a real, correct spoken answer, not a
    canned one.

---

## Known limitations

- The offline fallback parser only handles actions plus a handful of
  identity/greeting phrases — genuinely open-ended conversation needs
  Gemini or Ollama configured.
- `close_app` force-kills via `taskkill /F` — no graceful save-and-exit.
- `app_control.py`'s known install paths are common defaults, not a
  guarantee; falls through to `PATH` lookup and reports honestly if an
  app truly can't be found, rather than guessing.
- No wake-word ("Hey AURA") — push-to-talk-style continuous listening
  only, per the project's own preference for reliability over ambition.
- No packaging into a single `.exe` — run via Python directly.
- In text-mode console use, an extremely fast scripted/automated line of
  input arriving within milliseconds of the previous one (not realistic
  human typing) can race ahead of the background thread that processes
  it — confirmed harmless for real interactive typing (verified with
  human-realistic delays), but worth knowing if you're scripting AURA's
  console input for automated testing.

## Future scope

Wake-word detection, calendar/reminders, clipboard manager, PDF/document
summarization, local file question-answering, a plugin/skill system, a
proper settings panel (spec section 32's provider/voice/timeout
controls currently live in `.env`, not an in-app UI), and packaging into
a standalone `.exe` — deliberately deferred so the core assistant stays
demonstrable and understandable for a college defense.
