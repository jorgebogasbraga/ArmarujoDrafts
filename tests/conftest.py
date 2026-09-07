"""Shared pytest fixtures."""

import pytest

from tests.helpers import make_state


@pytest.fixture
def draft_state():
    return make_state()
