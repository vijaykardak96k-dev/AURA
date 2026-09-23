# AURA voice/reliability notes

What changed in the voice path, and why. Kept because the *reasons* are
easy to lose and expensive to rediscover.

## The three crashes

Real logs showed:

```
RuntimeWarning: overflow encountered in square
RuntimeWarning: invalid value encountered in matmul
Python backend stopped with code 3221225477
```

`3221225477` is `0xC0000005` — a Windows access violation, i.e. the
native CTranslate2 library crashing, not Python raising. All three share
one cause: raw `sounddevice` buffers containing NaN/Inf or samples well
outside `[-1.0, 1.0]` were being squared (overflowing float32) and then
fed into Whisper's matmul.

Fix: `app/voice/audio_safety.py`. Every buffer is normalised to finite
float32 in range before Whisper sees it; RMS is computed in float64;
unsalvageable buffers are discarded and logged, and listening continues.
The warnings are not suppressed — the invalid data no longer exists.

Note the deliberate asymmetry in `sanitize_audio()`: a handful of wild
samples are **clipped** (rescaling on one bad sample would make a good
recording inaudible), but a buffer whose 99th percentile is also out of
range is **rescaled** (clipping genuine int16-scaled audio would turn
speech into a square wave). Both cases are covered by tests.

## Microphone / TTS overlap

The old `listen_until_silence()` had no abort path: once the input stream
was open it ran to completion, including through `THINKING` and
`SPEAKING`. That is the actual mechanism behind "Whisper hears AURA".

Fix: every blocking capture now takes a `should_continue` callback and
polls it on each 50 ms block. `AuraAgent._listening_allowed()` is the
single source of truth and returns `False` for SPEAKING / THINKING /
EXECUTING / STOPPED / STARTING.

This is why `mute` is typed, not spoken. A microphone that stays open so
it can hear "mute" is a microphone that hears everything else AURA says.

## Command boundaries

Fixed-duration chunks were cutting sentences in half. Capture is now
driven by the audio: wait for energy, record while speech continues, end
on ~0.8 s of real silence. A 0.45 s pre-roll protects the first syllable,
and trailing silence beyond a short tail is trimmed before transcription.

Anything too short, too quiet, or showing only one voiced block (a door
slam) is discarded *before* Whisper runs.

## Empty transcriptions

`Transcribed: ''` used to flow into the full pipeline. Now an empty or
phantom transcript ("you", "Thanks for watching!", "[Music]") returns to
listening without calling Groq, executing an action, or speaking.

In wake mode the phantom filter never discards a transcript containing
the wake token — `wake_word.detect_wake_word()` stays the only wake gate.

## Sleeping CPU

A sleeping AURA ran Whisper on every 2.2 s window regardless of content.
Now an RMS gate discards silent windows before the decoder runs.

## TTS lifecycle

`respond()` publishes to the UI, writes memory, *then* attempts TTS
inside a `try/finally`. Piper failing costs the audio and nothing else,
and SPEAKING can no longer become a terminal state.

Whisper default moved from `small` to `base.en` (override with
`AURA_WHISPER_MODEL`) and beam size to 1, both for CPU latency.

## Still unverified

Real microphone capture, real Piper/SAPI5 output, and acoustic feedback
in a real room. No software-only test can cover those — see README's
"Suggested first run on Windows".
