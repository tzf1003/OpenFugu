#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Admin console persistence layer. Original code.
"""
console_store.py — JSON-backed store for console state.

Holds three concerns the console needs to persist across restarts:
  - settings : serve URL, config path, active head pointers, debug flag.
  - heads    : head version registry (metadata + eval results + active flag).
  - tasks    : profile/train task history (status + log path).

All state lives under data/console/ so it sits next to the datasets and heads
the console already manages. The store is process-local and file-locked with a
simple threading lock — the console is a single-process tool.
"""
from __future__ import annotations
import json, os, threading, time
from dataclasses import dataclass, asdict
from typing import Any


def _console_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(here, "..", "data", "console")
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "tasks"), exist_ok=True)
    return d


def _project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, ".."))


@dataclass
class Settings:
    serve_url: str = "http://localhost:8090"
    config_path: str = ""
    flash_head: str = ""        # head registry id of the active flash head
    pro_head: str = ""          # head registry id of the active pro head
    debug: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(**{k: d.get(k, getattr(cls, k)) for k in
                      ("serve_url", "config_path", "flash_head", "pro_head", "debug")})


class Store:
    """File-backed store for settings, head registry and task history."""

    def __init__(self):
        self.dir = _console_dir()
        self.root = _project_root()
        self.settings_path = os.path.join(self.dir, "settings.json")
        self.heads_path = os.path.join(self.dir, "head_registry.json")
        self.tasks_path = os.path.join(self.dir, "tasks.json")
        self._lock = threading.Lock()
        self._settings = self._load_settings()
        if not self._settings.config_path:
            self._settings.config_path = os.path.join(self.root, "configs", "fugu.yaml")
        self._heads = self._load_json(self.heads_path, default=[])
        self._tasks = self._load_json(self.tasks_path, default=[])
        self._seed_heads()

    # ---- settings ---------------------------------------------------------
    def _load_settings(self) -> Settings:
        if os.path.exists(self.settings_path):
            with open(self.settings_path) as f:
                return Settings.from_dict(json.load(f))
        return Settings()

    def settings(self) -> Settings:
        return self._settings

    def update_settings(self, patch: dict) -> Settings:
        with self._lock:
            for k, v in patch.items():
                if hasattr(self._settings, k):
                    setattr(self._settings, k, v)
            self._save_json(self.settings_path, self._settings.to_dict())
        return self._settings

    # ---- heads ------------------------------------------------------------
    def heads(self) -> list[dict]:
        return list(self._heads)

    def head(self, hid: str) -> dict | None:
        return next((h for h in self._heads if h["id"] == hid), None)

    def add_head(self, head: dict) -> dict:
        with self._lock:
            self._heads.append(head)
            self._save_json(self.heads_path, self._heads)
        return head

    def update_head(self, hid: str, patch: dict) -> dict | None:
        with self._lock:
            h = next((x for x in self._heads if x["id"] == hid), None)
            if not h:
                return None
            h.update(patch)
            self._save_json(self.heads_path, self._heads)
            return h

    def delete_head(self, hid: str) -> bool:
        with self._lock:
            before = len(self._heads)
            self._heads = [h for h in self._heads if h["id"] != hid]
            if len(self._heads) != before:
                self._save_json(self.heads_path, self._heads)
                return True
            return False

    def active_head(self, head_type: str) -> dict | None:
        hid = self._settings.flash_head if head_type == "flash" else self._settings.pro_head
        return self.head(hid) if hid else None

    def set_active_head(self, head_type: str, hid: str) -> dict | None:
        with self._lock:
            h = next((x for x in self._heads if x["id"] == hid), None)
            if not h:
                return None
            for x in self._heads:
                if x.get("type") == head_type:
                    x["active"] = (x["id"] == hid)
            if head_type == "flash":
                self._settings.flash_head = hid
            else:
                self._settings.pro_head = hid
            self._save_json(self.heads_path, self._heads)
            self._save_json(self.settings_path, self._settings.to_dict())
            return h

    def _seed_heads(self):
        """Auto-register existing head files on first run."""
        if self._heads:
            return
        now = time.time()
        candidates = [("flash", "data/flash_head.npy"), ("pro", "data/pro_head.npy")]
        changed = False
        for htype, rel in candidates:
            full = os.path.join(self.root, rel)
            if os.path.exists(full):
                shape = self._npy_shape(full)
                is_first = not any(h["type"] == htype for h in self._heads)
                hid = f"{htype}_seed_{int(now)}"
                self._heads.append({
                    "id": hid,
                    "type": htype,
                    "path": rel,
                    "shape": list(shape) if shape is not None else None,
                    "n_workers": (shape[0] // 1024) if (shape and htype == "flash" and len(shape) == 1) else (shape[0] if (shape and htype == "flash" and len(shape) >= 2) else None),
                    "training_workers": [],
                    "dataset": "",
                    "profile": "",
                    "params": {},
                    "eval": {},
                    "created_at": now,
                    "active": is_first,
                    "status": "seeded",
                    "note": "auto-registered from existing file",
                })
                if is_first:
                    if htype == "flash":
                        self._settings.flash_head = hid
                    else:
                        self._settings.pro_head = hid
                changed = True
        if changed:
            self._save_json(self.heads_path, self._heads)
            self._save_json(self.settings_path, self._settings.to_dict())

    @staticmethod
    def _npy_shape(path: str):
        try:
            import numpy as np
            return np.load(path).shape
        except Exception:
            return None

    # ---- tasks ------------------------------------------------------------
    def tasks(self) -> list[dict]:
        return list(self._tasks)

    def task(self, tid: str) -> dict | None:
        return next((t for t in self._tasks if t["id"] == tid), None)

    def add_task(self, task: dict) -> dict:
        with self._lock:
            self._tasks.append(task)
            self._tasks = self._tasks[-100:]
            self._save_json(self.tasks_path, self._tasks)
        return task

    def update_task(self, tid: str, patch: dict) -> dict | None:
        with self._lock:
            t = next((x for x in self._tasks if x["id"] == tid), None)
            if not t:
                return None
            t.update(patch)
            self._save_json(self.tasks_path, self._tasks)
            return t

    def task_log_path(self, tid: str) -> str:
        return os.path.join(self.dir, "tasks", f"{tid}.log")

    # ---- helpers ----------------------------------------------------------
    def _load_json(self, path: str, default: Any) -> Any:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                return default
        return default

    def _save_json(self, path: str, data: Any):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def abs(self, rel: str) -> str:
        """Resolve a project-relative path to absolute."""
        if os.path.isabs(rel):
            return rel
        return os.path.join(self.root, rel)
