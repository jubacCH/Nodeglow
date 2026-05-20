"""Tests for statistical helpers."""
import pytest

from services._stats import wilson_lower_bound


def test_wilson_zero_total_returns_zero():
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_zero_successes_returns_zero():
    assert wilson_lower_bound(0, 100) == pytest.approx(0.0, abs=0.001)


def test_wilson_5_of_5_far_below_one():
    # Naive ratio would be 1.0; Wilson punishes small samples.
    lb = wilson_lower_bound(5, 5)
    assert 0.55 < lb < 0.58


def test_wilson_20_of_20_higher_but_still_below_one():
    lb = wilson_lower_bound(20, 20)
    assert 0.83 < lb < 0.85


def test_wilson_100_of_100_close_to_one():
    lb = wilson_lower_bound(100, 100)
    assert 0.96 < lb < 0.97


def test_wilson_5_of_10_significantly_below_half():
    lb = wilson_lower_bound(5, 10)
    assert 0.22 < lb < 0.25


def test_wilson_monotonic_in_sample_size():
    # Same observed ratio 100%, more samples → higher lower bound.
    assert wilson_lower_bound(5, 5) < wilson_lower_bound(10, 10) < wilson_lower_bound(50, 50)
