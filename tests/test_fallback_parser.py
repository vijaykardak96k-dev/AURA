"""tests/test_fallback_parser.py — deterministic parser, including phrasing synonyms."""

from app.brain.fallback_parser import parse_fallback_to_dict
from app.brain.schemas import parse_ai_response


def _first_action(text: str):
    raw = parse_fallback_to_dict(text)
    items = parse_ai_response(raw)
    return items[0]


def test_open_chrome_variants_map_to_same_action():
    phrasings = ["open chrome", "launch chrome", "start chrome", "please open chrome"]
    for phrase in phrasings:
        action = _first_action(phrase)
        assert action.valid, phrase
        assert action.action == "OPEN_APP"
        assert action.parameters["app"] == "chrome"


def test_open_notepad():
    action = _first_action("open notepad")
    assert action.action == "OPEN_APP"
    assert action.parameters["app"] == "notepad"


def test_open_calculator():
    action = _first_action("open calculator")
    assert action.action == "OPEN_APP"
    assert action.parameters["app"] == "calculator"


def test_open_downloads_folder():
    action = _first_action("open my downloads folder")
    assert action.action == "OPEN_FOLDER"
    assert action.parameters["path"].lower() == "downloads"


def test_create_folder_called_college():
    action = _first_action("create a folder called College")
    assert action.action == "CREATE_FOLDER"
    assert action.parameters["path"] == "Desktop/College"


def test_take_screenshot():
    action = _first_action("take a screenshot")
    assert action.action == "SCREENSHOT"


def test_cpu_usage_variants():
    for phrase in ["what's my cpu usage", "show cpu usage", "check cpu"]:
        action = _first_action(phrase)
        assert action.action == "CPU_INFO", phrase


def test_ram_usage_variants():
    for phrase in ["how much ram am i using", "show ram usage", "check memory"]:
        action = _first_action(phrase)
        assert action.action == "RAM_INFO", phrase


def test_battery_percentage():
    action = _first_action("what's my battery percentage")
    assert action.action == "BATTERY_INFO"


def test_lock_computer():
    action = _first_action("lock my computer")
    assert action.action == "LOCK_SYSTEM"


def test_shutdown_requires_confirmation():
    action = _first_action("shutdown my computer")
    assert action.action == "SHUTDOWN_SYSTEM"
    assert action.requires_confirmation is True


def test_scheduled_shutdown_extracts_minutes():
    action = _first_action("shutdown my computer in 10 minutes")
    assert action.action == "SCHEDULE_SHUTDOWN"
    assert action.parameters["minutes"] == 10


def test_cancel_shutdown():
    action = _first_action("cancel shutdown")
    assert action.action == "CANCEL_SHUTDOWN"


def test_delete_requires_confirmation():
    action = _first_action("delete test.txt")
    assert action.action == "DELETE_FILE"
    assert action.requires_confirmation is True


def test_unrecognized_command_returns_unknown():
    action = _first_action("compose a symphony about clouds")
    assert action.action == "UNKNOWN"
