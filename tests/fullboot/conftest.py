"""Shared full-boot fixture: one booted AstrBot per test session."""

from __future__ import annotations

import pytest

from .harness import AstrBotHarness


@pytest.fixture(scope="session")
def harness():
    instance = AstrBotHarness.start()
    yield instance
    instance.stop()
