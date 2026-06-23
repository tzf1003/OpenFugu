#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Flash per-question router training.
# Reuses train/train_trinity_real.py's CMA-ES structure + Qwen3-0.6B features.
"""
train_flash_router.py — train the openfugu-flash per-question router head.

The router reads a question once and picks ONE worker. Fitness = the score of
the selected worker on that question. Because profile_workers.py already cached
every worker's score on every sample, CMA-ES optimizes against the cache with
ZERO new API calls — this is what makes training cheap after profiling.

  head : (n_workers, HIDDEN) bias-free linear head over Qwen3-0.6B penultimate
         hidden state of the question. route = argmax(head @ feat).
  train: sep-CMA-ES (CMA_diagonal), reusing train_trinity_real.py's approach.

Usage (needs Qwen3-0.6B for features):
  python train/train_flash_router.py \
      --model <Qwen3-0.6B dir> --dataset data/router_train.jsonl \
      --profile data/worker_profile.jsonl --out flash_head.npy

If no backbone model is available, --no-backbone falls back to hashing features
so the loop still runs end-to-end on the cache (for testing the pipeline).
"""
from __future__ import annotations
import argparse, os, sys, json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verifiable_data import read_jsonl, score_sample

HIDDEN = 1024


def load_profile(path: str) -> dict:
    """profile -> {(sample_id, worker_id): score}."""
    out = {}
    for r in read_jsonl(path):
        out[(r["sample_id"], r["worker_id"])] = r["score"]
    return out


class HashBackbone:
    """No-model fallback: deterministic hash vector as the question feature.
    Lets the training loop run without Qwen3-0.6B (for pipeline testing only —
    a hash feature has no generalization, it just memorizes ids)."""
    def __init__(self, dim=HIDDEN, seed=0):
        self.rng = np.random.default_rng(seed)
        self.dim = dim
        self._cache = {}

    def feature(self, text: str) -> np.ndarray:
        if text in self._cache:
            return self._cache[text]
        h = self.rng.standard_normal(self.dim)
        h /= np.linalg.norm(h) + 1e-9
        self._cache[text] = h
        return h


class QwenBackbone:
    """Real Qwen3-0.6B -> penultimate hidden state (the router feature)."""
    def __init__(self, model_dir, device=None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        try:
            self.model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.float32).eval()
        except TypeError:
            self.model = AutoModelForCausalLM.from_pretrained(model_dir, torch_dtype=torch.float32).eval()
        if device:
            self.model.to(device)
        self.device = next(self.model.parameters()).device
        self._cache = {}

    def feature(self, question: str) -> np.ndarray:
        if question in self._cache:
            return self._cache[question]
        torch = self.torch
        ids = self.tok(f"user: {question}", return_tensors="pt").to(self.device)
        with torch.no_grad():
            h = self.model.model(**ids).last_hidden_state[0, -2, :]
        v = h.float().cpu().numpy()
        self._cache[question] = v
        return v


def route(head_vec, feat, n_workers):
    W = head_vec.reshape(n_workers, HIDDEN)
    return int(np.argmax(W @ feat))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train flash per-question router head.")
    ap.add_argument("--model", default=os.environ.get("FUGU_MODEL", "Qwen/Qwen3-0.6B"))
    ap.add_argument("--no-backbone", action="store_true",
                    help="use hash features instead of Qwen3-0.6B (pipeline test only)")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--profile", required=True, help="worker_profile.jsonl from profile_workers.py")
    ap.add_argument("--config", default=None)
    ap.add_argument("--model-name", default="openfugu-flash")
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--sigma0", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="flash_head.npy")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "openfugu"))
    from config import load_config
    config = load_config(args.config)
    worker_ids = config.worker_ids(args.model_name)
    n_workers = len(worker_ids)

    samples = read_jsonl(args.dataset)
    prof = load_profile(args.profile)
    # keep only samples that have a profile entry for at least one worker
    samples = [s for s in samples if any((s["id"], w) in prof for w in worker_ids)]
    if not samples:
        print("[flash-train] no samples with profile data; nothing to train on.", flush=True)
        return 1
    print(f"[flash-train] {len(samples)} samples, {n_workers} workers: {worker_ids}", flush=True)

    if args.no_backbone:
        bb = HashBackbone(dim=HIDDEN, seed=args.seed)
        print("[flash-train] backbone: HashBackbone (no Qwen3-0.6B — pipeline test only)", flush=True)
    else:
        bb = QwenBackbone(args.model)
        print("[flash-train] backbone: Qwen3-0.6B", flush=True)
    feats = [bb.feature(s["prompt"]) for s in samples]

    # reward of a routing decision = cached score of the chosen worker
    def fitness(head_vec):
        tot = 0.0
        for s, feat in zip(samples, feats):
            wid = route(head_vec, feat, n_workers)
            tot += prof.get((s["id"], worker_ids[wid]), 0.0)
        return tot / len(samples)

    # baselines from the cache (no API)
    per_worker = []
    for w in worker_ids:
        scs = [prof.get((s["id"], w), 0.0) for s in samples]
        per_worker.append(np.mean(scs))
    best_single = max(per_worker)
    print("[baseline] per-worker mean score: " +
          ", ".join(f"{worker_ids[w]}={per_worker[w]:.3f}" for w in range(n_workers)), flush=True)
    print(f"[baseline] best single worker = {best_single:.3f}", flush=True)

    # oracle: best worker per question (the routing ceiling)
    oracle = np.mean([max(prof.get((s["id"], w), 0.0) for w in worker_ids) for s in samples])
    print(f"[baseline] oracle (best per question) = {oracle:.3f}", flush=True)

    dim = n_workers * HIDDEN
    best_vec, best_fit = None, -1.0
    try:
        import cma
        es = cma.CMAEvolutionStrategy(np.zeros(dim), args.sigma0,
                                      {"seed": args.seed, "verbose": -9, "CMA_diagonal": True})
        for it in range(args.iters):
            cands = es.ask()
            fits = [fitness(c) for c in cands]
            es.tell(cands, [-f for f in fits])
            i = int(np.argmax(fits))
            if fits[i] > best_fit:
                best_fit, best_vec = fits[i], cands[i].copy()
            print(f"[iter {it}] best={best_fit:.3f}  (best single {best_single:.3f}, "
                  f"oracle {oracle:.3f})", flush=True)
    except ImportError:
        # Fallback: random search when cma isn't installed. Less sample-efficient
        # but keeps the pipeline runnable offline. Prefer cma for real runs.
        rng = np.random.default_rng(args.seed)
        print("[flash-train] cma not installed — using random search fallback", flush=True)
        pop = 16
        best_vec, best_fit = np.zeros(dim), fitness(np.zeros(dim))
        for it in range(args.iters):
            cands = [rng.normal(0, args.sigma0, dim) for _ in range(pop)]
            fits = [fitness(c) for c in cands]
            i = int(np.argmax(fits))
            if fits[i] > best_fit:
                best_fit, best_vec = fits[i], cands[i].copy()
            print(f"[iter {it}] best={best_fit:.3f}  (best single {best_single:.3f}, "
                  f"oracle {oracle:.3f})", flush=True)

    np.save(args.out, best_vec)
    lift = (best_fit - best_single) / best_single * 100 if best_single > 0 else float("inf")
    print(f"\n[result] flash router = {best_fit:.3f} vs best single {best_single:.3f} "
          f"(oracle {oracle:.3f}, lift {lift:+.1f}%)")
    print(f"[result] saved {args.out}  ({dim} floats = {n_workers}x{HIDDEN})")
    if best_fit >= best_single - 1e-9:
        print("PASS — flash router >= best single worker on cached profile")
    else:
        print("NOTE — router below best single; check worker complementarity / data size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
