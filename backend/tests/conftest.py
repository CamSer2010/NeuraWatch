"""Shared pytest fixtures.

`anyio_backend` locks every `@pytest.mark.anyio` test to the stdlib
asyncio runner. Without this the anyio plugin parameterizes every
async test across both `asyncio` and `trio`, and we don't run Trio.
"""
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
