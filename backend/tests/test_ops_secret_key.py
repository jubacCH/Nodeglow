"""The startup warning when the encryption key lives next to the data."""
import logging

import config


def _messages(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records]


def test_silent_when_key_comes_from_env(monkeypatch, caplog):
    monkeypatch.setattr(config, "SECRET_KEY_SOURCE", "env")
    with caplog.at_level(logging.DEBUG, logger="config"):
        config.warn_about_secret_key()
    assert caplog.records == []


def test_warns_when_key_is_read_from_data_dir(monkeypatch, caplog):
    monkeypatch.setattr(config, "SECRET_KEY_SOURCE", "file")
    with caplog.at_level(logging.DEBUG, logger="config"):
        config.warn_about_secret_key()
    msgs = _messages(caplog)
    assert all(level == logging.WARNING for level, _ in msgs)
    text = "\n".join(m for _, m in msgs)
    assert "SECRET_KEY is not set" in text
    assert "SEPARATELY" in text
    # One record per line keeps JSON logs one object per line.
    assert all("\n" not in m for _, m in msgs)


def test_errors_when_a_new_key_was_generated(monkeypatch, caplog):
    monkeypatch.setattr(config, "SECRET_KEY_SOURCE", "generated")
    with caplog.at_level(logging.DEBUG, logger="config"):
        config.warn_about_secret_key()
    msgs = _messages(caplog)
    assert all(level == logging.ERROR for level, _ in msgs)
    assert any("NEW key was generated" in m for _, m in msgs)


def test_source_is_tracked(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRET_KEY_FILE", tmp_path / ".secret_key")
    # get_secret_key() writes these module globals; restore them afterwards.
    monkeypatch.setattr(config, "SECRET_KEY_SOURCE", config.SECRET_KEY_SOURCE)
    monkeypatch.setattr(config, "SECRET_KEY_FROM_ENV", config.SECRET_KEY_FROM_ENV)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    config.get_secret_key()
    assert config.SECRET_KEY_SOURCE == "generated"
    config.get_secret_key()
    assert config.SECRET_KEY_SOURCE == "file"
    monkeypatch.setenv("SECRET_KEY", "abc")
    assert config.get_secret_key() == "abc"
    assert config.SECRET_KEY_SOURCE == "env"
    assert config.SECRET_KEY_FROM_ENV is True
