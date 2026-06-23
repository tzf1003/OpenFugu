#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Evaluate the pro per-step router.
"""
eval_router_pro.py — compare pro per-step routing strategies.

Reports:
  - base head rollout score
  - trained pro head rollout score
  - max_turns sweep
  - per-domain score, routing distribution, sample count, held-out flag

Each strategy runs the full Coordinator multi-turn loop (Worker/Thinker/
Verifier) over a CloudWorkerPool or FakeCloudWorkerPool and scores the final
answer with the verifiable evaluator. This makes real API calls unless --fake.

Usage (offline):
  python eval/eval_router_pro.py --fake --dataset /tmp/ds.jsonl
  python eval/eval_router_pro.py --fake --dataset /tmp/ds.jsonl \
      --head /tmp/pro_head.npy --max-turns 4 --held-out
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter, defaultdict
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "train"))
sys.path.insert(0, os.path.join(_ROOT, "openfugu"))
from verifiable_data import read_jsonl, score_sample              # noqa: E402
from config import load_config                                    # noqa: E402
from cloud_pool import CloudWorkerPool, FakeCloudWorkerPool       # noqa: E402
from mini import FuguRouter, Coordinator, HEAD_ROWS, HIDDEN       # noqa: E402


def make_pool(config, fake, answers=None):
    if fake:
        return FakeCloudWorkerPool(config, answers=answers or {})
    return CloudWorkerPool(config)


def run_strategy(router, worker_fn, samples, max_turns):
    coord = Coordinator(router, worker_fn, max_turns=max_turns, sample=False)
    scores, routing, domains = [], Counter(), defaultdict(list)
    for s in samples:
        res = coord.run(s["prompt"])
        sc = score_sample(s, res.final)
        scores.append(sc)
        domains[s.get("domain", "?")].append(sc)
        for t in res.turns:
            routing[(t.role_name, t.agent_id % 99)] += 1
    return np.mean(scores), routing, domains, len(scores)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate pro per-step router.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--head", default=None, help="trained pro_head.npy")
    ap.add_argument("--model", default=os.environ.get("FUGU_MODEL", "Qwen/Qwen3-0.6B"))
    ap.add_argument("--vector", default=os.environ.get("FUGU_VECTOR", "model_iter_60.npy"))
    ap.add_argument("--model-name", default="openfugu-pro")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--held-out", action="store_true")
    ap.add_argument("--n", type=int, default=8, help="cap samples for cost")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    config = load_config(args.config)
    worker_ids = config.worker_ids(args.model_name)
    n_workers = len(worker_ids)
    samples = read_jsonl(args.dataset)[:args.n]
    print(f"[eval-pro] {len(samples)} samples, {n_workers} workers, "
          f"held_out={args.held_out}", flush=True)

    fake_answers = {}
    if args.fake:
        for wid in worker_ids:
            fake_answers[wid] = {"": "The answer is 42."}
    pool = make_pool(config, args.fake, answers=fake_answers)
    worker_fn = pool.as_coordinator_worker(worker_ids)

    try:
        router = FuguRouter(args.model, args.vector, seed=args.seed)
        have_router = True
        print("[eval-pro] router: Qwen3-0.6B", flush=True)
    except Exception as e:
        if not args.fake:
            raise SystemExit(f"[eval-pro] Qwen/vector required for live eval: {e}")
        have_router = False
        print(f"[eval-pro] no Qwen3-0.6B ({str(e)[:60]}); mock router (--fake only)", flush=True)

    def make_router(head_vec=None):
        if have_router:
            import torch
            n = n_workers
            if head_vec is not None:
                h = np.asarray(head_vec, dtype=np.float64).reshape(HEAD_ROWS, HIDDEN)
                router.head = torch.from_numpy(h[:n].copy()).float().to(router.device)
            orig_route = FuguRouter.route
            def route_n(self, messages, sample=False, agent_mask=None):
                r = orig_route(self, messages, sample=sample, agent_mask=agent_mask)
                r["agent_id"] = r["agent_id"] % n
                return r
            router.route = route_n.__get__(router, FuguRouter)
            del route_n
            return router
        class MockR:
            def __init__(s): s.t = 0
            def route(s, messages, sample=False, agent_mask=None):
                from mini import ROLE_NAMES
                aid = s.t % n_workers
                rid = 0 if s.t % 2 == 0 else 2
                s.t += 1
                return {"agent_id": aid, "role_id": rid, "role_name": ROLE_NAMES[rid],
                        "agent_logits": np.zeros(n_workers), "role_logits": np.zeros(3)}
        return MockR()

    # base head
    base_score, base_routing, base_domains, _ = run_strategy(
        make_router(), worker_fn, samples, args.max_turns)
    print(f"  base head  : {base_score:.3f}  (max_turns={args.max_turns})", flush=True)

    # trained head
    trained_score, tr_routing, tr_domains, _ = (None, None, None, 0)
    if args.head:
        hv = np.load(args.head).astype(np.float64)
        trained_score, tr_routing, tr_domains, _ = run_strategy(
            make_router(hv), worker_fn, samples, args.max_turns)
        print(f"  trained    : {trained_score:.3f}  (max_turns={args.max_turns})", flush=True)

    # max_turns sweep (base head)
    print("  max_turns sweep (base head):", flush=True)
    sweep = {}
    for mt in [2, 3, 5]:
        sc, _, _, _ = run_strategy(make_router(), worker_fn, samples, mt)
        sweep[mt] = sc
        print(f"    turns={mt}: {sc:.3f}", flush=True)

    print(f"\n=== pro router eval (n={len(samples)}, held_out={args.held_out}) ===")
    print(f"  base head score      : {base_score:.3f}")
    if args.head:
        lift = (trained_score - base_score) / base_score * 100 if base_score > 0 else 0.0
        print(f"  trained head score   : {trained_score:.3f}")
        print(f"  lift vs base         : {lift:+.1f}%")
    print("  per-domain (base head):")
    for d, scs in sorted(base_domains.items()):
        print(f"    {d:12s} {np.mean(scs):.3f}  (n={len(scs)})")
    if args.head and tr_domains:
        print("  per-domain (trained):")
        for d, scs in sorted(tr_domains.items()):
            print(f"    {d:12s} {np.mean(scs):.3f}  (n={len(scs)})")
    print("  routing distribution (base head):")
    for (role, aid), c in sorted(base_routing.items(), key=lambda x: -x[1])[:10]:
        print(f"    {role:10s} agent={aid}  {c}")
    if len(samples) < 50:
        print("  WARNING: small sample — treat as smoke test, not a generalization claim.")
    if not args.held_out:
        print("  NOTE: not marked held-out; may be training set (overfit risk).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
