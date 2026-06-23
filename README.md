# OpenFugu

**An open, runnable reverse-engineering of Sakana AI's Fugu — the "one model to
command them all" LLM orchestrator.**

Fugu is sold as a single model; it is really a *policy over models* — a tiny
coordinator that, per query, routes work to a pool of frontier LLMs and returns
one answer. Sakana's product and trained weights are closed. OpenFugu rebuilds
the mechanism from the two papers + released artifacts, verifies it against real
weights, trains a Conductor of our own, and serves it behind one OpenAI-compatible
endpoint. Four stages, all working: **read → run → train → serve.**

> Independent reimplementation. Not affiliated with Sakana AI. No third-party
> code/weights are redistributed here — `scripts/fetch_artifacts.py` pulls them
> from their licensed sources. See `NOTICE`.

## What's inside

| Stage | What | Evidence |
|-------|------|----------|
| **read** | `docs/HOW_FUGU_IS_IMPLEMENTED.md` — full math; `docs/ARCHITECTURE.md` — investigation log, evidence-graded | reverse-engineered from papers + author code |
| **run** | `openfugu/mini.py` (TRINITY: hidden-state → linear head → worker); `openfugu/ultra.py` (Conductor: workflow-DAG) | `mini.py --self-test` = **95% agent / 100% role** on the 37-case fixture, real weights |
| **train** | `train/train_trinity.py` — self-train the **TRINITY** coordinator from scratch via sep-CMA-ES (no Sakana weights); `train/train_conductor.py` — GRPO a **Conductor** on `nvidia/ToolScale` | TRINITY: chance→optimal routing in ~5 generations (mock, runs anywhere); Conductor: reward **1.21 → 1.64** over 100 steps ([curve](results/)) |
| **serve** | `openfugu/serve.py` — one OpenAI-compatible `/v1/chat/completions`; internal TRINITY loop over a litellm pool | `curl` returns one answer; pool hidden |
| **eval** | `eval/eval_orchestration.py` — does **per-question** routing beat the best single model? | trained router **+107%** over best single worker (query-level routing, **not** per-step coordination — see [results caveat](results/)) |

## Quickstart

```bash
pip install -r requirements.txt           # torch, transformers, trl, litellm, ...
python scripts/fetch_artifacts.py         # pull Qwen3-0.6B + model_iter_60.npy + fixture (not redistributed)

export FUGU_MODEL=$(...Qwen3-0.6B path...)
export FUGU_VECTOR=$PWD/artifacts/model_iter_60.npy
export FUGU_FIXTURE=$PWD/artifacts/qwen_router_prompt_eval_cases.json

# READ:  the architecture, evidence-graded
less docs/HOW_FUGU_IS_IMPLEMENTED.md

# RUN:   prove the reconstruction is faithful to the checkpoint
python openfugu/mini.py --self-test       # -> 95% / 100%

# RUN:   route one query (offline mock pool)
python openfugu/mini.py --demo

# RUN (live): real worker pool via litellm
export FUGU_API_KEY=...  FUGU_BASE_URL=...
python openfugu/mini.py --demo --live \
  --slot-models "novita/deepseek/deepseek-v4-flash,novita/zai-org/glm-5,..."

# TRAIN: a Conductor on ToolScale (8x A800-class; HF generation, no vLLM)
python train/train_conductor.py           # reward climbs off zero; saves checkpoint

# TRAIN: Fugu-Ultra recursive topology — Conductor revises its own output (test-time scaling)
python train/train_recursion.py           # mock: +9% over one-shot (toy policy w/ headroom)
python train/train_recursion_real.py      # REAL recursion (round-0 fed back into round-1)
python eval/eval_recursion_real.py        # honest held-out: round-0 vs round-1 → TIE (see results/)

# TRAIN: adaptive k-of-n pool — generalize to arbitrary worker subsets (swap the pool)
python train/train_adaptive_pool.py            # mock: +44% over blind, 94% of oracle
python train/train_adaptive_pool_perstep.py    # REAL per-step: random k-of-n subset masked each turn,
                                               # base 0.625 -> 1.000 (n=8, overfit caveat), PASS

# TRAIN: self-train the TRINITY coordinator from scratch (sep-CMA-ES, mock — no GPU/API)
python train/train_trinity.py             # chance -> optimal routing; PASS in seconds

# SERVE: Fugu as one model (API worker pool via litellm)
python openfugu/serve.py --slot-models "<csv>" --port 8088
curl localhost:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"flatten a nested list in one line"}]}'

# SERVE (real end-to-end): TRAINED per-step head + REAL local worker pool, no API
python openfugu/serve.py --model <qwen3-0.6b dir> --vector model_iter_60.npy \
  --head trinity_perstep.npy --local-models "<llama dir>,<gemma dir>" --port 8088
# prove it end-to-end (boots the server, POSTs a real GSM8K question, checks the answer):
python eval/serve_e2e.py --model <qwen3-0.6b dir> --vector model_iter_60.npy \
  --head trinity_perstep.npy --local-models "<llama dir>,<gemma dir>"   # -> answer 72, PASS

# PIPELINE: train -> serve -> verify in ONE command (the head served is the head just trained)
python pipeline/e2e_train_serve.py --model <qwen3-0.6b dir> --vector model_iter_60.npy \
  --local-models "<llama dir>,<gemma dir>" --port 8097      # trains a fresh head, serves it, PASS
# serve+verify only, reusing an existing head:
python pipeline/e2e_train_serve.py --skip-train --head trinity_perstep.npy \
  --model <qwen3-0.6b dir> --local-models "<llama dir>,<gemma dir>"

# SERVE (Fugu-Ultra): the Conductor workflow-DAG executor, fully local (no API)
python openfugu/ultra.py --query "..." --local-conductor <conductor dir> \
  --local-models "<llama dir>,<deepseek dir>"          # local Conductor emits + executes a DAG
python eval/ultra_e2e.py --conductor-ckpt <conductor dir> \
  --local-models "<llama dir>,<deepseek dir>"          # asserts a parsed, executed workflow

# EVAL: does orchestration beat the best single model? (the central Fugu claim)
python eval/eval_orchestration.py        # trained coordinator +107% over best single, PASS
```

## Product mode: openfugu-flash / openfugu-pro

On top of the TRINITY mechanism, OpenFugu ships a minimal **dual-model product
prototype**: two externally-visible "models" backed by a shared cloud worker
pool.

| Model | Routing | When |
|-------|---------|------|
| **openfugu-flash** | per-question — one routing decision, one worker answers fully | fast, cheap |
| **openfugu-pro** | per-step — each turn re-routes (Worker/Thinker/Verifier) until ACCEPT or max_turns | slower, finer-grained |

Both are served behind one OpenAI-compatible endpoint. Credentials come from the
environment via litellm — **never** from config or code.

### Configure

`configs/fugu.yaml` describes the two models and the worker pool. Edit it to
pick which cloud models participate, their tags, max_tokens, temperature, and
enabled flag. The same pool can be shared or split between flash and pro.

```yaml
models:
  openfugu-flash:
    mode: per_question
    workers: [gpt_cheap, deepseek_fast, gemini_flash]
  openfugu-pro:
    mode: per_step
    workers: [gpt_strong, claude_strong, gemini_pro, deepseek_reasoner]
    max_turns: 5
workers:
  gpt_cheap:
    provider_model: openai/gpt-4o-mini
    enabled: true
    tags: [cheap, fast]
    max_tokens: 1024
    temperature: 0.2
```

### Serve

```bash
# offline (mock pool, no API key — for trying the surface)
python openfugu/product_serve.py --port 8090

# live (real cloud workers via litellm)
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... GEMINI_API_KEY=... \
  python openfugu/product_serve.py --port 8090

# with a trained flash router head (Qwen3-0.6B features)
FUGU_MODEL=<Qwen3-0.6B dir> FUGU_VECTOR=model_iter_60.npy \
  python openfugu/product_serve.py --flash-head flash_head.npy --port 8090
```

```bash
curl localhost:8090/v1/models   # -> openfugu-flash, openfugu-pro
curl localhost:8090/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"openfugu-flash","messages":[{"role":"user","content":"..."}]}'
```

`usage` returns `fugu_mode` (per_question|per_step) and `fugu_turns`. Pass
`"debug":true` in the request body to expose the selected worker / route trace
(hidden by default so the pool is not leaked).

### Build verifiable training data (breadth-first)

The router learns from **verifiable** samples across domains — not just GSM8K.
Unified schema: `{id, domain, prompt, gold, evaluator, weight, metadata}`.
Evaluators: `numeric`, `exact`, `choice`, `regex`, `json_fields`, `tool_calls`,
`python_tests`.

```bash
# union public sources (needs `datasets` + network)
python train/build_verifiable_dataset.py \
  --sources gsm8k,toolscale,mmlu,humaneval \
  --out data/router_train.jsonl --limit-per-source 1000

# your own commercial data (most important!) — a .jsonl in the unified schema
python train/build_verifiable_dataset.py \
  --sources data/my_tasks.jsonl --out data/router_train.jsonl
```

### Profile workers (cache once, train free)

Profile every worker on every sample ONCE. The cache lets CMA-ES train the
router with zero new API calls. Resumable — interrupted runs can restart.

```bash
python train/profile_workers.py --config configs/fugu.yaml \
  --dataset data/router_train.jsonl --out data/worker_profile.jsonl \
  --model openfugu-flash
```

### Train the flash router

```bash
python train/train_flash_router.py \
  --model <Qwen3-0.6B dir> --dataset data/router_train.jsonl \
  --profile data/worker_profile.jsonl --out flash_head.npy --iters 20
# pipeline test without Qwen3-0.6B:
python train/train_flash_router.py --no-backbone \
  --dataset data/router_train.jsonl --profile data/worker_profile.jsonl --out flash_head.npy
```

### Train the pro router

Per-step training runs multi-turn rollouts, so it is expensive on cloud. Use
`--fake` for the pipeline flow; scale up deliberately.

```bash
# offline pipeline test
python train/train_pro_router.py --fake --dataset data/router_train.jsonl --out pro_head.npy
# cloud (expensive — each fitness eval is several multi-turn generations)
FUGU_MODEL=<Qwen3-0.6B dir> FUGU_VECTOR=model_iter_60.npy \
  python train/train_pro_router.py --dataset data/router_train.jsonl --out pro_head.npy
```

### Evaluate

```bash
python eval/eval_router_flash.py --dataset data/router_eval.jsonl \
  --profile data/worker_profile_eval.jsonl --head flash_head.npy --held-out
python eval/eval_router_pro.py --fake --dataset data/router_eval.jsonl \
  --head pro_head.npy --max-turns 4 --held-out
```

Both report best-single / router / oracle scores, lift %, per-domain breakdown,
routing distribution, sample count, held-out flag, and small-sample/overfit
warnings. They do not just print PASS.

### Offline tests

```bash
python tests/test_offline.py   # no API key needed
```

**Honest note on training effectiveness.** Router value depends on data quality
and **worker complementarity** — it is not "more data is better." If all workers
are similar or the task is too easy for any single one, there is no routing
headroom (see [results caveat](results/)). The framework is trainable,
evaluable, and extensible; it does not promise commercial results.

## The mechanism in one breath

A ~0.6B backbone (Qwen3-0.6B) never answers the user. It produces one hidden
state at the penultimate token; a **bias-free linear head** scores each worker;
the top worker is dispatched and *its* reply is returned. ~19.5K trainable
numbers (the head + singular-value-fine-tuning offsets on 9 matrices), optimized
gradient-free (sep-CMA-ES). **Fugu-Ultra** swaps the per-turn picker for a 7B
Conductor that emits a whole workflow DAG. No worker weights are ever touched —
it is macro-level composition over other people's models. Full math, with an
EXEC/CODE/DATA evidence grade on every claim, in `docs/`.

## Trained Conductor weights

The Conductor we trained on ToolScale (a fine-tune of Llama-3.2-3B-Instruct) is
published on HuggingFace, **not** in this repo (Llama 3.2 Community License
applies — see `NOTICE`):

    huggingface.co/di-zhang-fdu/openfugu-conductor-3b   (see model card)

## License

Apache-2.0 for all OpenFugu code (`LICENSE`). Third-party material is fetched,
not redistributed; trained weights carry the Llama 3.2 license. See `NOTICE`.
