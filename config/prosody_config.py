"""
config/prosody_config.py

Tunable prosody knobs, one dict per emotion, all in this one file so the
owner can adjust delivery without touching app/voice/prosody.py or any
synthesis code. Not YAML/JSON because AURA doesn't otherwise depend on a
YAML parser (see requirements.txt) — a plain Python dict edited directly
is one less dependency for the same "tune without touching code" goal.

Each entry:
  length_scale  — Piper speaking-rate multiplier. >1.0 = slower.
  noise_scale   — Piper's audio variation knob (voice texture).
  noise_w       — Piper's phoneme-duration variation knob.
  pause_scale   — multiplies the base punctuation pauses in
                  app/voice/prosody.py (1.0 = unchanged).
  pitch_shift   — semitone-ish shift applied post-synthesis, OFF by
                  default (see PITCH_SHIFT_ENABLED below); range-limited
                  by app/voice/prosody.py regardless of this value.

Bounds (enforced in app/voice/prosody.py, not here — this file only
supplies the *desired* values):
  length_scale in [0.8, 1.4], noise_scale/noise_w in [0.1, 1.2],
  pitch_shift in [-0.05, 0.05] (±5%).
"""

# Piper's own natural pace/texture, used as the "neutral" baseline.
_BASE_LENGTH_SCALE = 1.0
_BASE_NOISE_SCALE = 0.667
_BASE_NOISE_W = 0.8

PITCH_SHIFT_ENABLED = False  # off by default, per spec — subtle only.

PROSODY = {
    "neutral":    {"length_scale": 1.00, "noise_scale": 0.667, "noise_w": 0.80, "pause_scale": 1.00, "pitch_shift": 0.00},
    "warm":       {"length_scale": 1.03, "noise_scale": 0.700, "noise_w": 0.82, "pause_scale": 1.05, "pitch_shift": 0.01},
    "happy":      {"length_scale": 0.97, "noise_scale": 0.720, "noise_w": 0.85, "pause_scale": 0.90, "pitch_shift": 0.02},
    "excited":    {"length_scale": 0.90, "noise_scale": 0.750, "noise_w": 0.90, "pause_scale": 0.75, "pitch_shift": 0.03},
    "playful":    {"length_scale": 0.95, "noise_scale": 0.730, "noise_w": 0.88, "pause_scale": 1.15, "pitch_shift": 0.02},
    "calm":       {"length_scale": 1.08, "noise_scale": 0.620, "noise_w": 0.75, "pause_scale": 1.20, "pitch_shift": -0.01},
    "concerned":  {"length_scale": 1.06, "noise_scale": 0.640, "noise_w": 0.76, "pause_scale": 1.15, "pitch_shift": -0.01},
    "serious":    {"length_scale": 1.05, "noise_scale": 0.600, "noise_w": 0.72, "pause_scale": 1.10, "pitch_shift": -0.02},
    "empathetic": {"length_scale": 1.10, "noise_scale": 0.650, "noise_w": 0.78, "pause_scale": 1.30, "pitch_shift": -0.01},
    "confident":  {"length_scale": 0.98, "noise_scale": 0.680, "noise_w": 0.80, "pause_scale": 0.95, "pitch_shift": 0.01},
}

# Base pause durations (ms) inserted after each punctuation mark, before
# per-emotion pause_scale is applied. Tuned for a subtle, natural rhythm,
# not a theatrical read.
BASE_PAUSES_MS = {
    ",": 120,
    ";": 160,
    ":": 160,
    "...": 320,
    "\u2014": 220,  # em dash
    ".": 260,
    "!": 260,
    "?": 260,
}
