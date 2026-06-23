#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Build/union verifiable training datasets.
"""
build_verifiable_dataset.py — assemble a unified-format training set from one
or more sources, writing one JSONL file for the router trainer.

  python train/build_verifiable_dataset.py \
      --sources gsm8k,toolscale,mmlu \
      --out data/router_train.jsonl --limit-per-source 1000

  # include user commercial data (most important):
  python train/build_verifiable_dataset.py \
      --sources custom,data/gsm8k.jsonl \
      --custom-path data/my_tasks.jsonl --out data/router_train.jsonl

Sources: gsm8k, toolscale, mmlu, humaneval (network datasets) and custom (a
local .jsonl in the unified schema, passed via --custom-path). Custom data can
also be named directly as a source by passing its file path.
"""
from __future__ import annotations
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verifiable_data import (DATASET_REGISTRY, custom_jsonl_adapter,
                             write_jsonl, list_sources)


def parse_sources(raw: str):
    return [s.strip() for s in raw.split(",") if s.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a unified verifiable training set.")
    ap.add_argument("--sources", default="gsm8k", help="comma-separated source names")
    ap.add_argument("--out", default="data/router_train.jsonl")
    ap.add_argument("--limit-per-source", type=int, default=1000)
    ap.add_argument("--custom-path", default=None,
                    help="path to a custom .jsonl in unified schema (for source 'custom')")
    args = ap.parse_args(argv)

    sources = parse_sources(args.sources)
    all_samples = []
    for src in sources:
        if src in DATASET_REGISTRY:
            print(f"[build] loading {src} (limit {args.limit_per_source}) ...", flush=True)
            try:
                samples = DATASET_REGISTRY[src](args.limit_per_source)
            except Exception as e:
                print(f"[build] WARN: source {src} failed ({e}); skipping", flush=True)
                continue
        elif src == "custom" or os.path.exists(src):
            path = args.custom_path if src == "custom" else src
            if not path:
                print(f"[build] WARN: source 'custom' needs --custom-path; skipping", flush=True)
                continue
            print(f"[build] loading custom {path} (limit {args.limit_per_source}) ...", flush=True)
            samples = custom_jsonl_adapter(path, args.limit_per_source)
        else:
            print(f"[build] WARN: unknown source '{src}'; known: "
                  f"{','.join(list_sources())} or a .jsonl path; skipping", flush=True)
            continue
        print(f"[build]   {src}: {len(samples)} samples", flush=True)
        all_samples.extend(samples)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    write_jsonl(all_samples, args.out)
    # domain breakdown
    from collections import Counter
    domains = Counter(s["domain"] for s in all_samples)
    print(f"[build] wrote {len(all_samples)} samples to {args.out}", flush=True)
    print(f"[build] domains: {dict(domains)}", flush=True)


if __name__ == "__main__":
    main()
