import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_verify_migrations_script_runs() -> None:
    completed = subprocess.run(["bash", "scripts/verify_migrations.sh"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "Migration check passed." in completed.stdout
