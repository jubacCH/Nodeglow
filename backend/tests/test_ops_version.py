"""config.get_version(): one version, sourced from the repo-root VERSION file."""
from pathlib import Path

import config


def test_env_wins(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert config.get_version() == "9.9.9"


def test_reads_first_non_comment_line(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_VERSION", raising=False)
    f = tmp_path / "VERSION"
    f.write_text("# generated\n\n2.3.4\n# 2026-04-03T06:28:41Z\n")
    monkeypatch.setattr(config, "_version_candidates", lambda: [tmp_path / "nope", f])
    assert config.get_version() == "2.3.4"


def test_fallback_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr(config, "_version_candidates", lambda: [tmp_path / "nope"])
    assert config.get_version() == "0.0.0+unknown"


def test_repo_version_file_is_found(monkeypatch):
    """In a checkout the repo-root VERSION is picked up without any env."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    repo_version = Path(config.__file__).resolve().parent.parent / "VERSION"
    expected = repo_version.read_text().splitlines()[0].strip()
    assert config.get_version() == expected
