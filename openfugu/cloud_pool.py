#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Cloud worker pool abstraction for the dual-model
# product. Unified over litellm so any provider backend works behind one API.
"""
cloud_pool.py — CloudWorkerPool: call cloud models by config worker id.

This is the cloud-side generalization of mini.py's LiteLLMWorker. Instead of
binding agent slots positionally to a list of models, a CloudWorkerPool is
built from a FuguConfig and dispatches by worker_id, honoring per-worker
max_tokens / temperature. Credentials are NEVER in code — litellm resolves
them from the environment (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,
FUGU_API_KEY, FUGU_BASE_URL, ...).

Two implementations share one protocol:
  - CloudWorkerPool       : real litellm.completion calls
  - FakeCloudWorkerPool   : deterministic offline stand-in for tests (no API key)

Both implement call(worker_id, messages, role="", context=None) -> str.
"""
from __future__ import annotations
import os, time
from typing import Callable

try:
    from .config import FuguConfig          # package import (openfugu.cloud_pool)
except ImportError:                         # pragma: no cover - script mode
    from config import FuguConfig


# A worker is callable as (worker_id, messages, role, context) -> reply text.
WorkerPool = Callable[..., str]


class CloudWorkerPool:
    """Real cloud worker pool via litellm, dispatched by config worker id.

    Credentials/base url are resolved by litellm from env vars. An explicit
    FUGU_API_KEY / FUGU_BASE_URL is forwarded as api_key / api_base when set,
    which is useful for OpenAI-compatible gateways (Novita, OpenRouter, ...)."""

    def __init__(self, config: FuguConfig, default_max_tokens: int = 1024,
                 default_temperature: float = 0.2):
        import litellm
        self.litellm = litellm
        self.config = config
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature
        self.api_key = os.environ.get("FUGU_API_KEY")
        self.api_base = os.environ.get("FUGU_BASE_URL")
        self._latency: dict[str, list[float]] = {}

    def _worker(self, worker_id: str):
        w = self.config.workers.get(worker_id)
        if w is None:
            raise KeyError(f"unknown worker_id '{worker_id}'")
        if not w.enabled:
            raise ValueError(f"worker '{worker_id}' is disabled")
        return w

    def call(self, worker_id: str, messages: list[dict],
             role: str = "", context: dict | None = None) -> str:
        w = self._worker(worker_id)
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        kw = dict(
            model=w.provider_model, messages=msgs,
            max_tokens=w.max_tokens or self.default_max_tokens,
            temperature=w.temperature if w.temperature is not None else self.default_temperature,
        )
        if self.api_key:
            kw["api_key"] = self.api_key
        if self.api_base:
            kw["api_base"] = self.api_base
        t0 = time.time()
        try:
            r = self.litellm.completion(**kw)
        except Exception as e:
            # surface failures clearly — training must not mistake an API error
            # for a wrong answer (that would fake a 0 score on a solvable task).
            raise WorkerCallError(worker_id, w.provider_model, str(e)) from e
        dt = time.time() - t0
        self._latency.setdefault(worker_id, []).append(dt)
        return r.choices[0].message.content or ""

    # position-indexed adapter so mini.py's Coordinator (agent_id -> reply) can
    # drive a CloudWorkerPool unchanged. Returns a callable matching the
    # Coordinator's (role_name, messages, agent_id) -> reply protocol.
    def as_coordinator_worker(self, worker_ids: list[str]) -> Callable:
        def _cw(role_name, messages, agent_id):
            wid = worker_ids[agent_id % len(worker_ids)]
            return self.call(wid, messages, role=role_name)
        return _cw

    def latency(self, worker_id: str) -> list[float]:
        return list(self._latency.get(worker_id, []))


class WorkerCallError(RuntimeError):
    """Raised when a cloud worker call fails — never swallowed into a 0 score."""
    def __init__(self, worker_id, model, detail):
        super().__init__(f"worker '{worker_id}' ({model}) call failed: {detail}")
        self.worker_id, self.model, self.detail = worker_id, model, detail


class FakeCloudWorkerPool:
    """Deterministic offline worker pool for tests and dry-runs — no API key.

    Each worker maps to a domain->answer table. A worker answers from its table
    when the prompt matches a known domain keyword; otherwise it echoes a
    deterministic stub. This lets tests assert flash calls exactly one worker
    and pro runs multiple turns to verifier ACCEPT, all without any network."""
    def __init__(self, config: FuguConfig,
                 answers: dict[str, dict[str, str]] | None = None):
        self.config = config
        # answers[worker_id][domain_keyword] = canned reply
        self.answers = answers or {}

    def call(self, worker_id: str, messages: list[dict],
             role: str = "", context: dict | None = None) -> str:
        if worker_id not in self.config.workers:
            raise KeyError(f"unknown worker_id '{worker_id}'")
        content = messages[-1]["content"] if messages else ""
        table = self.answers.get(worker_id, {})
        for kw, reply in table.items():
            if kw in content:
                return reply
        # role-aware fallback so the Coordinator loop terminates
        if role == "Verifier":
            return "ACCEPT — the response is correct and complete."
        if role == "Thinker":
            return ("<suggestion>solve the problem step by step</suggestion>\n"
                    "<suggested_role>solver</suggested_role>")
        return f"[fake:{worker_id}] {content[:80]}"

    def as_coordinator_worker(self, worker_ids: list[str]) -> Callable:
        def _cw(role_name, messages, agent_id):
            wid = worker_ids[agent_id % len(worker_ids)]
            return self.call(wid, messages, role=role_name)
        return _cw
