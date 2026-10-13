"""Run the Windows wrapper with a fake uv; never authorize or start a server."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows command launcher")


def prepare_launcher(tmp_path, *, fake_uv, exit_code=0):
    project = tmp_path / "project with spaces & ! punctuation"
    project.mkdir()
    launcher = project / "start.cmd"
    shutil.copyfile(ROOT / "start.cmd", launcher)
    log = tmp_path / "uv-call.txt"
    tools = tmp_path / "tools"
    tools.mkdir()
    if fake_uv:
        (tools / "uv.cmd").write_text(
            '@echo off\n'
            '> "%CHATPASS_TEST_LOG%" echo "%CD%"\n'
            '>> "%CHATPASS_TEST_LOG%" echo %*\n'
            'exit /b %CHATPASS_TEST_EXIT%\n',
            encoding="ascii",
        )
    env = os.environ.copy()
    env.update(
        PATH=str(tools) + os.pathsep + str(Path(env["SYSTEMROOT"]) / "System32"),
        PATHEXT=".COM;.EXE;.BAT;.CMD",
        CHATPASS_TEST_LOG=str(log),
        CHATPASS_TEST_EXIT=str(exit_code),
    )
    return launcher, env, log


def run_launcher(launcher, env, cwd):
    return subprocess.run(
        f'"{env["COMSPEC"]}" /d /v:off /c call "{launcher}" --no-browser --port 19841',
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_windows_launcher_directory_arguments_and_exit_code(tmp_path, exit_code):
    launcher, env, log = prepare_launcher(tmp_path, fake_uv=True, exit_code=exit_code)
    result = run_launcher(launcher, env, tmp_path)
    assert result.returncode == exit_code, result.stdout + result.stderr
    cwd, arguments = log.read_text().splitlines()
    assert Path(cwd.strip('"')) == launcher.parent
    assert arguments == (
        "run --locked --no-dev --system-certs chatpass web --no-browser --port 19841"
    )
    assert "explicit action" in result.stdout
    if exit_code:
        assert f"exited with code {exit_code}" in result.stdout
    else:
        assert "exited with code" not in result.stdout


def test_windows_launcher_missing_uv_has_actionable_error(tmp_path):
    launcher, env, log = prepare_launcher(tmp_path, fake_uv=False)
    result = run_launcher(launcher, env, tmp_path)
    assert result.returncode == 1
    assert "uv was not found" in result.stdout
    assert "Install uv" in result.stdout
    assert not log.exists()
