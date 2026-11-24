"""Regression coverage for pytest's Windows temporary-directory isolation."""

import subprocess
import sys
from pathlib import Path
from shutil import copyfile

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows temp cleanup regression")
@pytest.mark.parametrize("explicit_base", [False, True])
def test_windows_test_session_avoids_shared_temp_pool(
    tmp_path, monkeypatch, explicit_base
):
    suite = tmp_path / "suite"
    suite.mkdir()
    copyfile(Path(__file__).with_name("conftest.py"), suite / "conftest.py")
    (suite / "test_probe.py").write_text(
        "def test_temp_location(tmp_path):\n    print(f'MARK_TEST_TEMP={tmp_path}')\n",
        encoding="utf-8",
    )

    shared_pool = tmp_path / "shared-pytest-temp"
    shared_pool.mkdir()
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(shared_pool))
    command = [sys.executable, "-m", "pytest", "-q", "-s"]
    if explicit_base:
        custom = tmp_path / "custom-base"
        command.extend(["--basetemp", str(custom)])

    result = subprocess.run(
        command,
        cwd=suite,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reported = next(
        line.removeprefix("MARK_TEST_TEMP=")
        for line in result.stdout.splitlines()
        if line.startswith("MARK_TEST_TEMP=")
    )
    actual = Path(reported)
    assert not list(shared_pool.iterdir())
    if explicit_base:
        assert actual.is_relative_to(custom.resolve())
    else:
        assert any(part.startswith("mark-pytest-") for part in actual.parts)
        assert not actual.exists()
