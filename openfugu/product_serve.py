#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Dual-model product serving surface for the
# openfugu-flash / openfugu-pro prototype. Original code.
"""
product_serve.py — serve OpenFugu as two externally-visible models.

  openfugu-flash : per-question routing. The router reads the question once,
                   picks ONE cloud worker, that worker answers fully. Fast.
  openfugu-pro   : per-step coordination. Each turn re-routes (worker/role)
                   over the evolving transcript, running the Worker/Thinker/
                   Verifier loop until a verifier ACCEPT or max_turns. Slower,
                   finer-grained.

Both are exposed behind one OpenAI-compatible endpoint:
  GET  /v1/models            -> lists openfugu-flash, openfugu-pro
  POST /v1/chat/completions  -> dispatches on request.model

stdlib http.server only — no FastAPI/uvicorn. API keys come from the env via
litellm (CloudWorkerPool), never from config or code.

Run (offline mock, no API key):
  python openfugu/product_serve.py --config configs/fugu.yaml --port 8090

Run (live cloud workers via litellm):
  OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  python openfugu/product_serve.py --config configs/fugu.yaml --port 8090
"""
from __future__ import annotations
import argparse, json, os, sys, time, uuid
from dataclasses import dataclass
from typing import Callable

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config, FuguConfig          # noqa: E402
from cloud_pool import CloudWorkerPool, FakeCloudWorkerPool, WorkerPool  # noqa: E402

# Reuse the faithful TRINITY coordinator for pro's per-step loop. When a
# trained router vector is available it is wrapped as a FuguRouter; otherwise
# a FallbackRouter picks workers deterministically (and logs that clearly).
try:
    from mini import (FuguRouter, Coordinator, MockWorker, HEAD_ROWS, HIDDEN, N_AGENTS)  # noqa: E402
    HAVE_MINI = True
except Exception:
    HAVE_MINI = False


# ---- routing backends ------------------------------------------------------
class FlashRouter:
    """Per-question router: given the question, pick one worker id.

    A trained router (head over Qwen3-0.6B hidden state) is preferred. With no
    weights we fall back to the first enabled worker (deterministic) or
    round-robin, and log the choice so it is never a silent pretend-train."""
    def __init__(self, config: FuguConfig, model_name: str,
                 head_path: str | None = None, model_dir: str | None = None,
                 vector_path: str | None = None, round_robin: bool = False):
        self.config = config
        self.model_name = model_name
        self.worker_ids = config.worker_ids(model_name)
        self.round_robin = round_robin
        self._rr = 0
        self.router = None
        self.head = None
        if head_path and model_dir:
            self._load_trained(model_dir, vector_path or _base_vector(), head_path)

    def _load_trained(self, model_dir, vector_path, head_path):
        # Layer a trained flash head over the base SVF-applied backbone.
        self.router = FuguRouter(model_dir, vector_path, seed=0)
        h = np.load(head_path).astype(np.float64)
        n = len(self.worker_ids)
        need = n * HIDDEN
        if h.shape != (need,):
            raise ValueError(f"flash head must be {need} floats ({n} workers x {HIDDEN}), got {h.shape}")
        self.head = self.router.torch.from_numpy(h.copy()).float().reshape(n, HIDDEN).to(self.router.device)

    def select(self, query: str) -> tuple[str, str]:
        """Return (worker_id, reason)."""
        if self.router is not None and self.head is not None:
            import torch
            h = self.router._hidden([{"role": "user", "content": query}])
            wid_idx = int(torch.argmax(self.head @ h))
            return self.worker_ids[wid_idx], "trained_head"
        if self.round_robin:
            wid = self.worker_ids[self._rr % len(self.worker_ids)]
            self._rr += 1
            return wid, "round_robin"
        # fallback: first enabled worker — deterministic, clearly logged
        return self.worker_ids[0], "fallback_first_enabled"


class FallbackRouter:
    """A per-step router used when no trained TRINITY weights are available.

    It keeps the Coordinator contract (route -> agent_id, role_id, role_name)
    but picks deterministically: workers round-robin, roles alternate
    Worker -> Thinker/Verifier. This is explicitly NOT a trained router; the
    server logs that it is a mock so we never silently fake training."""
    def __init__(self, n_agents: int, seed: int = 0):
        self.n_agents = n_agents
        self.turn = 0
        self.rng = np.random.default_rng(seed)

    def route(self, messages, sample=False, agent_mask=None):
        from mini import ROLE_NAMES
        agent_id = self.turn % self.n_agents
        # alternate Worker / Verifier so the loop terminates via ACCEPT
        role_id = 0 if self.turn % 2 == 0 else 2
        self.turn += 1
        return {"agent_id": agent_id, "role_id": role_id,
                "role_name": ROLE_NAMES[role_id],
                "agent_logits": np.zeros(self.n_agents),
                "role_logits": np.zeros(3)}


# ---- the server ------------------------------------------------------------
@dataclass
class ServerState:
    config: FuguConfig
    pool: WorkerPool
    flash: FlashRouter
    pro_router_factory: Callable
    debug: bool = False


def _last_user(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")


def run_flash(state: ServerState, messages: list[dict], debug: bool) -> dict:
    query = _last_user(messages)
    wid, reason = state.flash.select(query)
    # flash: ONE worker call, full answer
    reply = state.pool.call(wid, messages, role="Worker")
    usage = {"fugu_mode": "per_question", "fugu_turns": 1}
    if debug:
        usage["fugu_selected_worker"] = wid
        usage["fugu_route_reason"] = reason
    return _chat_response(reply, "openfugu-flash", usage)


def run_pro(state: ServerState, messages: list[dict], debug: bool) -> dict:
    query = _last_user(messages)
    model = state.config.model("openfugu-pro")
    worker_ids = state.config.worker_ids("openfugu-pro")
    router, worker_fn, reason = state.pro_router_factory(worker_ids)
    coord = Coordinator(router, worker_fn, max_turns=model.max_turns, sample=False)
    res = coord.run(query, verbose=False)
    usage = {"fugu_mode": "per_step", "fugu_turns": len(res.turns)}
    if debug:
        usage["fugu_route_reason"] = reason
        usage["fugu_terminated_by"] = res.terminated_by
        usage["fugu_trace"] = [{"turn": t.turn, "agent_id": t.agent_id,
                                "role": t.role_name, "reply": t.reply[:120]}
                               for t in res.turns]
    return _chat_response(res.final, "openfugu-pro", usage)


def _chat_response(text: str, model: str, usage: dict) -> dict:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": usage,
    }


def _build_pro_factory(config, model_dir, vector_path, head_path, pool):
    """Return a factory (worker_ids) -> (router, worker_fn, reason)."""
    have_trained = bool(model_dir and (head_path or vector_path))
    if have_trained and HAVE_MINI:
        def factory(worker_ids):
            router = FuguRouter(model_dir, vector_path or _base_vector(), seed=0)
            if head_path:
                h = np.load(head_path).astype(np.float64)
                n = len(worker_ids)
                # pro head is full HEAD_ROWS x HIDDEN; remap agent rows to the
                # active worker count by taking the first n agent rows.
                if h.shape == (HEAD_ROWS * HIDDEN,):
                    full = h.reshape(HEAD_ROWS, HIDDEN)
                    active = np.concatenate([full[:n], full[N_AGENTS:]], axis=0)
                    head = router.torch.from_numpy(active.copy()).float().to(router.device)
                    # patch route to use the n-row head
                    router.head = head
                    orig_route = router.route
                    def route_n(messages, sample=False, agent_mask=None):
                        r = orig_route(messages, sample=sample, agent_mask=agent_mask)
                        # remap agent_id into [0, n)
                        r["agent_id"] = r["agent_id"] % n
                        return r
                    router.route = route_n
                else:
                    raise ValueError(f"pro head must be {HEAD_ROWS * HIDDEN} floats, got {h.shape}")
            worker_fn = pool.as_coordinator_worker(worker_ids)
            return router, worker_fn, "trained_trinity_head"
        return factory
    # fallback mock router — logged loudly at startup, never a silent fake
    def factory(worker_ids):
        router = FallbackRouter(len(worker_ids))
        worker_fn = pool.as_coordinator_worker(worker_ids)
        return router, worker_fn, "fallback_mock_router"
    return factory


def _base_vector():
    return os.environ.get("FUGU_VECTOR", "model_iter_60.npy")


def make_handler(state: ServerState):
    class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            from http.server import BaseHTTPRequestHandler  # noqa
            if self.path == "/v1/models":
                self._send(200, {"object": "list", "data": [
                    {"id": "openfugu-flash", "object": "model", "owned_by": "openfugu"},
                    {"id": "openfugu-pro", "object": "model", "owned_by": "openfugu"},
                ]})
            elif self.path in ("/health", "/"):
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._send(404, {"error": "not found"}); return
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                messages = req.get("messages", [])
                if not messages:
                    self._send(400, {"error": "messages required"}); return
                model = req.get("model", "openfugu-flash")
                debug = bool(req.get("debug", False)) or state.debug
                if model == "openfugu-flash":
                    self._send(200, run_flash(state, messages, debug))
                elif model == "openfugu-pro":
                    self._send(200, run_pro(state, messages, debug))
                else:
                    self._send(400, {"error": f"unknown model '{model}'; use openfugu-flash or openfugu-pro"})
            except Exception as e:
                self._send(500, {"error": str(e)})

        def log_message(self, *a):
            pass
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serve openfugu-flash / openfugu-pro.")
    ap.add_argument("--config", default=None, help="path to fugu.yaml (env FUGU_CONFIG)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--model-dir", default=None, help="Qwen3-0.6B dir for trained router (env FUGU_MODEL)")
    ap.add_argument("--vector", default=None, help="base TRINITY vector .npy (env FUGU_VECTOR)")
    ap.add_argument("--flash-head", default=None, help="trained flash router head .npy (n_workers x HIDDEN)")
    ap.add_argument("--pro-head", default=None, help="trained pro per-step head .npy (HEAD_ROWS x HIDDEN)")
    ap.add_argument("--round-robin", action="store_true", help="flash: round-robin instead of first-enabled")
    ap.add_argument("--live", action="store_true", help="use real litellm CloudWorkerPool (needs API keys)")
    ap.add_argument("--debug", action="store_true", help="expose internal worker/trace in usage by default")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    model_dir = args.model_dir or os.environ.get("FUGU_MODEL")
    vector = args.vector or os.environ.get("FUGU_VECTOR")

    if args.live:
        pool = CloudWorkerPool(config)
        print("[serve] worker pool: CloudWorkerPool (live, via litellm)", flush=True)
    else:
        pool = FakeCloudWorkerPool(config)
        print("[serve] worker pool: FakeCloudWorkerPool (offline mock — no API key)", flush=True)

    flash = FlashRouter(config, "openfugu-flash", head_path=args.flash_head,
                        model_dir=model_dir, vector_path=vector,
                        round_robin=args.round_robin)
    print(f"[serve] flash router: {_router_label(flash)}", flush=True)

    pro_factory = _build_pro_factory(config, model_dir, vector, args.pro_head, pool)
    # probe once to report the pro router mode
    _wid = config.worker_ids("openfugu-pro")
    _r, _w, reason = pro_factory(_wid)
    print(f"[serve] pro router: {reason}", flush=True)

    state = ServerState(config=config, pool=pool, flash=flash,
                        pro_router_factory=pro_factory, debug=args.debug)
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(state))
    print(f"[serve] OpenFugu listening on :{args.port} — openfugu-flash / openfugu-pro", flush=True)
    srv.serve_forever()


def _router_label(flash: FlashRouter) -> str:
    wid, reason = flash.select("ping")
    return f"{reason} (first worker={wid})"


if __name__ == "__main__":
    main()
