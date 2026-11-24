from unittest.mock import patch
from core.action_confirmation import confirm_result


def test_folder_requires_matching_active_explorer_location(tmp_path):
    from actions import win_manager
    with patch.object(win_manager, "active_window", return_value={"hwnd": 1, "title": "Explorer", "exe": "explorer.exe"}), \
         patch("core.action_confirmation.explorer_active_path", return_value=str(tmp_path)):
        result = confirm_result("file_controller", {"action": "open", "path": str(tmp_path)}, "[UNVERIFIED]", timeout=0)
    assert result.startswith("[DONE]")


def test_wrong_folder_is_not_success(tmp_path):
    from actions import win_manager
    with patch.object(win_manager, "active_window", return_value={"hwnd": 1, "title": "Explorer", "exe": "explorer.exe"}), \
         patch("core.action_confirmation.explorer_active_path", return_value=str(tmp_path.parent)):
        result = confirm_result("file_controller", {"action": "open", "path": str(tmp_path)}, "[DONE]", timeout=0)
    assert result.startswith("[UNVERIFIED]")


def test_missing_folder_fails(tmp_path):
    result = confirm_result("file_controller", {"action": "open", "path": str(tmp_path / "missing")}, "[UNVERIFIED]", timeout=0)
    assert result.startswith("[FAILED]")


def test_application_requires_foreground_match():
    from actions import win_manager
    with patch.object(win_manager, "active_window", return_value={"title": "Browser", "exe": "browser.exe"}):
        result = confirm_result("open_app", {"app_name": "Notepad"}, "[DONE]", timeout=0)
    assert result.startswith("[UNVERIFIED]")
