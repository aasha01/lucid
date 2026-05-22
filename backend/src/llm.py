"""
Ollama HTTP client for Lucid.

Wraps the Ollama REST API (default: http://localhost:11434) to provide
chat completion (with optional streaming) and embedding generation.

Ollama must be running locally. Models must be pulled in advance:
    ollama pull qwen2.5:14b
    ollama pull llama3.1:8b
    ollama pull nomic-embed-text
"""
from __future__ import annotations

import json
from typing import Generator, Iterable

import requests


class OllamaClient:
    """Thin client around the Ollama REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        chat_model: str = "qwen2.5:14b",
        embed_model: str = "nomic-embed-text",
        timeout: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout = timeout

    # ---------- Health ----------
    def ping(self) -> bool:
        """Return True if Ollama is reachable."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        """Return names of locally available models."""
        r = requests.get(f"{self.base_url}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    # ---------- Chat ----------
    def chat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        num_ctx: int = 8192,
    ) -> str:
        """Single-shot chat completion (non-streaming). Returns the full text."""
        payload = {
            "model": model or self.chat_model,
            "messages": self._build_messages(prompt, system),
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }
        r = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    def chat_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        num_ctx: int = 8192,
    ) -> Generator[str, None, None]:
        """Streaming chat completion. Yields text chunks as they arrive."""
        payload = {
            "model": model or self.chat_model,
            "messages": self._build_messages(prompt, system),
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }
        with requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "message" in data and "content" in data["message"]:
                    yield data["message"]["content"]
                if data.get("done"):
                    break

    @staticmethod
    def _build_messages(prompt: str, system: str | None) -> list[dict]:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    # ---------- Embeddings ----------
    def embed(self, text: str, model: str | None = None) -> list[float]:
        """Generate embedding for a single text."""
        payload = {
            "model": model or self.embed_model,
            "prompt": text,
        }
        r = requests.post(
            f"{self.base_url}/api/embeddings",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["embedding"]

    def embed_batch(self, texts: Iterable[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings for many texts. Ollama doesn't batch natively,
        so we loop. Good enough for paper-sized workloads."""
        return [self.embed(t, model=model) for t in texts]


# Convenience singleton (created lazily by callers)
_default_client: OllamaClient | None = None


def get_client() -> OllamaClient:
    global _default_client
    if _default_client is None:
        _default_client = OllamaClient()
    return _default_client


if __name__ == "__main__":
    # Quick smoke test: run `python -m backend.src.llm` from project root
    c = OllamaClient()
    print("Ollama reachable:", c.ping())
    print("Available models:", c.list_models())
    print("\nTest chat:")
    print(c.chat("Say 'Lucid is alive' in exactly 3 words.", temperature=0.0))
    print("\nTest embedding (first 5 dims):")
    print(c.embed("hello world")[:5])
