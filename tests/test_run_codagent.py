import os
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "run_codagent.sh"


def _base_env(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "RUN_CODAGENT_LOG_DIR": str(tmp_path / "logs"),
            "RUN_CODAGENT_LOCK_FILE": str(tmp_path / "run_codagent.lock"),
            "RUN_CODAGENT_RESTART_DELAY": "0",
        }
    )
    return env


def test_run_codagent_single_iteration(tmp_path):
    dummy_script = tmp_path / "dummy_agent.py"
    dummy_script.write_text("print('dummy run')\n", encoding="utf-8")

    env = _base_env(tmp_path)
    env.update(
        {
            "RUN_CODAGENT_PYTHON_SCRIPT": str(dummy_script),
            "RUN_CODAGENT_MAX_RESTARTS": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout

    log_file = Path(env["RUN_CODAGENT_LOG_DIR"]) / "codagent_mccay.log"
    assert log_file.exists()
    log_contents = log_file.read_text(encoding="utf-8")
    assert "run_codagent.sh started" in log_contents
    assert "Launching codagent_mccay.py" in log_contents
    assert "exited with status 0" in log_contents
    assert "Reached maximum restart limit" in log_contents


def test_run_codagent_rejects_duplicate_instance(tmp_path):
    sleepy_script = tmp_path / "sleepy_agent.py"
    sleepy_script.write_text(
        "import time\ntime.sleep(2)\nprint('done')\n",
        encoding="utf-8",
    )

    env = _base_env(tmp_path)
    env.update(
        {
            "RUN_CODAGENT_PYTHON_SCRIPT": str(sleepy_script),
            "RUN_CODAGENT_MAX_RESTARTS": "1",
        }
    )

    first_proc = subprocess.Popen(
        ["bash", str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        time.sleep(0.2)

        second_result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )

        assert second_result.returncode == 0
        assert "already running" in second_result.stdout

    finally:
        if first_proc.poll() is None:
            first_proc.terminate()
            try:
                first_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                first_proc.kill()
                first_proc.wait(timeout=5)

    first_stdout = first_proc.stdout.read() if first_proc.stdout else ""
    assert "run_codagent.sh started" in first_stdout
