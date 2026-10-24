"""Clean-process smoke tests for deployment-critical module imports."""

import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _import_in_clean_process(module: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.setdefault("DATABASE_URL", "sqlite:///:memory:")
    environment.setdefault("SECRET_KEY", "startup-smoke-test-only")
    return subprocess.run(
        [sys.executable, "-c", f"import {module}; print('{module} import OK')"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_models_imports_in_clean_process():
    result = _import_in_clean_process("models")
    assert result.returncode == 0, result.stderr
    assert "models import OK" in result.stdout


def test_main_imports_in_clean_process():
    result = _import_in_clean_process("main")
    assert result.returncode == 0, result.stderr
    assert "main import OK" in result.stdout
