"""Pytest plugin entry point. Tests import the checks they want to run."""

from tegel.dataserver.testing.conformance import assert_read_only_api

__all__ = ["assert_read_only_api"]
