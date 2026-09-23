"""
app/ui/main_window.py

The Tkinter desktop window — redesigned as a dark, modern assistant
dashboard rather than a plain form with a mic button (spec sections 17-21).

Crucially, this file contains almost NO business logic anymore. Every
command — voice, typed, or button-driven — goes through the single
AuraAgent (app/core/agent.py), which owns the state machine, the
continuous listening loop, confirmation handling, and the response
pipeline. This window is a thin, honest view over that agent: it renders
state changes and messages the agent reports via callbacks, and forwards
user input (typed text, Start/Stop, Yes/No) back into the agent. Nothing
here calls tts.speak(), execute_action(), or parse_intent() directly.

Threading: the agent's voice loop and any background command processing
run on non-UI threads. All callbacks marshal back to the Tk main thread
via `root.after(0, ...)`, which is safe to call from any thread.

NOT VISUALLY VERIFIED ON A REAL SCREEN — built and exercised via Xvfb
(virtual display) in this project's Linux sandbox: confirmed to
construct, render, and correctly drive the agent through real commands,
but actual visual appearance on Windows 11 needs a look.
"""

import threading
import tkinter as tk
from tkinter import scrolledtext

from app.core.agent import AuraAgent, AuraState
from app.memory import memory_manager

# --- palette: dark, modern, restrained (spec: not a "sci-fi movie") ---
BG = "#0f1117"
PANEL = "#161922"
ACCENT = "#5ec8d8"
ACCENT_DIM = "#2f6b74"
TEXT_PRIMARY = "#e6e8ec"
TEXT_SECONDARY = "#8b93a1"
USER_COLOR = "#7fb3ff"
AURA_COLOR = "#5ec8d8"
ERROR_COLOR = "#e0645a"
WARN_COLOR = "#e0b84f"

_STATE_COLORS = {
    AuraState.STARTING: TEXT_SECONDARY,
    AuraState.SLEEPING: TEXT_SECONDARY,
    AuraState.ACTIVE: "#3ddc97",
    AuraState.HEARING: "#3ddc97",
    AuraState.THINKING: WARN_COLOR,
    AuraState.SPEAKING: ACCENT,
    AuraState.WAITING_FOR_CONFIRMATION: "#c88ce0",
    AuraState.WAITING_FOR_BROWSER_CHOICE: "#c88ce0",
    AuraState.EXECUTING: WARN_COLOR,
    AuraState.ERROR: ERROR_COLOR,
    AuraState.STOPPED: TEXT_SECONDARY,
}

_STATE_LABELS = {
    AuraState.STARTING: "STARTING",
    AuraState.SLEEPING: "SLEEPING",
    AuraState.ACTIVE: "ACTIVE",
    AuraState.HEARING: "HEARING",
    AuraState.THINKING: "THINKING",
    AuraState.SPEAKING: "SPEAKING",
    AuraState.WAITING_FOR_CONFIRMATION: "WAITING FOR CONFIRMATION",
    AuraState.WAITING_FOR_BROWSER_CHOICE: "WAITING FOR BROWSER CHOICE",
    AuraState.EXECUTING: "EXECUTING",
    AuraState.ERROR: "ERROR",
    AuraState.STOPPED: "STOPPED",
}


class AuraWindow:
    def __init__(self, root: tk.Tk, agent: AuraAgent):
        self.root = root
        self.agent = agent

        self.root.title("AURA — Personal Desktop Assistant")
        self.root.geometry("560x760")
        self.root.minsize(460, 620)
        self.root.configure(bg=BG)

        memory_manager.init()

        self._anim_phase = 0.0
        self._current_state = AuraState.STARTING

        self._build_layout()
        self._wire_agent_callbacks()
        self._refresh_recent_actions()
        self._animate()
        self._refresh_system_stats()

    # ---------------------------------------------------------------- layout

    def _build_layout(self) -> None:
        # --- header ---
        header = tk.Frame(self.root, bg=BG, pady=10)
        header.pack(fill="x", padx=14)
        tk.Label(header, text="AURA", font=("Segoe UI", 18, "bold"), fg=TEXT_PRIMARY, bg=BG).pack(side="left")
        self.online_var = tk.StringVar(value="● ONLINE")
        tk.Label(header, textvariable=self.online_var, font=("Segoe UI", 10, "bold"),
                 fg=_STATE_COLORS[AuraState.STARTING], bg=BG).pack(side="right")

        # --- central AURA core visual ---
        core_frame = tk.Frame(self.root, bg=BG)
        core_frame.pack(fill="x", pady=(4, 2))
        self.canvas = tk.Canvas(core_frame, width=200, height=140, bg=BG, highlightthickness=0)
        self.canvas.pack()

        self.state_label_var = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self.state_label_var, font=("Segoe UI", 11, "bold"),
                 fg=TEXT_PRIMARY, bg=BG).pack()

        provider_label = self.agent.provider.name.capitalize() if self.agent.provider.is_ai else "Offline (fallback)"
        tk.Label(self.root, text=f"AI: {provider_label}", font=("Segoe UI", 9), fg=TEXT_SECONDARY, bg=BG).pack(pady=(0, 6))

        # --- conversation log ---
        log_frame = tk.Frame(self.root, bg=PANEL)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.log = scrolledtext.ScrolledText(
            log_frame, height=14, wrap="word", state="disabled",
            font=("Segoe UI", 10), bg=PANEL, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY, borderwidth=0, highlightthickness=0,
        )
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.tag_configure("you", foreground=USER_COLOR, font=("Segoe UI", 9, "bold"))
        self.log.tag_configure("aura", foreground=AURA_COLOR, font=("Segoe UI", 9, "bold"))
        self.log.tag_configure("body", foreground=TEXT_PRIMARY)

        # --- confirmation row (hidden unless WAITING_FOR_CONFIRMATION) ---
        self.confirm_frame = tk.Frame(self.root, bg=BG)
        tk.Label(self.confirm_frame, text="Confirm?", fg=WARN_COLOR, bg=BG,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(14, 8))
        tk.Button(self.confirm_frame, text="Yes", bg="#234d3a", fg=TEXT_PRIMARY, relief="flat",
                  command=lambda: self.agent.confirm(True)).pack(side="left", padx=4)
        tk.Button(self.confirm_frame, text="No", bg="#4d2323", fg=TEXT_PRIMARY, relief="flat",
                  command=lambda: self.agent.confirm(False)).pack(side="left", padx=4)
        # Not packed initially — shown by _on_state_change when needed.

        # --- input row ---
        input_frame = tk.Frame(self.root, bg=BG)
        input_frame.pack(fill="x", padx=14, pady=(0, 6))
        self.entry_var = tk.StringVar()
        entry = tk.Entry(input_frame, textvariable=self.entry_var, font=("Segoe UI", 10),
                          bg=PANEL, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                          relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        entry.bind("<Return>", lambda _e: self._on_send())
        tk.Button(input_frame, text="Send", command=self._on_send, bg=ACCENT_DIM, fg=TEXT_PRIMARY,
                  relief="flat", padx=10).pack(side="left", padx=(6, 0))

        # --- start/stop control ---
        self.toggle_button = tk.Button(self.root, text="⏸ STOP LISTENING", command=self._on_toggle_listening,
                                        bg=PANEL, fg=TEXT_PRIMARY, font=("Segoe UI", 10, "bold"),
                                        relief="flat")
        self.toggle_button.pack(fill="x", padx=14, pady=(0, 8), ipady=6)

        # --- recent actions ---
        tk.Label(self.root, text="Recent Actions", font=("Segoe UI", 9, "bold"),
                 fg=TEXT_SECONDARY, bg=BG).pack(anchor="w", padx=14)
        self.recent_list = tk.Listbox(self.root, height=4, font=("Segoe UI", 9), bg=PANEL,
                                       fg=TEXT_PRIMARY, borderwidth=0, highlightthickness=0)
        self.recent_list.pack(fill="x", padx=14, pady=(2, 8))

        # --- bottom status bar ---
        status_bar = tk.Frame(self.root, bg=PANEL)
        status_bar.pack(fill="x", side="bottom")
        self.cpu_var = tk.StringVar(value="CPU --%")
        self.ram_var = tk.StringVar(value="RAM --%")
        self.mic_var = tk.StringVar(value="MIC ●")
        self.ai_var = tk.StringVar(value="AI ●")
        for var, color in ((self.cpu_var, TEXT_SECONDARY), (self.ram_var, TEXT_SECONDARY),
                            (self.mic_var, TEXT_SECONDARY), (self.ai_var, TEXT_SECONDARY)):
            tk.Label(status_bar, textvariable=var, fg=color, bg=PANEL, font=("Segoe UI", 8)).pack(
                side="left", padx=10, pady=4)

    # -------------------------------------------------------- agent wiring

    def _wire_agent_callbacks(self) -> None:
        self.agent.on_state_change = lambda s: self.root.after(0, self._on_state_change, s)
        self.agent.on_message = lambda speaker, text: self.root.after(0, self._append_log, speaker, text)
        self.agent.on_recent_actions_update = lambda: self.root.after(0, self._refresh_recent_actions)

    def start_agent_async(self) -> None:
        """Kicks off agent.start() (which speaks the greeting and may
        block briefly) on a background thread so the UI never freezes."""
        threading.Thread(target=self.agent.start, daemon=True).start()

    # ------------------------------------------------------------- rendering

    def _append_log(self, speaker: str, text: str) -> None:
        self.log.configure(state="normal")
        tag = "you" if speaker.lower() == "you" else "aura"
        self.log.insert("end", f"{speaker}\n", tag)
        self.log.insert("end", f"{text}\n\n", "body")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _on_state_change(self, state: AuraState) -> None:
        self._current_state = state
        color = _STATE_COLORS.get(state, TEXT_SECONDARY)
        self.online_var.set(f"● {_STATE_LABELS.get(state, state.value)}")

        friendly = {
            AuraState.STARTING: "Starting…",
            AuraState.SLEEPING: f"Say \"{self.agent.wake_name.capitalize()}\" to wake me…",
            AuraState.ACTIVE: "I'm listening…",
            AuraState.HEARING: "Hearing you…",
            AuraState.THINKING: "Thinking…",
            AuraState.SPEAKING: "Speaking…",
            AuraState.WAITING_FOR_CONFIRMATION: "Waiting for your confirmation…",
            AuraState.WAITING_FOR_BROWSER_CHOICE: "Which browser should I use?",
            AuraState.EXECUTING: "Working on it…",
            AuraState.ERROR: "Something went wrong — recovering…",
            AuraState.STOPPED: "Listening stopped.",
        }.get(state, state.value)
        self.state_label_var.set(friendly)

        if state == AuraState.WAITING_FOR_CONFIRMATION:
            self.confirm_frame.pack(fill="x", pady=(0, 6), before=self.log.master)
        else:
            self.confirm_frame.pack_forget()

        if state == AuraState.STOPPED:
            self.toggle_button.config(text="▶ START LISTENING")
        else:
            self.toggle_button.config(text="⏸ STOP LISTENING")

        self.mic_var.set("MIC ●" if self.agent.voice_enabled else "MIC ○ (off)")

    def _refresh_recent_actions(self) -> None:
        self.recent_list.delete(0, "end")
        for row in memory_manager.get_recent_actions(limit=6):
            mark = "✓" if row["success"] else "✗"
            self.recent_list.insert("end", f"{mark} {row['action']} — {row['result_message']}")

    def _refresh_system_stats(self) -> None:
        try:
            from app.actions import system_control
            _, cpu_msg = system_control.get_cpu_usage()
            _, ram_msg = system_control.get_ram_usage()
            self.cpu_var.set(cpu_msg.split("about ")[-1].split("%")[0] + "% CPU" if "about" in cpu_msg else "CPU --%")
            self.ram_var.set(ram_msg.split("about ")[-1].split("%")[0] + "% RAM" if "about" in ram_msg else "RAM --%")
        except Exception:
            pass  # status bar is cosmetic — never let it disrupt the app
        self.ai_var.set("AI ●" if self.agent.provider.is_ai else "AI ○ (offline)")
        self.root.after(4000, self._refresh_system_stats)

    # --------------------------------------------------------------- animation

    def _animate(self) -> None:
        import math
        self.canvas.delete("core")

        state = self._current_state
        color = _STATE_COLORS.get(state, TEXT_SECONDARY)
        cx, cy = 100, 70

        if state == AuraState.ACTIVE:
            speed, base, amp = 0.18, 34, 8        # stronger pulse — actively listening for a command
        elif state == AuraState.HEARING:
            speed, base, amp = 0.5, 36, 10          # fastest, biggest pulse — actively capturing your speech
        elif state == AuraState.SLEEPING:
            speed, base, amp = 0.04, 26, 2         # subtle idle pulse — just waiting for the wake word
        elif state == AuraState.THINKING:
            speed, base, amp = 0.35, 30, 5
        elif state == AuraState.SPEAKING:
            speed, base, amp = 0.45, 32, 10
        elif state == AuraState.WAITING_FOR_CONFIRMATION:
            speed, base, amp = 0.25, 30, 6
        elif state == AuraState.WAITING_FOR_BROWSER_CHOICE:
            speed, base, amp = 0.25, 30, 6
        elif state == AuraState.ERROR:
            speed, base, amp = 0.6, 28, 4
        else:
            speed, base, amp = 0.06, 28, 3  # idle/starting/stopped — subtle

        self._anim_phase += speed
        r_outer = base + amp * (0.5 + 0.5 * math.sin(self._anim_phase))
        r_inner = base * 0.55

        self.canvas.create_oval(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                                 outline=color, width=2, tags="core")
        self.canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
                                 fill=color, outline="", stipple="gray50", tags="core")

        self.root.after(80, self._animate)

    # --------------------------------------------------------------- actions

    def _on_send(self) -> None:
        text = self.entry_var.get().strip()
        if not text:
            return
        self.entry_var.set("")
        threading.Thread(target=self.agent.handle_input, args=(text,), daemon=True).start()

    def _on_toggle_listening(self) -> None:
        if self._current_state == AuraState.STOPPED:
            self.agent.start_listening()
        else:
            self.agent.stop_listening()

    # ---------------------------------------------------- backward-compat shim

    def _handle_command(self, text: str) -> None:
        """Kept for callers/tests that used the pre-upgrade direct-call API;
        delegates to the agent, which is now the real source of truth."""
        self.agent.handle_input(text)


def launch(voice_enabled: bool = True) -> None:
    """
    Entry point called by app/main.py. Raises tk.TclError if there's no
    display available (e.g. running headless) — main.py catches this and
    falls back to text mode.

    Preloads the TTS engine (and, if voice is enabled, the Whisper model)
    BEFORE constructing the window/agent, on background threads run in
    parallel and joined with a bound — this is the fix for the startup
    greeting taking 1-2+ seconds to begin: that delay was the TTS
    engine's first-use initialization (pyttsx3's SAPI5 startup, or
    Piper's ONNX model load) happening lazily at the exact moment the
    greeting was first spoken. Paying that cost here, concurrently with
    the rest of startup, means the greeting can begin as soon as the
    engine is ready rather than only starting to load right when it's
    needed.
    """
    import os
    import time as _time

    from app.brain.provider import get_provider
    from app.voice.speech_to_text import SpeechToText
    from app.voice.text_to_speech import create_tts_engine

    memory_manager.init()

    provider = get_provider()
    tts = create_tts_engine()
    stt = SpeechToText() if voice_enabled else None

    t0 = _time.monotonic()
    preload_threads = []
    if hasattr(tts, "preload"):
        preload_threads.append(threading.Thread(target=_safe_preload, args=(tts, "TTS")))
    if stt is not None:
        preload_threads.append(threading.Thread(target=_safe_preload, args=(stt, "Whisper STT")))
    for t in preload_threads:
        t.start()
    for t in preload_threads:
        t.join(timeout=15)
    logger_startup_elapsed = _time.monotonic() - t0
    if preload_threads:
        _log_startup_timing(logger_startup_elapsed)

    confirmation_timeout = float(os.environ.get("AURA_CONFIRMATION_TIMEOUT_SECONDS", "12"))
    session_timeout = float(os.environ.get("AURA_SESSION_TIMEOUT_SECONDS", "120"))
    wake_name = os.environ.get("AURA_NAME", "AURA")

    agent = AuraAgent(
        provider=provider, stt=stt, tts=tts, voice_enabled=voice_enabled,
        wake_name=wake_name, session_timeout=session_timeout,
        confirmation_timeout=confirmation_timeout,
    )

    root = tk.Tk()
    window = AuraWindow(root, agent)
    window.start_agent_async()  # speaks greeting + starts continuous listening, off the UI thread

    def on_close():
        agent.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def _safe_preload(engine, label: str) -> None:
    """Runs engine.preload() and logs failures without raising — startup
    must proceed (falling back to lazy-load-on-first-use) even if
    preloading itself fails for some reason."""
    from app.utils.logger import get_logger
    logger = get_logger()
    try:
        engine.preload()
    except Exception as e:
        logger.warning(f"{label} preload failed (will load lazily on first use instead): {e}")


def _log_startup_timing(elapsed_seconds: float) -> None:
    from app.utils.logger import get_logger
    get_logger().info(f"Voice engine preload took {elapsed_seconds:.2f}s (ran concurrently with window setup).")
