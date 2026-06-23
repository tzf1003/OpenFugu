#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Evaluate the flash per-question router.
"""
eval_router_flash.py — compare routing strategies on a (held-out) profile cache.

Reports:
  - best single worker score
  - random worker score
  - trained flash router score
  - oracle best-per-question score
  - lift %, per-domain score, routing distribution, sample count, held-out flag

Runs entirely on a profile cache (no API calls), so evaluation is free and
reproducible. The router head is applied with the same backbone used in training
(Qwen3-0.6B or hash fallback).

Usage:
  python eval/eval_router_flash.py \
      --dataset data/router_eval.jsonl --profile data/worker_profile_eval.jsonl \
      --head flash_head.npy --model <Qwen3-0.6B dir>
  # no backbone (hash, for pipeline check):
  python eval/eval_router_flash.py --no-backbone \
      --dataset /tmp/ds.jsonl --profile /tmp/prof.jsonl --head /tmp/flash_head.npy
"""
from __future__ import annotations
import argparse, os, sys, json
from collections import Counter, defaultdict
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "train"))
sys.path.insert(0, os.path.join(_ROOT, "openfugu"))
from verifiable_data import read_jsonl                                # noqa: E402
from train_flash_router import load_profile, HashBackbone, HIDDEN, route  # noqa: E402
from config import load_config                                        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate flash per-question router.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--head", required=True, help="flash_head.npy")
    ap.add_argument("--model", default=os.environ.get("FUGU_MODEL", "Qwen/Qwen3-0.6B"))
    ap.add_argument("--no-backbone", action="store_true")
    ap.add_argument("--model-name", default="openfugu-flash")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--held-out", action="store_true", help="mark this as a held-out set")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    worker_ids = config.worker_ids(args.model_name)
    n = len(worker_ids)
    samples = read_jsonl(args.dataset)
    prof = load_profile(args.profile)
    samples = [s for s in samples if any((s["id"], w) in prof for w in worker_ids)]
    print(f"[eval-flash] {len(samples)} samples, {n} workers, "
          f"held_out={args.held_out}", flush=True)

    if args.no_backbone:
        bb = HashBackbone(dim=HIDDEN, seed=args.seed)
    else:
        from train_flash_router import QwenBackbone
        bb = QwenBackbone(args.model)
    feats = {s["id"]: bb.feature(s["prompt"]) for s in samples}
    head = np.load(args.head).astype(np.float64)

    rng = np.random.default_rng(args.seed)

    def worker_score(wid):
        return np.mean([prof.get((s["id"], wid), 0.0) for s in samples])

    per_worker = {w: worker_score(w) for w in worker_ids}
    best_single = max(per_worker.values())
    best_single_w = max(per_worker, key=per_worker.get)

    # random routing
    rand_scores = []
    for s in samples:
        wid = worker_ids[int(rng.integers(n))]
        rand_scores.append(prof.get((s["id"], wid), 0.0))
    random_score = np.mean(rand_scores)

    # trained router
    router_scores, routing = [], Counter()
    domain_scores = defaultdict(list)
    for s in samples:
        wid_idx = route(head, feats[s["id"]], n)
        wid = worker_ids[wid_idx]
        sc = prof.get((s["id"], wid), 0.0)
        router_scores.append(sc)
        routing[wid] += 1
        domain_scores[s.get("domain", "?")].append(sc)
    router_score = np.mean(router_scores)

    # oracle
    oracle = np.mean([max(prof.get((s["id"], w), 0.0) for w in worker_ids) for s in samples])

    lift = (router_score - best_single) / best_single * 100 if best_single > 0 else float("inf")
    print(f"\n=== flash router eval (n={len(samples)}, held_out={args.held_out}) ===")
    print(f"  best single worker : {best_single:.3f}  ({best_single_w})")
    print(f"  random worker      : {random_score:.3f}")
    print(f"  trained router     : {router_score:.3f}")
    print(f"  oracle (per-q best): {oracle:.3f}")
    print(f"  lift vs best single: {lift:+.1f}%")
    print(f"  router/oracle      : {router_score/oracle:.1%}" if oracle > 0 else "  router/oracle: n/a")
    print("  per-worker score:")
    for w in worker_ids:
        print(f"    {w:16s} {per_worker[w]:.3f}")
    print("  routing distribution:")
    for w, c in sorted(routing.items(), key=lambda x: -x[1]):
        print(f"    {w:16s} {c} ({c/len(samples):.0%})")
    print("  per-domain router score:")
    for d, scs in sorted(domain_scores.items()):
        print(f"    {d:12s} {np.mean(scs):.3f}  (n={len(scs)})")
    # honesty flag
    if len(samples) < 50:
        print("  WARNING: small sample — treat as smoke test, not a generalization claim.")
    if not args.held_out:
        print("  NOTE: not marked held-out; may be training set (overfit risk).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
