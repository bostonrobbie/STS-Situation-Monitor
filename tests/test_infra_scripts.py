from __future__ import annotations

import subprocess

import pytest


pytestmark = pytest.mark.unit


def test_check_migration_graph_script() -> None:
    result = subprocess.run(["python", "scripts/check_migration_graph.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_verify_config_surface_script() -> None:
    result = subprocess.run(["python", "scripts/verify_config_surface.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_check_authz_surface_script() -> None:
    result = subprocess.run(["python", "scripts/check_authz_surface.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_check_deployment_surface_script() -> None:
    result = subprocess.run(["python", "scripts/check_deployment_surface.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_check_readme_endpoint_sync_script() -> None:
    result = subprocess.run(["python", "scripts/check_readme_endpoint_sync.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_evaluate_truth_harness_script() -> None:
    result = subprocess.run(["python", "scripts/evaluate_truth_harness.py"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
