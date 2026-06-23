#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Profile each cloud worker on each verifiable sample.
"""
profile_workers.py — cache every worker's answer + score on every sample.

Profiling happens ONCE, before router training, so the CMA-ES trainer reuses
cached results instead of re-burning API budget. Output is one JSONL record per
(worker, sample):

  {"sample_id":"...","worker_id":"...","output":"...","score":0.0,
   "latency":1.23,"error":null}

Usage:
  python train/profile_workers.py --config configs/fugu.yaml \
      --dataset data/router_train.jsonl --out data/worker_profile.jsonl

  # profile only the flash pool, offline (fake workers) for a smoke test:
  python train/profile_workers.py --dataset /tmp/router_train.jsonl \
      --out /tmp/profile.jsonl --model openfugu-flash --fake

Re-running appends/resumes: existing (sample_id, worker_id) pairs in --out are
skipped, so an interrupted profile run can be restarted without duplicate cost.
"""
from __future__ import annotations
import argparse, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "openfugu"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config                                   # noqa: E402
from cloud_pool import CloudWorkerPool, FakeCloudWorkerPool      # noqa: E402
from verifiable_data import read_jsonl, score_sample, write_jsonl  # noqa: E402


def _load_existing(path: str) -> set[tuple[str, str]]:
    seen = set()
    if not os.path.exists(path):
        return seen
    for r in read_jsonl(path):
        seen.add((r.get("sample_id"), r.get("worker_id")))
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser(description="Profile cloud workers on a dataset.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dataset", required=True, help="unified-format .jsonl")
    ap.add_argument("--out", default="data/worker_profile.jsonl")
    ap.add_argument("--model", default="openfugu-flash",
                    help="which model's worker pool to profile")
    ap.add_argument("--fake", action="store_true",
                    help="use FakeCloudWorkerPool (offline, no API key)")
    ap.add_argument("--max-tokens-override", type=int, default=None)
    args = ap.parse_args(argv)

    config = load_config(args.config)
    worker_ids = config.worker_ids(args.model)
    samples = read_jsonl(args.dataset)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    seen = _load_existing(args.out)

    if args.fake:
        pool = FakeCloudWorkerPool(config)
        print("[profile] pool: FakeCloudWorkerPool (offline)", flush=True)
    else:
        pool = CloudWorkerPool(config)
        print(f"[profile] pool: CloudWorkerPool (live) — "
              f"{len(worker_ids)} workers: {worker_ids}", flush=True)

    print(f"[profile] {len(samples)} samples x {len(worker_ids)} workers "
          f"= {len(samples) * len(worker_ids)} calls "
          f"({len(seen)} already cached)", flush=True)

    records = []
    n_done = 0
    for s in samples:
        msgs = [{"role": "user", "content": s["prompt"]}]
        for wid in worker_ids:
            if (s["id"], wid) in seen:
                continue
            t0 = time.time()
            try:
                out = pool.call(wid, msgs, role="Worker")
                err = None
            except Exception as e:
                out, err = "", str(e)
            dt = time.time() - t0
            score = score_sample(s, out) if err is None else 0.0
            rec = {"sample_id": s["id"], "worker_id": wid, "output": out,
                   "score": score, "latency": round(dt, 3), "error": err}
            records.append(rec)
            n_done += 1
            if err:
                print(f"[profile] ERROR {wid} on {s['id']}: {err[:80]}", flush=True)

    # append new records to the cache file
    if records:
        with open(args.out, "a") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[profile] wrote {n_done} new records to {args.out}", flush=True)

    # summary
    all_recs = read_jsonl(args.out)
    by_worker = {}
    for r in all_recs:
        by_worker.setdefault(r["worker_id"], []).append(r["score"])
    print("[profile] mean score per worker:", flush=True)
    for wid, scs in sorted(by_worker.items()):
        print(f"  {wid:16s} {sum(scs)/len(scs):.3f}  (n={len(scs)})", flush=True)


if __name__ == "__main__":
    main()
