#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Product configuration loader for the dual-model
# (openfugu-flash / openfugu-pro) prototype. Reads configs/fugu.yaml (or .json),
# validates the worker pool, and resolves which enabled workers back a model.
"""
config.py — load and validate the OpenFugu product config.

The config describes two externally-visible models backed by a shared cloud
worker pool. API keys are NEVER read from the config file — litellm resolves
them from the environment (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,
FUGU_API_KEY, FUGU_BASE_URL, ...).
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerSpec:
    id: str
    provider_model: str            # litellm model id, e.g. "openai/gpt-4o-mini"
    display_name: str = ""
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.2
    cost: float | None = None      # optional metadata, not used in core logic
    latency: float | None = None


@dataclass
class ModelSpec:
    name: str
    mode: str                      # "per_question" | "per_step"
    workers: list[str]             # worker ids
    max_turns: int = 5


@dataclass
class FuguConfig:
    models: dict[str, ModelSpec]
    workers: dict[str, WorkerSpec]
    raw: dict[str, Any]

    def worker_ids(self, model_name: str, only_enabled: bool = True) -> list[str]:
        """Resolve the worker ids backing a model, skipping disabled workers."""
        m = self.models[model_name]
        out = []
        for wid in m.workers:
            w = self.workers.get(wid)
            if w is None:
                raise KeyError(f"model {model_name} references unknown worker '{wid}'")
            if only_enabled and not w.enabled:
                continue
            out.append(wid)
        return out

    def model(self, name: str) -> ModelSpec:
        return self.models[name]

    def worker(self, wid: str) -> WorkerSpec:
        return self.workers[wid]


def _load_file(path: str) -> dict:
    if path.endswith(".json"):
        with open(path) as f:
            return json.load(f)
    # yaml — prefer omegaconf (already a dependency), fall back to PyYAML
    try:
        from omegaconf import OmegaConf
        return OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    except ImportError:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)


def load_config(path: str | None = None) -> FuguConfig:
    path = path or os.environ.get("FUGU_CONFIG") or _default_path()
    raw = _load_file(path)
    workers = {}
    for wid, w in (raw.get("workers") or {}).items():
        workers[wid] = WorkerSpec(
            id=wid,
            provider_model=w["provider_model"],
            display_name=w.get("display_name") or wid,
            enabled=bool(w.get("enabled", True)),
            tags=list(w.get("tags") or []),
            max_tokens=int(w.get("max_tokens", 1024)),
            temperature=float(w.get("temperature", 0.2)),
            cost=w.get("cost"),
            latency=w.get("latency"),
        )
    models = {}
    for name, m in (raw.get("models") or {}).items():
        models[name] = ModelSpec(
            name=name,
            mode=m["mode"],
            workers=list(m["workers"]),
            max_turns=int(m.get("max_turns", 5)),
        )
    cfg = FuguConfig(models=models, workers=workers, raw=raw)
    _validate(cfg, path)
    return cfg


def _default_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "configs", "fugu.yaml")


def _validate(cfg: FuguConfig, path: str):
    if "openfugu-flash" not in cfg.models:
        raise ValueError(f"{path}: missing model 'openfugu-flash'")
    if "openfugu-pro" not in cfg.models:
        raise ValueError(f"{path}: missing model 'openfugu-pro'")
    for name, m in cfg.models.items():
        if m.mode not in ("per_question", "per_step"):
            raise ValueError(f"{path}: model {name} mode must be per_question|per_step")
        for wid in m.workers:
            if wid not in cfg.workers:
                raise ValueError(f"{path}: model {name} references unknown worker '{wid}'")
    for name in cfg.models:
        if not cfg.worker_ids(name):
            raise ValueError(f"{path}: model {name} has no enabled workers")


if __name__ == "__main__":
    import sys
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"workers ({len(cfg.workers)}):")
    for w in cfg.workers.values():
        print(f"  {w.id:16s} {w.provider_model:32s} enabled={w.enabled} tags={w.tags}")
    for name in cfg.models:
        print(f"model {name}: mode={cfg.model(name).mode} workers={cfg.worker_ids(name)}")
