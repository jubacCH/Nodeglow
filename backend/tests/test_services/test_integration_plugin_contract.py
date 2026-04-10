"""Contract tests for every BaseIntegration subclass.

These run once per registered plugin and assert the plugin satisfies the
core contract — without making any network calls. The point is to catch
the kind of bug where someone forgets to set `display_name`, ships a
ConfigField without a label, or breaks the parse_alerts contract by
returning something that isn't a list.

A real "smoke test" against the upstream API still needs network access
or per-plugin mocks; that's a separate (much heavier) layer. These
contract tests are the cheap, fast layer that runs in CI on every commit.
"""
from __future__ import annotations

import inspect

import pytest

from integrations import get_registry
from integrations._base import (
    Alert,
    BaseIntegration,
    CollectorResult,
    ConfigField,
)


# A list of (plugin_name, plugin_class) tuples — one per registered plugin.
# Pytest's `parametrize` will produce one test row per plugin so failures
# pinpoint exactly which plugin is broken.
_PLUGINS = sorted(get_registry().items())


def test_at_least_one_plugin_registered():
    """Sanity check — auto-discovery should find at least the bundled plugins."""
    assert len(_PLUGINS) >= 5, (
        f"Expected at least 5 integration plugins to be auto-discovered, "
        f"got {len(_PLUGINS)}"
    )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_has_required_metadata(name, cls):
    """`name`, `display_name`, `description` must all be set and non-empty."""
    assert cls.name == name, f"{cls.__name__}.name does not match registry key"
    assert cls.name, f"{cls.__name__} missing `name`"
    assert cls.display_name, f"{cls.__name__} missing `display_name`"
    assert cls.description, f"{cls.__name__} missing `description`"
    assert isinstance(cls.color, str) and cls.color, (
        f"{cls.__name__}.color must be a non-empty string"
    )
    assert isinstance(cls.single_instance, bool), (
        f"{cls.__name__}.single_instance must be a bool"
    )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_name_is_url_safe(name, cls):
    """Plugin names become URL slugs — no spaces, no uppercase, no slashes."""
    assert cls.name.islower(), f"{cls.__name__}.name must be lowercase"
    assert " " not in cls.name, f"{cls.__name__}.name must not contain spaces"
    assert "/" not in cls.name, f"{cls.__name__}.name must not contain slashes"
    # Allow letters, digits, underscore, hyphen
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    assert set(cls.name) <= allowed, (
        f"{cls.__name__}.name contains invalid characters: {set(cls.name) - allowed}"
    )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_config_fields_are_well_formed(name, cls):
    """Every ConfigField must have key, label, and a known field_type."""
    assert isinstance(cls.config_fields, list), (
        f"{cls.__name__}.config_fields must be a list"
    )
    keys_seen = set()
    valid_types = {"text", "password", "number", "checkbox", "url", "select", "textarea"}
    for f in cls.config_fields:
        assert isinstance(f, ConfigField), (
            f"{cls.__name__}.config_fields contains a non-ConfigField item"
        )
        assert f.key, f"{cls.__name__} has a ConfigField with empty key"
        assert f.label, f"{cls.__name__}.{f.key} has empty label"
        assert f.field_type in valid_types, (
            f"{cls.__name__}.{f.key} has unknown field_type: {f.field_type!r} "
            f"(allowed: {sorted(valid_types)})"
        )
        # No duplicate keys
        assert f.key not in keys_seen, (
            f"{cls.__name__} has duplicate ConfigField key: {f.key}"
        )
        keys_seen.add(f.key)
        # Select fields must declare options
        if f.field_type == "select":
            assert f.options, (
                f"{cls.__name__}.{f.key} is a select but has no options"
            )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_collect_is_async(name, cls):
    """`collect()` must be a coroutine function — caller awaits it."""
    assert inspect.iscoroutinefunction(cls.collect), (
        f"{cls.__name__}.collect must be `async def`"
    )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_health_check_is_async(name, cls):
    """Same for the optional health_check (defaults to async on the base class)."""
    assert inspect.iscoroutinefunction(cls.health_check), (
        f"{cls.__name__}.health_check must be `async def`"
    )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_instantiable_with_empty_config(name, cls):
    """Should be possible to construct with no config — caller does this for
    the 'available integrations' listing before the user has filled anything in."""
    try:
        instance = cls(config={})
    except Exception as exc:
        pytest.fail(
            f"{cls.__name__}({{}}) raised {type(exc).__name__}: {exc}. "
            f"Plugins must tolerate empty config at construction time."
        )
    assert isinstance(instance, BaseIntegration)


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_parse_alerts_returns_list(name, cls):
    """parse_alerts must always return a list (even if empty) — never None."""
    instance = cls(config={})
    # Pass deliberately empty / minimal data shapes
    for sample in ({}, {"foo": "bar"}, {"nodes": []}, {"devices": []}):
        try:
            result = instance.parse_alerts(sample)
        except Exception as exc:
            pytest.fail(
                f"{cls.__name__}.parse_alerts({sample!r}) raised "
                f"{type(exc).__name__}: {exc}"
            )
        assert isinstance(result, list), (
            f"{cls.__name__}.parse_alerts must return list, got {type(result).__name__}"
        )
        for item in result:
            assert isinstance(item, Alert), (
                f"{cls.__name__}.parse_alerts returned non-Alert: {type(item).__name__}"
            )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_get_dashboard_summary_shape(name, cls):
    """get_dashboard_summary returns either None or a dict."""
    instance = cls(config={})
    for sample in ({}, {"x": 1}):
        try:
            result = instance.get_dashboard_summary(sample)
        except Exception as exc:
            pytest.fail(
                f"{cls.__name__}.get_dashboard_summary({sample!r}) raised "
                f"{type(exc).__name__}: {exc}"
            )
        assert result is None or isinstance(result, dict), (
            f"{cls.__name__}.get_dashboard_summary must return None or dict, "
            f"got {type(result).__name__}"
        )


@pytest.mark.parametrize("name,cls", _PLUGINS)
def test_plugin_get_detail_context_returns_dict(name, cls):
    """get_detail_context must always return a dict (even empty)."""
    instance = cls(config={})
    for sample in ({}, {"x": 1}):
        try:
            result = instance.get_detail_context(sample, {})
        except Exception as exc:
            pytest.fail(
                f"{cls.__name__}.get_detail_context({sample!r}, {{}}) raised "
                f"{type(exc).__name__}: {exc}"
            )
        assert isinstance(result, dict), (
            f"{cls.__name__}.get_detail_context must return dict, "
            f"got {type(result).__name__}"
        )
