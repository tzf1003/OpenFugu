#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Offline test suite — no API key required.
"""
test_offline.py — minimum runnable tests for the dual-model product prototype.

Covers:
  - config: disabled workers excluded, model worker lists loaded correctly
  - flash : request model=openfugu-flash calls exactly one worker, returns answer
  - pro   : multi-turn loop, verifier ACCEPT terminates
  - evaluators: numeric / exact / choice / regex / json_fields / tool_calls

Run:
  python tests/test_offline.py
"""
from __future__ import annotations
import json, os, sys, tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "openfugu"))
sys.path.insert(0, os.path.join(_ROOT, "train"))


def _ok(name):
    print(f"  PASS — {name}")


# ---- config ----------------------------------------------------------------
def test_config_disabled_worker():
    from config import load_config, FuguConfig
    import json as _json
    cfg = {
        "models": {
            "openfugu-flash": {"mode": "per_question", "workers": ["a", "b"]},
            "openfugu-pro": {"mode": "per_step", "workers": ["a", "c"], "max_turns": 3},
        },
        "workers": {
            "a": {"provider_model": "openai/x", "enabled": True},
            "b": {"provider_model": "openai/y", "enabled": False},   # disabled
            "c": {"provider_model": "openai/z", "enabled": True},
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(cfg, f); path = f.name
    c = load_config(path)
    os.unlink(path)
    # disabled worker b must NOT appear in flash's resolved pool
    assert c.worker_ids("openfugu-flash") == ["a"], c.worker_ids("openfugu-flash")
    assert c.worker_ids("openfugu-pro") == ["a", "c"]
    _ok("config: disabled worker excluded; model worker lists correct")


def test_config_missing_model_rejected():
    from config import load_config
    import json as _json
    cfg = {"models": {"openfugu-flash": {"mode": "per_question", "workers": ["a"]}},
           "workers": {"a": {"provider_model": "openai/x", "enabled": True}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(cfg, f); path = f.name
    try:
        load_config(path)
        assert False, "should have rejected config missing openfugu-pro"
    except ValueError:
        pass
    finally:
        os.unlink(path)
    _ok("config: missing openfugu-pro rejected")


# ---- evaluators ------------------------------------------------------------
def test_evaluators():
    from verifiable_data import (eval_numeric, eval_exact, eval_choice,
                                 eval_regex, eval_json_fields, eval_tool_calls)
    assert eval_numeric("the total is 1,234.", "1234") == 1.0
    assert eval_numeric("no number", "5") == 0.0
    assert eval_exact("Paris\n", "paris") == 1.0
    assert eval_exact("Paris", "London") == 0.0
    assert eval_choice("answer: B.", "B") == 1.0
    assert eval_choice("C. no", "B") == 0.0
    assert eval_regex("code 42", r"code \d+") == 1.0
    assert eval_regex("none", r"\d+") == 0.0
    assert eval_json_fields('{"name":"fugu","n":"7"}', {"name": "fugu", "n": 7}) == 1.0
    assert eval_json_fields('{"name":"fugu"}', {"name": "fugu", "x": 1}) == 0.5
    gold = [{"name": "get", "arguments": {"id": "76"}}]
    assert eval_tool_calls('<answer>' + json.dumps(gold) + '</answer>', gold) > 0.9
    assert eval_tool_calls('<answer>[]</answer>', gold) == 0.0
    _ok("evaluators: numeric/exact/choice/regex/json_fields/tool_calls")


# ---- flash (per-question) --------------------------------------------------
def test_flash_one_worker():
    from config import load_config, FuguConfig
    from cloud_pool import FakeCloudWorkerPool
    import product_serve as ps
    import json as _json
    cfg = {
        "models": {"openfugu-flash": {"mode": "per_question", "workers": ["w1", "w2"]},
                   "openfugu-pro": {"mode": "per_step", "workers": ["w1", "w2"], "max_turns": 3}},
        "workers": {"w1": {"provider_model": "openai/a", "enabled": True, "max_tokens": 64},
                    "w2": {"provider_model": "openai/b", "enabled": True, "max_tokens": 64}},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(cfg, f); path = f.name
    c = load_config(path)
    os.unlink(path)
    calls = []
    pool = FakeCloudWorkerPool(c, answers={"w1": {"math": "The answer is 42."}})
    real_call = pool.call
    def counting_call(wid, messages, role="", context=None):
        calls.append(wid)
        return real_call(wid, messages, role=role, context=context)
    pool.call = counting_call
    flash = ps.FlashRouter(c, "openfugu-flash")
    state = ps.ServerState(config=c, pool=pool, flash=flash,
                           pro_router_factory=ps._build_pro_factory(c, None, None, None, pool))
    r = ps.run_flash(state, [{"role": "user", "content": "solve this math problem"}], debug=True)
    assert r["usage"]["fugu_turns"] == 1, r["usage"]
    assert len(calls) == 1, f"flash must call exactly one worker, got {calls}"
    assert "42" in r["choices"][0]["message"]["content"]
    assert r["usage"]["fugu_mode"] == "per_question"
    _ok("flash: one routing decision, one worker call, correct answer, turns=1")


# ---- pro (per-step) --------------------------------------------------------
def test_pro_multi_turn_accept():
    from config import load_config
    from cloud_pool import FakeCloudWorkerPool
    import product_serve as ps
    import json as _json
    cfg = {
        "models": {"openfugu-flash": {"mode": "per_question", "workers": ["w1"]},
                   "openfugu-pro": {"mode": "per_step", "workers": ["w1", "w2"], "max_turns": 5}},
        "workers": {"w1": {"provider_model": "openai/a", "enabled": True},
                    "w2": {"provider_model": "openai/b", "enabled": True}},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(cfg, f); path = f.name
    c = load_config(path)
    os.unlink(path)
    pool = FakeCloudWorkerPool(c)
    flash = ps.FlashRouter(c, "openfugu-flash")
    state = ps.ServerState(config=c, pool=pool, flash=flash,
                           pro_router_factory=ps._build_pro_factory(c, None, None, None, pool))
    r = ps.run_pro(state, [{"role": "user", "content": "solve this problem"}], debug=True)
    usage = r["usage"]
    assert usage["fugu_mode"] == "per_step"
    assert usage["fugu_turns"] >= 2, f"pro should run multiple turns, got {usage}"
    assert usage["fugu_terminated_by"] == "verifier_accept", usage
    _ok("pro: multi-turn loop, verifier ACCEPT terminates, turns>=2")


# ---- HTTP smoke ------------------------------------------------------------
def test_http_models_endpoint():
    import product_serve as ps
    from config import load_config
    from cloud_pool import FakeCloudWorkerPool
    from http.server import ThreadingHTTPServer
    import urllib.request
    c = load_config(os.path.join(_ROOT, "configs", "fugu.yaml"))
    pool = FakeCloudWorkerPool(c)
    flash = ps.FlashRouter(c, "openfugu-flash")
    state = ps.ServerState(config=c, pool=pool, flash=flash,
                           pro_router_factory=ps._build_pro_factory(c, None, None, None, pool))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.make_handler(state))
    port = srv.server_address[1]
    import threading
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models") as r:
            data = json.loads(r.read())
        ids = {m["id"] for m in data["data"]}
        assert ids == {"openfugu-flash", "openfugu-pro"}, ids
        # flash POST
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps({"model": "openfugu-flash",
                             "messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        assert resp["usage"]["fugu_mode"] == "per_question"
        assert resp["usage"]["fugu_turns"] == 1
    finally:
        srv.shutdown()
    _ok("http: /v1/models lists flash+pro; flash POST returns per_question, turns=1")


def main():
    print("Running offline tests (no API key)...")
    test_config_disabled_worker()
    test_config_missing_model_rejected()
    test_evaluators()
    test_flash_one_worker()
    test_pro_multi_turn_accept()
    test_http_models_endpoint()
    print("\nALL PASS — offline test suite green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
