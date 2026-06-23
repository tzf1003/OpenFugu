#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Admin console task runner. Original code.
"""
console_tasks.py — run profile/train commands as tracked subprocesses.

The console does NOT reimplement the training algorithms — it shells out to
the existing scripts (train/profile_workers.py, train/train_flash_router.py,
train/train_pro_router.py) so the algorithm stays in one place. This module:

  - builds the command line from a task spec,
  - launches it in a background thread,
  - streams stdout+stderr to a per-task log file,
  - parses the final result summary (baselines / scores) from the log,
  - records the produced artifact (head .npy) into the head registry.

A task is a dict: id, type, command, status, started_at, finished_at, log path,
artifact, eval summary. The Store persists the task row; this runner holds the
live process handle in memory.
"""
from __future__ import annotations
import os, subprocess, threading, time, uuid, re
from typing import Any

try:
    from .console_store import Store
except ImportError:
    from console_store import Store


class TaskRunner:
    """Launch and monitor profile/train subprocesses."""

    def __init__(self, store: Store, root: str):
        self.store = store
        self.root = root
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ---- public API -------------------------------------------------------
    def start_profile(self, model: str, dataset: str, out: str, fake: bool,
                      config: str | None = None) -> dict:
        cfg = config or self.store.settings().config_path
        cmd = [self._py(), "train/profile_workers.py",
               "--config", cfg, "--dataset", dataset,
               "--out", out, "--model", model]
        if fake:
            cmd.append("--fake")
        return self._launch("profile", cmd, artifact=out, meta={
            "model": model, "dataset": dataset, "out": out, "fake": fake})

    def start_train_flash(self, dataset: str, profile: str, out: str,
                          model_name: str = "openfugu-flash",
                          no_backbone: bool = True, iters: int = 20,
                          config: str | None = None) -> dict:
        cfg = config or self.store.settings().config_path
        cmd = [self._py(), "train/train_flash_router.py",
               "--config", cfg, "--dataset", dataset,
               "--profile", profile, "--out", out,
               "--model-name", model_name, "--iters", str(iters)]
        if no_backbone:
            cmd.append("--no-backbone")
        return self._launch("train_flash", cmd, artifact=out, meta={
            "dataset": dataset, "profile": profile, "out": out,
            "model_name": model_name, "no_backbone": no_backbone, "iters": iters})

    def start_train_pro(self, dataset: str, out: str, fake: bool = True,
                        iters: int = 6, config: str | None = None) -> dict:
        cfg = config or self.store.settings().config_path
        cmd = [self._py(), "train/train_pro_router.py",
               "--config", cfg, "--dataset", dataset, "--out", out,
               "--iters", str(iters)]
        if fake:
            cmd.append("--fake")
        return self._launch("train_pro", cmd, artifact=out, meta={
            "dataset": dataset, "out": out, "fake": fake, "iters": iters})

    def stop(self, tid: str) -> bool:
        with self._lock:
            p = self._procs.get(tid)
            if p and p.poll() is None:
                p.terminate()
                return True
        return False

    # ---- internals --------------------------------------------------------
    def _py(self) -> str:
        return os.environ.get("FUGU_PYTHON", "python3")

    def _launch(self, ttype: str, cmd: list[str], artifact: str, meta: dict) -> dict:
        tid = f"{ttype}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        log_path = self.store.task_log_path(tid)
        task = {
            "id": tid,
            "type": ttype,
            "command": " ".join(cmd),
            "status": "pending",
            "started_at": time.time(),
            "finished_at": None,
            "log": os.path.relpath(log_path, self.root),
            "artifact": artifact,
            "meta": meta,
            "eval": {},
        }
        self.store.add_task(task)
        t = threading.Thread(target=self._run, args=(tid, cmd, log_path, artifact, ttype, meta),
                             daemon=True)
        t.start()
        return task

    def _run(self, tid: str, cmd: list[str], log_path: str, artifact: str,
             ttype: str, meta: dict):
        self.store.update_task(tid, {"status": "running"})
        try:
            with open(log_path, "w") as logf:
                p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                     cwd=self.root, text=True)
                with self._lock:
                    self._procs[tid] = p
                p.wait()
                rc = p.returncode
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"\n[console] launch error: {e}\n")
            rc = -1
        log_text = ""
        try:
            with open(log_path) as f:
                log_text = f.read()
        except Exception:
            pass
        eval_summary = _parse_eval(log_text, ttype)
        patch = {
            "status": "done" if rc == 0 else "failed",
            "finished_at": time.time(),
            "eval": eval_summary,
        }
        # register a head version for successful train tasks
        if rc == 0 and ttype in ("train_flash", "train_pro") and os.path.exists(
                os.path.join(self.root, artifact)):
            self._register_head(tid, ttype, artifact, meta, eval_summary, log_text)
        self.store.update_task(tid, patch)

    def _register_head(self, tid: str, ttype: str, artifact: str, meta: dict,
                       eval_summary: dict, log_text: str):
        head_type = "flash" if ttype == "train_flash" else "pro"
        full = os.path.join(self.root, artifact)
        shape = None
        try:
            import numpy as np
            shape = list(np.load(full).shape)
        except Exception:
            pass
        n_workers = shape[0] if (shape and head_type == "flash") else None
        # try to read training workers from config
        training_workers = []
        try:
            from config import load_config
            cfg = load_config(meta.get("config") or self.store.settings().config_path)
            mn = meta.get("model_name", f"openfugu-{head_type}")
            training_workers = cfg.worker_ids(mn)
        except Exception:
            pass
        head = {
            "id": f"{head_type}_{tid}",
            "type": head_type,
            "path": artifact,
            "shape": shape,
            "n_workers": n_workers,
            "training_workers": training_workers,
            "dataset": meta.get("dataset", ""),
            "profile": meta.get("profile", ""),
            "params": meta,
            "eval": eval_summary,
            "created_at": time.time(),
            "active": False,
            "status": "trained",
            "note": f"trained by task {tid}",
        }
        self.store.add_head(head)


def _parse_eval(log_text: str, ttype: str) -> dict:
    """Scrape baseline/score numbers from the training script stdout."""
    out: dict[str, Any] = {}
    for key, pat in [
        ("best_single", r"best single worker\s*=?\s*([0-9.]+)"),
        ("oracle", r"oracle[^0-9]*([0-9.]+)"),
        ("router_score", r"flash router\s*=\s*([0-9.]+)|pro head\s*=\s*([0-9.]+)"),
        ("base", r"base head rollout score\s*=\s*([0-9.]+)|vs base\s*([0-9.]+)"),
    ]:
        m = re.search(pat, log_text)
        if m:
            val = next((g for g in m.groups() if g), None)
            if val:
                out[key] = float(val)
    if "router_score" in out and "best_single" in out and out["best_single"] > 0:
        out["lift_pct"] = round(
            (out["router_score"] - out["best_single"]) / out["best_single"] * 100, 2)
    return out
