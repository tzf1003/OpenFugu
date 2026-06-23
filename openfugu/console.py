#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Admin console HTTP server. Original code.
"""
console.py — serve the OpenFugu admin console.

A single stdlib http.server process (no FastAPI/uvicorn — the project's serving
philosophy) that exposes:

  /api/...   JSON API for the nine console modules
  /          the static SPA frontend (console_static/)

The API talks to the config layer (config.py), the persistence store
(console_store.py), the task runner (console_tasks.py), and proxies playground
calls to the running product_serve instance. It never holds plaintext API
keys — endpoints store env var names only.

Run:
  python openfugu/console.py --port 8091
  python openfugu/console.py --port 8091 --serve-url http://localhost:8090
"""
from __future__ import annotations
import argparse, json, os, sys, time, traceback, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (load_config, save_config, FuguConfig, WorkerSpec,
                    CanonicalModel, Endpoint, ModelSpec)  # noqa: E402
from console_store import Store  # noqa: E402
from console_tasks import TaskRunner  # noqa: E402

HIDDEN = 1024
HEAD_ROWS = 10

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console_static")


class Console:
    """Holds shared state for the API handlers."""

    def __init__(self, root: str):
        self.root = root
        self.store = Store()
        self.tasks = TaskRunner(self.store, root)

    def cfg(self) -> FuguConfig:
        return load_config(self.store.settings().config_path)

    def cfg_path(self) -> str:
        return self.store.settings().config_path


CONSOLE: Console | None = None


# ---- helpers ---------------------------------------------------------------
def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode()


def _dataclass_to_dict(obj) -> dict:
    """Serialise config dataclasses, skipping observed-only health fields."""
    if isinstance(obj, WorkerSpec):
        return {"id": obj.id, "provider_model": obj.provider_model,
                "display_name": obj.display_name, "enabled": obj.enabled,
                "tags": obj.tags, "max_tokens": obj.max_tokens,
                "temperature": obj.temperature, "cost": obj.cost,
                "latency": obj.latency, "canonical_model": obj.canonical_model,
                "endpoint_policy": obj.endpoint_policy,
                "fixed_endpoint": obj.fixed_endpoint, "is_legacy": obj.is_legacy}
    if isinstance(obj, CanonicalModel):
        return {"id": obj.id, "display_name": obj.display_name,
                "family": obj.family, "vendor": obj.vendor,
                "capabilities": obj.capabilities, "status": obj.status,
                "description": obj.description}
    if isinstance(obj, Endpoint):
        return {"id": obj.id, "canonical_model": obj.canonical_model,
                "provider_model": obj.provider_model,
                "api_base_env": obj.api_base_env, "api_key_env": obj.api_key_env,
                "enabled": obj.enabled, "priority": obj.priority,
                "cost": obj.cost, "latency": obj.latency, "weight": obj.weight,
                "last_error": obj.last_error, "error_count": obj.error_count}
    if isinstance(obj, ModelSpec):
        return {"name": obj.name, "mode": obj.mode, "workers": obj.workers,
                "max_turns": obj.max_turns}
    return {}


def _config_dict(cfg: FuguConfig) -> dict:
    models = {}
    for name, m in cfg.models.items():
        models[name] = _dataclass_to_dict(m)
    return {
        "models": models,
        "workers": {wid: _dataclass_to_dict(w) for wid, w in cfg.workers.items()},
        "canonical_models": {cid: _dataclass_to_dict(c) for cid, c in cfg.canonical_models.items()},
        "endpoints": {eid: _dataclass_to_dict(e) for eid, e in cfg.endpoints.items()},
        "config_path": CONSOLE.cfg_path(),
    }


def _serve_health() -> dict:
    url = CONSOLE.store.settings().serve_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=3) as r:
            return {"online": True, "data": json.loads(r.read())}
    except Exception as e:
        return {"online": False, "error": str(e)}


def _serve_models() -> dict | None:
    url = CONSOLE.store.settings().serve_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/v1/models", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _proxy_chat(model: str, messages: list, debug: bool) -> dict:
    url = CONSOLE.store.settings().serve_url.rstrip("/") + "/v1/chat/completions"
    body = json.dumps({"model": model, "messages": messages, "debug": debug}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
            data["_latency_ms"] = round((time.time() - t0) * 1000)
            return data
    except urllib.error.HTTPError as e:
        return {"error": f"serve returned {e.code}: {e.read().decode()[:200]}",
                "_latency_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": str(e), "_latency_ms": round((time.time() - t0) * 1000)}


def _list_dir(rel_glob: str) -> list[dict]:
    """List files matching a project-relative glob with size + mtime."""
    import glob
    base = os.path.join(CONSOLE.root, rel_glob)
    out = []
    for p in sorted(glob.glob(base)):
        if os.path.isfile(p):
            out.append({"path": os.path.relpath(p, CONSOLE.root),
                        "size": os.path.getsize(p),
                        "mtime": os.path.getmtime(p)})
    return out


def _profile_summary(rel: str) -> dict:
    """Aggregate a worker_profile.jsonl into per-worker stats."""
    full = CONSOLE.store.abs(rel)
    if not os.path.exists(full):
        return {"error": "not found"}
    by: dict[str, dict] = {}
    n = 0
    try:
        with open(full) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                n += 1
                w = r.get("worker_id", "?")
                d = by.setdefault(w, {"n": 0, "scores": [], "lat": [], "errors": 0})
                d["n"] += 1
                d["scores"].append(r.get("score", 0.0))
                if r.get("latency") is not None:
                    d["lat"].append(r["latency"])
                if r.get("error"):
                    d["errors"] += 1
    except Exception as e:
        return {"error": str(e)}
    workers = {}
    for w, d in by.items():
        sc = d["scores"]
        lt = d["lat"]
        workers[w] = {"n": d["n"], "mean_score": round(sum(sc) / len(sc), 3) if sc else 0,
                      "mean_latency": round(sum(lt) / len(lt), 3) if lt else None,
                      "errors": d["errors"]}
    scores_all = [s for d in by.values() for s in d["scores"]]
    return {"records": n, "workers": workers,
            "overall_mean": round(sum(scores_all) / len(scores_all), 3) if scores_all else 0}


def _head_match_info(cfg: FuguConfig, model_name: str) -> dict:
    """Compare the current worker pool against the active head's training pool."""
    head_type = "flash" if "flash" in model_name else "pro"
    head = CONSOLE.store.active_head(head_type)
    current = cfg.worker_ids(model_name)
    if not head:
        return {"model": model_name, "current_workers": current,
                "head": None, "match": None, "issues": ["no active head"]}
    tw = head.get("training_workers") or []
    issues = []
    if head_type == "flash":
        if head.get("n_workers") and head["n_workers"] != len(current):
            issues.append(f"head expects {head['n_workers']} workers, pool has {len(current)}")
        if tw and tw != current:
            missing = [w for w in tw if w not in current]
            extra = [w for w in current if w not in tw]
            if missing:
                issues.append(f"head trained without current workers: {missing}")
            if extra:
                issues.append(f"pool has workers not in head: {extra}")
            if list(tw) != list(current):
                issues.append("worker order differs from training order")
    else:
        # pro head is flat 1D (HEAD_ROWS*HIDDEN,) per product_serve.py
        if head.get("shape") and head["shape"] not in ([HEAD_ROWS * HIDDEN], [HEAD_ROWS, HIDDEN]):
            issues.append(f"pro head shape {head['shape']} != expected [{HEAD_ROWS * HIDDEN}] or [{HEAD_ROWS}, {HIDDEN}]")
    return {"model": model_name, "current_workers": current, "head": head,
            "match": len(issues) == 0, "issues": issues}


def _validate_config(cfg: FuguConfig) -> dict:
    """Run the design-doc validation rules (section 9)."""
    errors, warnings = [], []
    for name, m in cfg.models.items():
        enabled = [w for w in m.workers if w in cfg.workers and cfg.workers[w].enabled]
        if not enabled:
            errors.append(f"model {name} has no enabled workers")
    for wid, w in cfg.workers.items():
        if w.enabled and w.canonical_model and w.canonical_model not in cfg.canonical_models:
            errors.append(f"worker {wid} references unknown canonical_model '{w.canonical_model}'")
    for cid, c in cfg.canonical_models.items():
        if c.status == "active":
            eps = cfg.endpoints_for(cid)
            if not eps:
                errors.append(f"active canonical model {cid} has no enabled endpoint")
    for eid, e in cfg.endpoints.items():
        if e.api_key_env and e.api_key_env.islower():
            warnings.append(f"endpoint {eid} api_key_env '{e.api_key_env}' looks like a value, not an env var name")
    # head shape checks
    for ht in ("flash", "pro"):
        head = CONSOLE.store.active_head(ht)
        if head and head.get("path"):
            full = CONSOLE.store.abs(head["path"])
            if not os.path.exists(full):
                errors.append(f"active {ht} head file missing: {head['path']}")
            else:
                try:
                    import numpy as np
                    shape = np.load(full).shape
                    if ht == "flash":
                        n = len(cfg.worker_ids(f"openfugu-flash"))
                        if shape not in ((n * HIDDEN,), (n, HIDDEN)):
                            errors.append(f"flash head shape {shape} != ({n * HIDDEN},) or ({n}, {HIDDEN})")
                    else:
                        if shape not in ((HEAD_ROWS * HIDDEN,), (HEAD_ROWS, HIDDEN)):
                            errors.append(f"pro head shape {shape} != ({HEAD_ROWS * HIDDEN},) or ({HEAD_ROWS}, {HIDDEN})")
                except Exception as ex:
                    warnings.append(f"cannot read {ht} head: {ex}")
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _read_requests(limit=200) -> list[dict]:
    path = os.path.join(CONSOLE.root, "data", "request_log.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path) as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return out


# ---- API dispatch ----------------------------------------------------------
def handle_api(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """Return (status_code, json_body). 404 -> (404, {...})."""
    c = CONSOLE
    # ---- overview ----
    if path == "/api/overview" and method == "GET":
        cfg = c.cfg()
        health = _serve_health()
        models_served = _serve_models()
        recent = _read_requests(20)
        return 200, {
            "health": health, "serve_url": c.store.settings().serve_url,
            "served_models": models_served,
            "config_path": c.cfg_path(),
            "models": {n: {"mode": m.mode, "workers": cfg.worker_ids(n)}
                       for n, m in cfg.models.items()},
            "flash_head": c.store.active_head("flash"),
            "pro_head": c.store.active_head("pro"),
            "flash_match": _head_match_info(cfg, "openfugu-flash"),
            "pro_match": _head_match_info(cfg, "openfugu-pro"),
            "canonical_count": len(cfg.canonical_models),
            "endpoint_count": len(cfg.endpoints),
            "worker_count": len(cfg.workers),
            "recent_requests": recent,
            "debug": c.store.settings().debug,
        }

    # ---- canonical models ----
    if path == "/api/canonical-models" and method == "GET":
        cfg = c.cfg()
        items = []
        for cid, cm in cfg.canonical_models.items():
            eps = cfg.endpoints_for(cid)
            workers = [wid for wid, w in cfg.workers.items()
                       if w.canonical_model == cid and w.enabled]
            in_flash = cid in [cfg.canonical_model_of(w) for w in cfg.worker_ids("openfugu-flash", False)]
            in_pro = cid in [cfg.canonical_model_of(w) for w in cfg.worker_ids("openfugu-pro", False)]
            items.append({**_dataclass_to_dict(cm), "endpoint_count": len(eps),
                          "worker_count": len(workers), "in_flash": in_flash, "in_pro": in_pro})
        return 200, {"items": items}

    if path == "/api/canonical-models" and method == "POST":
        cfg = c.cfg()
        cid = body.get("id", "").strip()
        if not cid or cid in cfg.canonical_models:
            return 400, {"error": "id required and must be unique"}
        cfg.canonical_models[cid] = CanonicalModel(
            id=cid, display_name=body.get("display_name") or cid,
            family=body.get("family", ""), vendor=body.get("vendor", ""),
            capabilities=body.get("capabilities", []),
            status=body.get("status", "draft"),
            description=body.get("description", ""))
        save_config(cfg, c.cfg_path())
        return 201, _dataclass_to_dict(cfg.canonical_models[cid])

    if path.startswith("/api/canonical-models/") and method == "PUT":
        cfg = c.cfg()
        cid = path.split("/")[-1]
        if cid not in cfg.canonical_models:
            return 404, {"error": "not found"}
        cm = cfg.canonical_models[cid]
        for k in ("display_name", "family", "vendor", "description", "status"):
            if k in body:
                setattr(cm, k, body[k])
        if "capabilities" in body:
            cm.capabilities = body["capabilities"]
        save_config(cfg, c.cfg_path())
        return 200, _dataclass_to_dict(cm)

    if path.startswith("/api/canonical-models/") and method == "DELETE":
        cfg = c.cfg()
        cid = path.split("/")[-1]
        if cid not in cfg.canonical_models:
            return 404, {"error": "not found"}
        # block if workers reference it
        refs = [wid for wid, w in cfg.workers.items() if w.canonical_model == cid]
        if refs:
            return 409, {"error": f"workers still reference this model: {refs}"}
        del cfg.canonical_models[cid]
        for eid in [e for e, ep in cfg.endpoints.items() if ep.canonical_model == cid]:
            del cfg.endpoints[eid]
        save_config(cfg, c.cfg_path())
        return 200, {"deleted": cid}

    if path.endswith("/add-to-pool") and method == "POST":
        cfg = c.cfg()
        cid = path.split("/")[3]
        pool = body.get("pool", "flash")
        model_name = f"openfugu-{pool}"
        if cid not in cfg.canonical_models:
            return 404, {"error": "canonical model not found"}
        # find or create a worker for this canonical model
        wid = next((wid for wid, w in cfg.workers.items()
                    if w.canonical_model == cid), None)
        if not wid:
            wid = cid
            eps = cfg.endpoints_for(cid)
            pm = eps[0].provider_model if eps else ""
            cfg.workers[wid] = WorkerSpec(
                id=wid, provider_model=pm, display_name=cfg.canonical_models[cid].display_name,
                canonical_model=cid, endpoint_policy="priority")
        elif not cfg.workers[wid].enabled:
            cfg.workers[wid].enabled = True
        if wid not in cfg.models[model_name].workers:
            cfg.models[model_name].workers.append(wid)
        save_config(cfg, c.cfg_path())
        return 200, {"worker": wid, "pool": model_name}

    if path.endswith("/remove-from-pool") and method == "POST":
        cfg = c.cfg()
        cid = path.split("/")[3]
        pool = body.get("pool", "flash")
        model_name = f"openfugu-{pool}"
        wid = next((wid for wid, w in cfg.workers.items()
                    if w.canonical_model == cid), None)
        if wid and wid in cfg.models[model_name].workers:
            cfg.models[model_name].workers.remove(wid)
            save_config(cfg, c.cfg_path())
        return 200, {"removed": wid}

    # ---- endpoints ----
    if path == "/api/endpoints" and method == "GET":
        cfg = c.cfg()
        items = []
        for eid, e in cfg.endpoints.items():
            d = _dataclass_to_dict(e)
            d["canonical_display"] = cfg.canonical_models.get(
                e.canonical_model, CanonicalModel(id=e.canonical_model,
                                                  display_name=e.canonical_model)).display_name
            items.append(d)
        return 200, {"items": items}

    if path == "/api/endpoints" and method == "POST":
        cfg = c.cfg()
        eid = body.get("id", "").strip()
        cm = body.get("canonical_model", "").strip()
        pm = body.get("provider_model", "").strip()
        if not eid or not cm or not pm:
            return 400, {"error": "id, canonical_model, provider_model required"}
        if eid in cfg.endpoints:
            return 400, {"error": "endpoint id must be unique"}
        cfg.endpoints[eid] = Endpoint(
            id=eid, canonical_model=cm, provider_model=pm,
            api_base_env=body.get("api_base_env", ""), api_key_env=body.get("api_key_env", ""),
            enabled=body.get("enabled", True), priority=int(body.get("priority", 10)),
            cost=float(body.get("cost", 0.0)), weight=float(body.get("weight", 1.0)))
        save_config(cfg, c.cfg_path())
        return 201, _dataclass_to_dict(cfg.endpoints[eid])

    if path.startswith("/api/endpoints/") and method == "PUT":
        cfg = c.cfg()
        eid = path.split("/")[-1]
        if eid not in cfg.endpoints:
            return 404, {"error": "not found"}
        e = cfg.endpoints[eid]
        for k in ("provider_model", "api_base_env", "api_key_env", "canonical_model"):
            if k in body:
                setattr(e, k, body[k])
        if "enabled" in body:
            e.enabled = bool(body["enabled"])
        if "priority" in body:
            e.priority = int(body["priority"])
        if "cost" in body:
            e.cost = float(body["cost"])
        if "weight" in body:
            e.weight = float(body["weight"])
        save_config(cfg, c.cfg_path())
        return 200, _dataclass_to_dict(e)

    if path.startswith("/api/endpoints/") and method == "DELETE":
        cfg = c.cfg()
        eid = path.split("/")[-1]
        if eid in cfg.endpoints:
            del cfg.endpoints[eid]
            save_config(cfg, c.cfg_path())
            return 200, {"deleted": eid}
        return 404, {"error": "not found"}

    if path == "/api/endpoints/smoke-test" and method == "POST":
        return _smoke_test(body)

    if path.startswith("/api/endpoints/") and path.endswith("/set-current") and method == "POST":
        cfg = c.cfg()
        eid = path.split("/")[3]
        if eid not in cfg.endpoints:
            return 404, {"error": "endpoint not found"}
        ep = cfg.endpoints[eid]
        cm = ep.canonical_model
        # Enable target, bump its priority to be the lowest number (highest
        # priority) among endpoints sharing the same canonical model.
        siblings = [e for e in cfg.endpoints.values() if e.canonical_model == cm]
        min_pri = min((e.priority for e in siblings), default=10)
        ep.enabled = True
        ep.priority = min_pri - 1 if min_pri > 1 else 0
        save_config(cfg, c.cfg_path())
        return 200, {"current": eid, "canonical_model": cm, "priority": ep.priority}

    # ---- workers ----
    if path == "/api/workers" and method == "GET":
        cfg = c.cfg()
        model = query.get("model")
        ids = cfg.worker_ids(model) if model else list(cfg.workers.keys())
        items = []
        for wid in ids:
            w = cfg.workers[wid]
            d = _dataclass_to_dict(w)
            pe = cfg.primary_endpoint(w)
            d["primary_endpoint"] = pe.id if pe else None
            d["primary_provider_model"] = pe.provider_model if pe else w.provider_model
            d["canonical_display"] = cfg.canonical_models.get(
                w.canonical_model, CanonicalModel(id=w.canonical_model,
                                                  display_name=w.canonical_model)).display_name
            items.append(d)
        return 200, {"items": items, "order": ids}

    if path == "/api/workers/order" and method == "PUT":
        cfg = c.cfg()
        model_name = body.get("model")
        order = body.get("order", [])
        if model_name not in cfg.models:
            return 400, {"error": "unknown model"}
        cfg.models[model_name].workers = list(order)
        save_config(cfg, c.cfg_path())
        return 200, {"model": model_name, "order": cfg.models[model_name].workers}

    if path == "/api/workers/head-compare" and method == "GET":
        cfg = c.cfg()
        model = query.get("model", "openfugu-flash")
        return 200, _head_match_info(cfg, model)

    if path.startswith("/api/workers/") and method == "PUT":
        cfg = c.cfg()
        wid = path.split("/")[-1]
        if wid not in cfg.workers:
            return 404, {"error": "not found"}
        w = cfg.workers[wid]
        for k in ("display_name", "endpoint_policy", "fixed_endpoint", "canonical_model"):
            if k in body:
                setattr(w, k, body[k])
        if "enabled" in body:
            w.enabled = bool(body["enabled"])
        if "max_tokens" in body:
            w.max_tokens = int(body["max_tokens"])
        if "temperature" in body:
            w.temperature = float(body["temperature"])
        if "tags" in body:
            w.tags = body["tags"]
        # re-derive provider_model from endpoint if policy changed
        if w.canonical_model and ("endpoint_policy" in body or "fixed_endpoint" in body):
            pe = cfg.primary_endpoint(w)
            if pe:
                w.provider_model = pe.provider_model
        save_config(cfg, c.cfg_path())
        return 200, _dataclass_to_dict(w)

    # ---- datasets & profiles ----
    if path == "/api/datasets" and method == "GET":
        return 200, {"items": _list_dir("data/router_train*.jsonl")}

    if path == "/api/profiles" and method == "GET":
        return 200, {"items": _list_dir("data/worker_profile*.jsonl")}

    if path.startswith("/api/profiles/") and path.endswith("/summary") and method == "GET":
        rel = path[len("/api/profiles/"):-len("/summary")]
        # the path may contain slashes -> rejoin
        rel = path.split("/", 3)[-1].rsplit("/summary", 1)[0]
        return 200, _profile_summary(rel)

    # ---- tasks ----
    if path == "/api/tasks" and method == "GET":
        return 200, {"items": c.store.tasks()}

    if path.startswith("/api/tasks/") and method == "GET":
        tid = path.split("/")[-1]
        t = c.store.task(tid)
        if not t:
            return 404, {"error": "not found"}
        log_text = ""
        try:
            with open(c.store.task_log_path(tid)) as f:
                log_text = f.read()
        except Exception:
            pass
        return 200, {**t, "log": log_text}

    if path.startswith("/api/tasks/") and path.endswith("/stop") and method == "POST":
        tid = path.split("/")[3]
        ok = c.tasks.stop(tid)
        return 200, {"stopped": ok}

    if path == "/api/tasks/profile" and method == "POST":
        t = c.tasks.start_profile(
            model=body.get("model", "openfugu-flash"),
            dataset=body.get("dataset", "data/router_train.jsonl"),
            out=body.get("out", f"data/worker_profile_{int(time.time())}.jsonl"),
            fake=body.get("fake", True))
        return 201, t

    if path == "/api/tasks/train-flash" and method == "POST":
        t = c.tasks.start_train_flash(
            dataset=body.get("dataset", "data/router_train.jsonl"),
            profile=body.get("profile", "data/worker_profile_flash.jsonl"),
            out=body.get("out", f"data/flash_head_{int(time.time())}.npy"),
            no_backbone=body.get("no_backbone", True),
            iters=int(body.get("iters", 20)))
        return 201, t

    if path == "/api/tasks/train-pro" and method == "POST":
        t = c.tasks.start_train_pro(
            dataset=body.get("dataset", "data/router_train.jsonl"),
            out=body.get("out", f"data/pro_head_{int(time.time())}.npy"),
            fake=body.get("fake", True),
            iters=int(body.get("iters", 6)))
        return 201, t

    # ---- heads ----
    if path == "/api/heads" and method == "GET":
        return 200, {"items": c.store.heads(),
                     "active_flash": c.store.settings().flash_head,
                     "active_pro": c.store.settings().pro_head}

    if path == "/api/heads" and method == "POST":
        head = body
        head.setdefault("id", f"{head.get('type','head')}_{int(time.time())}")
        head.setdefault("created_at", time.time())
        head.setdefault("active", False)
        head.setdefault("status", "registered")
        c.store.add_head(head)
        return 201, head

    if path.startswith("/api/heads/") and path.endswith("/activate") and method == "POST":
        hid = path.split("/")[3]
        ht = body.get("type", "flash")
        h = c.store.set_active_head(ht, hid)
        if not h:
            return 404, {"error": "head not found"}
        return 200, h

    if path.startswith("/api/heads/") and path.endswith("/deactivate") and method == "POST":
        hid = path.split("/")[3]
        h = c.store.head(hid)
        if not h:
            return 404, {"error": "not found"}
        h["active"] = False
        c.store.update_head(hid, {"active": False, "status": "deprecated"})
        s = c.store.settings()
        if s.flash_head == hid:
            c.store.update_settings({"flash_head": ""})
        if s.pro_head == hid:
            c.store.update_settings({"pro_head": ""})
        return 200, h

    if path.startswith("/api/heads/") and method == "DELETE":
        hid = path.split("/")[-1]
        ok = c.store.delete_head(hid)
        return 200 if ok else 404, {"deleted": ok}

    # ---- requests ----
    if path == "/api/requests" and method == "GET":
        reqs = _read_requests(500)
        # filters
        for key in ("model", "worker", "status"):
            v = query.get(key)
            if v:
                reqs = [r for r in reqs if str(r.get(key, "")).lower() == v.lower()]
        return 200, {"items": list(reversed(reqs))[-200:]}

    # ---- playground ----
    if path == "/api/playground/chat" and method == "POST":
        model = body.get("model", "openfugu-flash")
        messages = body.get("messages", [])
        debug = body.get("debug", False)
        return 200, _proxy_chat(model, messages, debug)

    # ---- config ----
    if path == "/api/config" and method == "GET":
        return 200, _config_dict(c.cfg())

    if path == "/api/config/save" and method == "POST":
        cfg = c.cfg()
        save_config(cfg, c.cfg_path())
        return 200, {"saved": c.cfg_path()}

    if path == "/api/config/reload" and method == "POST":
        return 200, {"reloaded": c.cfg_path()}

    if path == "/api/config/validate" and method == "GET":
        return 200, _validate_config(c.cfg())

    if path == "/api/config/snapshot" and method == "GET":
        cfg = c.cfg()
        import io
        try:
            from omegaconf import OmegaConf
            buf = io.StringIO()
            OmegaConf.save(OmegaConf.create(cfg.raw), buf)
            return 200, {"yaml": buf.getvalue(), "path": c.cfg_path()}
        except Exception:
            return 200, {"raw": cfg.raw, "path": c.cfg_path()}

    # ---- settings ----
    if path == "/api/settings" and method == "GET":
        return 200, c.store.settings().to_dict()

    if path == "/api/settings" and method == "PUT":
        return 200, c.store.update_settings(body).to_dict()

    return 404, {"error": f"unknown endpoint {method} {path}"}


def _smoke_test(body: dict) -> tuple[int, dict]:
    """Send a tiny ping through a specific endpoint's provider_model."""
    import os as _os
    cfg = CONSOLE.cfg()
    eid = body.get("endpoint_id", "")
    ep = cfg.endpoints.get(eid)
    if not ep:
        return 404, {"error": "endpoint not found"}
    api_key = _os.environ.get(ep.api_key_env, "") if ep.api_key_env else _os.environ.get("OPENAI_API_KEY", "")
    api_base = _os.environ.get(ep.api_base_env, "") if ep.api_base_env else _os.environ.get("OPENAI_API_BASE", "")
    payload = json.dumps({"model": ep.provider_model,
                          "messages": [{"role": "user", "content": "ping"}],
                          "max_tokens": 5}).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = (api_base.rstrip("/") + "/v1/chat/completions") if api_base else "https://api.openai.com/v1/chat/completions"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            return 200, {"ok": True, "latency_ms": round((time.time() - t0) * 1000),
                         "reply": (data.get("choices", [{}])[0].get("message", {}).get("content", ""))[:100]}
    except urllib.error.HTTPError as e:
        return 200, {"ok": False, "latency_ms": round((time.time() - t0) * 1000),
                     "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return 200, {"ok": False, "latency_ms": round((time.time() - t0) * 1000), "error": str(e)}


# ---- HTTP handler ----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            data = _json(body)
        elif isinstance(body, bytes):
            data = body
            if content_type == "application/json":
                content_type = "application/octet-stream"
        else:
            data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path.startswith("/api/"):
            code, body = handle_api("GET", path, query, {})
            self._send(code, body)
            return
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if path.startswith("/api/"):
            code, result = handle_api("POST", path, {}, body)
            self._send(code, result)
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if path.startswith("/api/"):
            code, result = handle_api("PUT", path, {}, body)
            self._send(code, result)
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            code, result = handle_api("DELETE", path, {}, {})
            self._send(code, result)
        else:
            self._send(404, {"error": "not found"})

    def _serve_static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        # prevent path traversal
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            # SPA fallback: serve index.html for unknown non-asset routes
            full = os.path.join(STATIC_DIR, "index.html")
        ctype = {
            ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json",
            ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        try:
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)
        except Exception:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def main(argv=None):
    global CONSOLE
    ap = argparse.ArgumentParser(description="OpenFugu admin console.")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--serve-url", default=None, help="product_serve URL (env FUGU_SERVE_URL)")
    ap.add_argument("--config", default=None, help="config path (env FUGU_CONFIG)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.normpath(os.path.join(here, ".."))
    CONSOLE = Console(root)
    s = CONSOLE.store.settings()
    if args.serve_url:
        CONSOLE.store.update_settings({"serve_url": args.serve_url})
    if args.config:
        CONSOLE.store.update_settings({"config_path": args.config})
    if args.debug:
        CONSOLE.store.update_settings({"debug": True})
    s = CONSOLE.store.settings()

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[console] OpenFugu admin console on http://localhost:{args.port}", flush=True)
    print(f"[console] config: {s.config_path}", flush=True)
    print(f"[console] serve:  {s.serve_url}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
