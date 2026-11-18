"""The compose files hand NODEGLOW_SSH_DEPLOY to the backend.

The backend reads the gate from its environment; compose passes only the
variables it lists, so a value in .env without this entry never arrives.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.release.yml"])
def test_compose_passes_the_ssh_deploy_gate(name):
    compose = yaml.safe_load((ROOT / name).read_text())
    env = compose["services"]["nodeglow"]["environment"]
    assert env["NODEGLOW_SSH_DEPLOY"] == "${NODEGLOW_SSH_DEPLOY:-auto}"
