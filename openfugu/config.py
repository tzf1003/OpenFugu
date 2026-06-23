#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Product configuration loader for the dual-model
# (openfugu-flash / openfugu-pro) prototype. Reads configs/fugu.yaml (or .json),
# validates the worker pool, and resolves which enabled workers back a model.
"""
config.py — load, validate and persist the OpenFugu product config.

The config describes two externally-visible models backed by a shared cloud
worker pool. API keys are NEVER read from the config file — litellm resolves
them from the environment (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,
FUGU_API_KEY, FUGU_BASE_URL, ...).

New structure (managed by the console) separates three concerns:

  canonical_models : "capability-equivalent model" abstraction (e.g. gpt_5_5).
  endpoints        : concrete API channels for a canonical model (official,
                     gateway, aggregator, ...). One canonical model may have
                     several endpoints; only env var *names* are stored, never
                     plaintext keys.
  workers          : bind to a canonical model and pick an endpoint via
                     `endpoint_policy` (fixed|cheapest_healthy|fastest_healthy|
                     priority|weighted).

Legacy configs where a worker only carries `provider_model` are read in a
compat mode: a canonical_model + default endpoint are synthesised in memory so
the console can present every worker uniformly. Saving migrates legacy workers
to the canonical structure.
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerSpec:
    id: str
    provider_model: str = ""       # litellm model id (legacy, or derived from endpoint)
    display_name: str = ""
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.2
    cost: float | None = None      # optional metadata, not used in core logic
    latency: float | None = None
    # new-structure fields (empty for legacy workers)
    canonical_model: str = ""
    endpoint_policy: str = ""      # fixed|cheapest_healthy|fastest_healthy|priority|weighted
    fixed_endpoint: str = ""       # endpoint id when policy == "fixed"
    is_legacy: bool = False        # True when the worker only had provider_model


@dataclass
class CanonicalModel:
    id: str
    display_name: str = ""
    family: str = ""
    vendor: str = ""
    capabilities: list[str] = field(default_factory=list)
    status: str = "active"         # active | inactive | draft
    description: str = ""


@dataclass
class Endpoint:
    id: str
    canonical_model: str
    provider_model: str
    api_base_env: str = ""
    api_key_env: str = ""
    enabled: bool = True
    priority: int = 10
    cost: float = 0.0
    latency: float | None = None
    weight: float = 1.0
    # observed health (not persisted in config)
    last_success: float | None = None
    last_error: str | None = None
    error_count: int = 0


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
    canonical_models: dict[str, CanonicalModel] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

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

    def endpoints_for(self, canonical_model: str, only_enabled: bool = True) -> list[Endpoint]:
        eps = [e for e in self.endpoints.values() if e.canonical_model == canonical_model]
        if only_enabled:
            eps = [e for e in eps if e.enabled]
        eps.sort(key=lambda e: e.priority)
        return eps

    def primary_endpoint(self, worker: WorkerSpec) -> Endpoint | None:
        """Resolve the endpoint a worker would use right now (best-effort)."""
        if not worker.canonical_model:
            return None
        eps = self.endpoints_for(worker.canonical_model)
        if not eps:
            return None
        if worker.endpoint_policy == "fixed" and worker.fixed_endpoint:
            for e in eps:
                if e.id == worker.fixed_endpoint:
                    return e
        return eps[0]

    def canonical_model_of(self, worker_id: str) -> str:
        w = self.workers.get(worker_id)
        return w.canonical_model if w and w.canonical_model else worker_id


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

    canonical_models = {}
    for cid, c in (raw.get("canonical_models") or {}).items():
        canonical_models[cid] = CanonicalModel(
            id=cid,
            display_name=c.get("display_name") or cid,
            family=c.get("family", ""),
            vendor=c.get("vendor", ""),
            capabilities=list(c.get("capabilities") or []),
            status=c.get("status", "active"),
            description=c.get("description", ""),
        )

    endpoints = {}
    for eid, e in (raw.get("endpoints") or {}).items():
        endpoints[eid] = Endpoint(
            id=eid,
            canonical_model=e["canonical_model"],
            provider_model=e["provider_model"],
            api_base_env=e.get("api_base_env", ""),
            api_key_env=e.get("api_key_env", ""),
            enabled=bool(e.get("enabled", True)),
            priority=int(e.get("priority", 10)),
            cost=float(e.get("cost", 0.0)),
            latency=e.get("latency"),
            weight=float(e.get("weight", 1.0)),
        )

    workers = {}
    for wid, w in (raw.get("workers") or {}).items():
        cm = w.get("canonical_model", "")
        policy = w.get("endpoint_policy", "")
        fixed_ep = w.get("fixed_endpoint", "")
        is_legacy = "provider_model" in w and not cm
        # derive provider_model: explicit > endpoint resolution > empty
        if "provider_model" in w:
            pm = w["provider_model"]
        elif cm:
            pm = _resolve_provider_model(endpoints, cm, fixed_ep)
        else:
            pm = ""
        workers[wid] = WorkerSpec(
            id=wid,
            provider_model=pm,
            display_name=w.get("display_name") or wid,
            enabled=bool(w.get("enabled", True)),
            tags=list(w.get("tags") or []),
            max_tokens=int(w.get("max_tokens", 1024)),
            temperature=float(w.get("temperature", 0.2)),
            cost=w.get("cost"),
            latency=w.get("latency"),
            canonical_model=cm,
            endpoint_policy=policy,
            fixed_endpoint=fixed_ep,
            is_legacy=is_legacy,
        )
        # synthesise a canonical_model + default endpoint for legacy workers so
        # the console can present every worker uniformly without rewriting the
        # config file until the user explicitly saves.
        if is_legacy and cm == "":
            if wid not in canonical_models:
                canonical_models[wid] = CanonicalModel(
                    id=wid,
                    display_name=w.get("display_name") or wid,
                    family=_guess_family(w.get("provider_model", "")),
                    status="active",
                )
            ep_id = f"{wid}_default"
            if ep_id not in endpoints:
                endpoints[ep_id] = Endpoint(
                    id=ep_id, canonical_model=wid,
                    provider_model=w.get("provider_model", ""),
                    api_base_env="OPENAI_API_BASE",
                    api_key_env="OPENAI_API_KEY",
                    enabled=True, priority=10,
                )
            workers[wid].canonical_model = wid
            workers[wid].endpoint_policy = workers[wid].endpoint_policy or "priority"
            workers[wid].fixed_endpoint = workers[wid].fixed_endpoint or ""

    models = {}
    for name, m in (raw.get("models") or {}).items():
        models[name] = ModelSpec(
            name=name,
            mode=m["mode"],
            workers=list(m["workers"]),
            max_turns=int(m.get("max_turns", 5)),
        )
    cfg = FuguConfig(models=models, workers=workers, canonical_models=canonical_models,
                     endpoints=endpoints, raw=raw)
    _validate(cfg, path)
    return cfg


def _resolve_provider_model(endpoints: dict, canonical_model: str, fixed_ep: str) -> str:
    eps = sorted(
        [e for e in endpoints.values()
         if e.canonical_model == canonical_model and e.enabled],
        key=lambda e: e.priority)
    if fixed_ep:
        for e in eps:
            if e.id == fixed_ep:
                return e.provider_model
    return eps[0].provider_model if eps else ""


def _guess_family(provider_model: str) -> str:
    pm = provider_model.lower()
    if "gpt" in pm or "openai" in pm:
        return "openai"
    if "claude" in pm or "anthropic" in pm:
        return "anthropic"
    if "gemini" in pm:
        return "google"
    if "deepseek" in pm:
        return "deepseek"
    if "kimi" in pm:
        return "moonshot"
    return "other"


def save_config(cfg: FuguConfig, path: str):
    """Persist a FuguConfig back to YAML in the new structure.

    Legacy workers are migrated to canonical_model + endpoint form on save so
    the file converges on the canonical structure the console manages."""
    out: dict[str, Any] = {}
    # preserve top-level keys we don't manage verbatim from raw
    for k, v in (cfg.raw or {}).items():
        if k in ("models", "workers", "canonical_models", "endpoints"):
            continue
        out[k] = v
    out["models"] = {}
    for name, m in cfg.models.items():
        mm: dict[str, Any] = {"mode": m.mode, "workers": list(m.workers)}
        if m.max_turns != 5:
            mm["max_turns"] = m.max_turns
        out["models"][name] = mm
    out["canonical_models"] = {}
    for cid, c in cfg.canonical_models.items():
        cc: dict[str, Any] = {"display_name": c.display_name}
        if c.family:
            cc["family"] = c.family
        if c.vendor:
            cc["vendor"] = c.vendor
        if c.capabilities:
            cc["capabilities"] = list(c.capabilities)
        if c.status != "active":
            cc["status"] = c.status
        if c.description:
            cc["description"] = c.description
        out["canonical_models"][cid] = cc
    out["endpoints"] = {}
    for eid, e in cfg.endpoints.items():
        ee: dict[str, Any] = {
            "canonical_model": e.canonical_model,
            "provider_model": e.provider_model,
            "enabled": e.enabled,
            "priority": e.priority,
        }
        if e.api_base_env:
            ee["api_base_env"] = e.api_base_env
        if e.api_key_env:
            ee["api_key_env"] = e.api_key_env
        if e.cost:
            ee["cost"] = e.cost
        if e.latency is not None:
            ee["latency"] = e.latency
        if e.weight != 1.0:
            ee["weight"] = e.weight
        out["endpoints"][eid] = ee
    out["workers"] = {}
    for wid, w in cfg.workers.items():
        ww: dict[str, Any] = {"canonical_model": w.canonical_model or wid}
        if w.endpoint_policy:
            ww["endpoint_policy"] = w.endpoint_policy
        if w.fixed_endpoint:
            ww["fixed_endpoint"] = w.fixed_endpoint
        ww["display_name"] = w.display_name
        ww["enabled"] = w.enabled
        if w.tags:
            ww["tags"] = list(w.tags)
        ww["max_tokens"] = w.max_tokens
        ww["temperature"] = w.temperature
        # keep provider_model for runtime compat even in new structure
        ww["provider_model"] = w.provider_model
        out["workers"][wid] = ww
    _dump_yaml(out, path)


def _dump_yaml(data: dict, path: str):
    try:
        from omegaconf import OmegaConf
        OmegaConf.save(OmegaConf.create(data), path)
    except Exception:
        import yaml
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


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
