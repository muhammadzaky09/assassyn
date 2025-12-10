"""Unit test for backend validation in elaborate function."""

import pytest
from assassyn.frontend import SysBuilder
from assassyn.backend import elaborate


def test_backend_validation_both_disabled():
    """Test that elaborate raises ValueError when both backends are disabled."""
    sys = SysBuilder("test_system")

    with pytest.raises(ValueError, match="At least one backend must be enabled"):
        elaborate(sys, simulator=False, verilog=False)


def test_backend_validation_simulator_only():
    """Test that elaborate works with only simulator backend enabled."""
    sys = SysBuilder("test_system")

    # This should not raise an error
    # We'll use enable_cache=False to avoid caching issues in tests
    try:
        elaborate(sys, simulator=True, verilog=False, enable_cache=False)
    except ValueError as e:
        if "At least one backend must be enabled" in str(e):
            pytest.fail("Should not raise ValueError when simulator is enabled")
        # Other ValueErrors are acceptable (e.g., from actual elaboration)


def test_backend_validation_verilog_only():
    """Test that elaborate works with only verilog backend enabled."""
    sys = SysBuilder("test_system")

    # This should not raise an error
    # We'll use enable_cache=False to avoid caching issues in tests
    try:
        elaborate(sys, simulator=False, verilog=True, enable_cache=False)
    except ValueError as e:
        if "At least one backend must be enabled" in str(e):
            pytest.fail("Should not raise ValueError when verilog is enabled")
        # Other ValueErrors are acceptable (e.g., from actual elaboration)


def test_backend_validation_both_enabled():
    """Test that elaborate works with both backends enabled."""
    sys = SysBuilder("test_system")

    # This should not raise an error
    # We'll use enable_cache=False to avoid caching issues in tests
    try:
        elaborate(sys, simulator=True, verilog=True, enable_cache=False)
    except ValueError as e:
        if "At least one backend must be enabled" in str(e):
            pytest.fail("Should not raise ValueError when both backends are enabled")
        # Other ValueErrors are acceptable (e.g., from actual elaboration)
