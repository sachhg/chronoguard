"""Shared fixtures.

The Ollama fixtures skip rather than fail when there's no server, so the fast
suite stays runnable on a machine that has never heard of Ollama.
"""

from __future__ import annotations

import pytest

from chronoguard.ollama import OllamaClient


@pytest.fixture(scope="session")
def ollama_client() -> OllamaClient:
    client = OllamaClient()
    if not client.is_available():
        pytest.skip(f"no Ollama server at {client.host}, start one with `ollama serve`")
    return client


@pytest.fixture(scope="session")
def ollama_model(ollama_client: OllamaClient) -> str:
    names = ollama_client.model_names()
    if not names:
        pytest.skip("Ollama is up but has no models installed, try `ollama pull gemma3:4b`")
    return ollama_client.pick_model()


@pytest.fixture(scope="session")
def tool_capable_model(ollama_client: OllamaClient) -> str:
    for name in sorted(ollama_client.model_names()):
        if ollama_client.supports_tools(name):
            return name
    installed = ", ".join(sorted(ollama_client.model_names())) or "none"
    pytest.skip(
        f"no installed model supports native tool calling (have: {installed}), "
        "try `ollama pull qwen3:4b`"
    )
