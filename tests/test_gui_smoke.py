"""
tests/test_gui_smoke.py — headless Tkinter GUI smoke tests for the
agent-driven redesign. Uses a fake AI provider and no voice, so these
don't need real hardware — they DO exercise a real Tk window (skipped if
no display is available; run via `xvfb-run -a python -m pytest` to
include them, same as before this upgrade).

What this covers: the window actually constructs and renders with the
new dark theme/canvas/status-bar layout, real commands flow through to
the conversation log with the right speaker tags, the recent-actions
panel updates, and — the important behavior change from this upgrade —
dangerous actions now go through AuraAgent's WAITING_FOR_CONFIRMATION
state with Yes/No buttons calling agent.confirm(), not a messagebox
dialog.

What this does NOT cover: visual appearance, animation smoothness, or
real voice/TTS — see tests/test_agent.py for the state-machine logic in
isolation and README.md for what still needs a look on real Windows.
"""

import threading
import time

import pytest

tk = pytest.importorskip("tkinter")


def _tk_available() -> bool:
    try:
        root = tk.Tk()
        root.destroy()
        return True
    except tk.TclError:
        return False


pytestmark = pytest.mark.skipif(
    not _tk_available(), reason="No display available for Tkinter (set DISPLAY or run under xvfb-run)."
)


class FakeProvider:
    name = "fake"
    is_ai = False

    def __init__(self, response=None):
        self.response = response or {"actions": [{"action": "UNKNOWN"}]}

    def generate_actions(self, user_text, context=""):
        return self.response

    def is_available(self):
        return True


@pytest.fixture()
def window_and_agent(tmp_path, monkeypatch):
    from app.memory import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_gui.db")

    from app.core.agent import AuraAgent
    from app.ui.main_window import AuraWindow

    provider = FakeProvider()
    agent = AuraAgent(provider=provider, stt=None, tts=None, voice_enabled=False, confirmation_timeout=1)

    root = tk.Tk()
    win = AuraWindow(root, agent)
    agent.start()  # synchronous here (no TTS) — fine to call directly in a test
    root.update()

    yield win, agent, provider

    agent.shutdown()
    root.destroy()


def _set_response(provider, response):
    provider.response = response


def test_window_constructs_and_renders(window_and_agent):
    win, agent, provider = window_and_agent
    assert win.root.title() == "AURA — Personal Desktop Assistant"


def test_greeting_appears_in_log_on_start(window_and_agent):
    win, agent, provider = window_and_agent
    log_text = win.log.get("1.0", "end")
    assert "AURA" in log_text
    assert "welcome" in log_text.lower()


def test_safe_action_flows_through_to_log(window_and_agent):
    win, agent, provider = window_and_agent
    _set_response(provider, {"actions": [{"action": "CPU_INFO"}]})

    agent.handle_input("what is my cpu usage")
    win.root.update()

    log_text = win.log.get("1.0", "end")
    assert "You" in log_text
    assert "what is my cpu usage" in log_text


def test_create_folder_updates_recent_actions_panel(window_and_agent, tmp_path, monkeypatch):
    win, agent, provider = window_and_agent
    from app.utils import paths
    monkeypatch.setitem(paths.KNOWN_FOLDERS, "desktop", tmp_path)

    _set_response(provider, {"actions": [{"action": "CREATE_FOLDER", "parameters": {"path": "Desktop/GuiTest"}}]})
    agent.handle_input("create a folder called GuiTest")
    win.root.update()

    recent = win.recent_list.get(0, "end")
    assert any("CREATE_FOLDER" in row for row in recent)
    assert (tmp_path / "GuiTest").exists()


def test_dangerous_action_shows_confirm_buttons_and_no_declines(window_and_agent, tmp_path, monkeypatch):
    """
    Drives this through a real root.mainloop() rather than manual
    root.update() polling: on_state_change/on_message are invoked via
    root.after(0, ...) from the background command thread, and Tkinter
    only permits that cross-thread marshalling while mainloop() is
    actually running — the same reason a real deployed AURA works but a
    bare update()-polling loop (with no mainloop() at all) would raise
    "main thread is not in main loop".
    """
    win, agent, provider = window_and_agent
    from app.utils import paths
    monkeypatch.setitem(paths.KNOWN_FOLDERS, "desktop", tmp_path)
    (tmp_path / "todelete.txt").write_text("x")

    _set_response(provider, {"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/todelete.txt"}}]})

    from app.core.agent import AuraState
    observed = {}
    thread_holder = {}

    def start_command():
        t = threading.Thread(target=lambda: agent.handle_input("delete todelete.txt"), daemon=True)
        thread_holder["t"] = t
        t.start()
        win.root.after(20, poll_for_waiting, time.time() + 5)

    def poll_for_waiting(deadline):
        if agent.state == AuraState.WAITING_FOR_CONFIRMATION:
            observed["state"] = agent.state
            observed["confirm_visible"] = win.confirm_frame.winfo_ismapped()
            win.agent.confirm(False)  # simulates clicking "No"
            win.root.after(300, poll_for_finished, time.time() + 5)
            return
        if time.time() >= deadline:
            win.root.quit()
            return
        win.root.after(20, poll_for_waiting, deadline)

    def poll_for_finished(deadline):
        if agent.state != AuraState.WAITING_FOR_CONFIRMATION and not thread_holder["t"].is_alive():
            win.root.quit()
            return
        if time.time() >= deadline:
            win.root.quit()
            return
        win.root.after(20, poll_for_finished, deadline)

    win.root.after(50, start_command)
    win.root.mainloop()

    if thread_holder.get("t"):
        thread_holder["t"].join(timeout=3)  # don't let this thread bleed into the next test

    assert observed.get("state") == AuraState.WAITING_FOR_CONFIRMATION
    assert bool(observed.get("confirm_visible"))  # winfo_ismapped() returns Tcl 1/0, not Python True/False
    assert (tmp_path / "todelete.txt").exists()  # not deleted — declined


def test_dangerous_action_confirmed_via_button_executes(window_and_agent, tmp_path, monkeypatch):
    win, agent, provider = window_and_agent
    from app.utils import paths
    monkeypatch.setitem(paths.KNOWN_FOLDERS, "desktop", tmp_path)
    (tmp_path / "todelete2.txt").write_text("x")

    _set_response(provider, {"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/todelete2.txt"}}]})

    from app.core.agent import AuraState
    thread_holder = {}

    def start_command():
        t = threading.Thread(target=lambda: agent.handle_input("delete todelete2.txt"), daemon=True)
        thread_holder["t"] = t
        t.start()
        win.root.after(20, poll_for_waiting, time.time() + 5)

    def poll_for_waiting(deadline):
        if agent.state == AuraState.WAITING_FOR_CONFIRMATION:
            win.agent.confirm(True)  # simulates clicking "Yes"
            win.root.after(300, poll_for_finished, time.time() + 5)
            return
        if time.time() >= deadline:
            win.root.quit()
            return
        win.root.after(20, poll_for_waiting, deadline)

    def poll_for_finished(deadline):
        if agent.state != AuraState.WAITING_FOR_CONFIRMATION and not thread_holder["t"].is_alive():
            win.root.quit()
            return
        if time.time() >= deadline:
            win.root.quit()
            return
        win.root.after(20, poll_for_finished, deadline)

    win.root.after(50, start_command)
    win.root.mainloop()

    if thread_holder.get("t"):
        thread_holder["t"].join(timeout=3)  # don't let this thread bleed into the next test

    assert not (tmp_path / "todelete2.txt").exists()  # actually deleted


def test_unknown_command_does_not_crash(window_and_agent):
    win, agent, provider = window_and_agent
    _set_response(provider, {"actions": [{"action": "UNKNOWN"}]})
    agent.handle_input("compose a symphony about clouds")
    win.root.update()
    log_text = win.log.get("1.0", "end")
    assert "AURA" in log_text


def test_toggle_button_reflects_stop_and_start(window_and_agent):
    win, agent, provider = window_and_agent
    assert "STOP" in win.toggle_button["text"]

    agent.stop_listening()
    win.root.update()
    assert "START" in win.toggle_button["text"]

    agent.start_listening()
    win.root.update()
    assert "STOP" in win.toggle_button["text"]
