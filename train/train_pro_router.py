#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Pro per-step router training (cloud worker pool).
# Reuses train/train_trinity_perstep.py's rollout-fitness + mini.py Coordinator.
"""
train_pro_router.py — train the openfugu-pro per-step router head.

Abstracts LocalPoolWorker (train_trinity_perstep.py) into a WorkerPool so the
per-step Coordinator can run over a CLOUD pool (CloudWorkerPool via litellm) or
a FAKE pool (offline). Fitness = terminal reward of the full multi-turn
step_trinity rollout (verifier ACCEPT / max_turns), scored against the
verifiable dataset.

  router : Qwen3-0.6B hidden state -> candidate head -> (agent_id, role_id)
  workers: CloudWorkerPool (litellm) or FakeCloudWorkerPool, by worker_id
  reward : per-sample evaluator score of the final answer
  train  : sep-CMA-ES (or random-search fallback) over the head

Cost note: each fitness eval is a multi-turn rollout with several worker
generations per sample. Cloud per-step training is EXPENSIVE. Use --fake and a
small dataset for the pipeline flow; scale up deliberately and budget-aware.

Usage (offline pipeline test):
  python train/train_pro_router.py --fake --dataset /tmp/ds.jsonl \
      --iters 4 --out pro_head.npy

Usage (cloud, expensive):
  OPENAI_API_KEY=... python train/train_pro_router.py \
      --model <Qwen3-0.6B dir> --vector model_iter_60.npy \
      --dataset data/router_train.jsonl --out pro_head.npy
"""
from __future__ import annotations
import argparse, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "openfugu"))
from verifiable_data import read_jsonl, score_sample          # noqa: E402
from config import load_config                                # noqa: E402
from cloud_pool import CloudWorkerPool, FakeCloudWorkerPool   # noqa: E402
from mini import FuguRouter, Coordinator, HEAD_ROWS, HIDDEN   # noqa: E402


def make_pool(config, fake, answers=None):
    if fake:
        return FakeCloudWorkerPool(config, answers=answers or {})
    return CloudWorkerPool(config)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train pro per-step router head (cloud/fake pool).")
    ap.add_argument("--config", default=None)
    ap.add_argument("--model", default=os.environ.get("FUGU_MODEL", "Qwen/Qwen3-0.6B"))
    ap.add_argument("--vector", default=os.environ.get("FUGU_VECTOR", "model_iter_60.npy"))
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model-name", default="openfugu-pro")
    ap.add_argument("--fake", action="store_true", help="offline FakeCloudWorkerPool (no API)")
    ap.add_argument("--n-train", type=int, default=8, help="cap samples per fitness eval")
    ap.add_argument("--iters", type=int, default=6)
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--sigma0", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="pro_head.npy")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    worker_ids = config.worker_ids(args.model_name)
    n_workers = len(worker_ids)
    samples = read_jsonl(args.dataset)[:args.n_train]
    print(f"[pro-train] {len(samples)} samples, {n_workers} workers, "
          f"max_turns={args.max_turns}", flush=True)

    # fake answers keyed by domain keyword so the offline loop can ACCEPT
    fake_answers = {}
    if args.fake:
        for wid in worker_ids:
            fake_answers[wid] = {"": "The answer is 42."}  # generic accept-friendly
    pool = make_pool(config, args.fake, answers=fake_answers)
    print(f"[pro-train] pool: {'Fake' if args.fake else 'Cloud'}WorkerPool", flush=True)

    # router backbone
    try:
        router = FuguRouter(args.model, args.vector, seed=args.seed)
        base_head = router.head.clone()
        print("[pro-train] router: Qwen3-0.6B + base vector", flush=True)
    except Exception as e:
        print(f"[pro-train] cannot load Qwen3-0.6B ({str(e)[:80]}); "
              f"using mock head (random, for pipeline test only)", flush=True)
        router = None
        base_head = np.zeros(HEAD_ROWS * HIDDEN)

    worker_fn = pool.as_coordinator_worker(worker_ids)

    def rollout_score(head_vec):
        if router is not None:
            import torch
            # remap agent rows to the active worker count
            n = n_workers
            h = head_vec.reshape(HEAD_ROWS, HIDDEN)
            router.head = torch.from_numpy(h[:n].copy()).float().to(router.device)
            # patch route to fold agent_id into [0,n)
            orig_route = FuguRouter.route
            def route_n(self, messages, sample=False, agent_mask=None):
                r = orig_route(self, messages, sample=sample, agent_mask=agent_mask)
                r["agent_id"] = r["agent_id"] % n
                return r
            router.route = route_n.__get__(router, FuguRouter)
            coord = Coordinator(router, worker_fn, max_turns=args.max_turns, sample=False)
            del route_n
        else:
            # mock router: deterministic, not trained
            class MockR:
                def __init__(s): s.t = 0
                def route(s, messages, sample=False, agent_mask=None):
                    from mini import ROLE_NAMES
                    aid = s.t % n_workers
                    rid = 0 if s.t % 2 == 0 else 2
                    s.t += 1
                    return {"agent_id": aid, "role_id": rid, "role_name": ROLE_NAMES[rid],
                            "agent_logits": np.zeros(n_workers), "role_logits": np.zeros(3)}
            coord = Coordinator(MockR(), worker_fn, max_turns=args.max_turns, sample=False)
        tot = 0.0
        for s in samples:
            res = coord.run(s["prompt"])
            tot += score_sample(s, res.final)
        return tot / max(len(samples), 1)

    base_vec = base_head.cpu().numpy().ravel() if hasattr(base_head, "cpu") else np.asarray(base_head).ravel()
    base_fit = rollout_score(base_vec)
    print(f"[pro-train] base head rollout score={base_fit:.3f}", flush=True)

    dim = HEAD_ROWS * HIDDEN
    best_vec, best_fit = base_vec.copy(), base_fit
    try:
        import cma
        es = cma.CMAEvolutionStrategy(base_vec, args.sigma0,
                                      {"seed": args.seed, "verbose": -9, "CMA_diagonal": True})
        for it in range(args.iters):
            cands = es.ask()
            fits = [rollout_score(c) for c in cands]
            es.tell(cands, [-f for f in fits])
            i = int(np.argmax(fits))
            if fits[i] > best_fit:
                best_fit, best_vec = fits[i], cands[i].copy()
            print(f"[iter {it}] best={best_fit:.3f} (base {base_fit:.3f})", flush=True)
    except ImportError:
        rng = np.random.default_rng(args.seed)
        print("[pro-train] cma not installed — random search fallback", flush=True)
        pop = 8
        for it in range(args.iters):
            cands = [base_vec + rng.normal(0, args.sigma0, dim) for _ in range(pop)]
            fits = [rollout_score(c) for c in cands]
            i = int(np.argmax(fits))
            if fits[i] > best_fit:
                best_fit, best_vec = fits[i], cands[i].copy()
            print(f"[iter {it}] best={best_fit:.3f} (base {base_fit:.3f})", flush=True)

    np.save(args.out, best_vec)
    print(f"\n[result] pro head = {best_fit:.3f} vs base {base_fit:.3f}")
    print(f"[result] saved {args.out}  ({dim} floats = {HEAD_ROWS}x{HIDDEN})")
    if best_fit > base_fit + 0.01:
        print("PASS — per-step head improved over base rollout")
    else:
        print(f"NOTE — no improvement over base in {args.iters} iters "
              f"(small scale / fake pool / saturated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
