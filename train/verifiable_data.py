#!/usr/bin/env python3
# OpenFugu — Apache-2.0. Unified verifiable training-data system for the router.
# Reuses train/toolscale_data.py's _parse_plan/_score for tool_calls eval.
"""
verifiable_data.py — unified, verifiable training samples for the router.

The goal is breadth-first coverage (math, code, tool, qa, choice, json, ...) so
the router learns real complementary worker strengths, not one narrow domain.

Unified sample schema (one JSON object per line in a .jsonl):

  {
    "id": "...",                         # stable id
    "domain": "math|code|tool|qa|choice|json|...",
    "prompt": "...",                     # the question/task text
    "gold": ...,                         # evaluator-specific ground truth
    "evaluator": "numeric|exact|choice|regex|json_fields|tool_calls|python_tests",
    "weight": 1.0,
    "metadata": {}
  }

Evaluators all return a float in [0,1]. This module is import-safe (no network,
no datasets import at module top) so tests run offline.
"""
from __future__ import annotations
import json, re, sys, os

# ---- evaluators ------------------------------------------------------------
def _norm(s: str) -> str:
    return str(s).strip().lower().replace(",", "").rstrip(".")


def eval_numeric(output: str, gold) -> float:
    """Last number in the output vs gold (a number string)."""
    nums = re.findall(r"-?\d[\d,]*\.?\d*", (output or "").replace(",", ""))
    if not nums:
        return 0.0
    return 1.0 if _norm(nums[-1]) == _norm(gold) else 0.0


def eval_exact(output: str, gold) -> float:
    return 1.0 if _norm(output) == _norm(gold) else 0.0


def eval_choice(output: str, gold) -> float:
    """gold is the option letter, e.g. 'B'. Match a leading A/B/C/D or the letter."""
    g = str(gold).strip().upper()
    if not g:
        return 0.0
    o = (output or "").strip().upper()
    # 'answer: b', 'b)', 'b' all accepted
    m = re.search(r"\b([A-D])\b", o)
    pred = m.group(1) if m else (o[:1] if o else "")
    return 1.0 if pred == g else 0.0


def eval_regex(output: str, gold) -> float:
    """gold is a regex pattern; 1.0 if it matches anywhere in output."""
    try:
        return 1.0 if re.search(str(gold), output or "") else 0.0
    except re.error:
        return 0.0


def eval_json_fields(output: str, gold) -> float:
    """gold is {field: expected_value}; fraction of fields present and equal."""
    try:
        obj = json.loads(_extract_json(output))
    except Exception:
        return 0.0
    gold = gold if isinstance(gold, dict) else json.loads(gold)
    if not gold:
        return 0.0
    hit = sum(1 for k, v in gold.items()
              if k in obj and _norm(obj[k]) == _norm(v))
    return hit / len(gold)


_ANSWER_RE = re.compile(r"<answer>\s*([\s\S]*?)\s*</answer>", re.I)


def _parse_plan(completion: str) -> list[dict] | None:
    """Mirror of toolscale_data._parse_plan — extract the JSON list from an
    <answer>...</answer> block. Works offline (no datasets import)."""
    m = _ANSWER_RE.search(completion)
    if not m:
        return None
    body = m.group(1).strip()
    try:
        data = json.loads(body)
    except Exception:
        try:
            data = json.loads(re.sub(r"'", '"', body))
        except Exception:
            return None
    if not isinstance(data, list):
        return None
    norm = []
    for it in data:
        if isinstance(it, dict) and "name" in it:
            norm.append({"name": it.get("name"), "arguments": it.get("arguments") or {}})
    return norm


def _score_plan(pred: list[dict], gold: list[dict]) -> float:
    """Mirror of toolscale_data._score — name set/order (0.7) + args (0.3)."""
    if not gold:
        return 0.0
    gold_names = [g["name"] for g in gold]
    pred_names = [p["name"] for p in pred]
    name_hit = sum(1 for n in gold_names if n in pred_names) / len(gold_names)
    order = 0
    for a, b in zip(pred_names, gold_names):
        if a == b:
            order += 1
        else:
            break
    order_frac = order / len(gold_names)
    name_score = 0.6 * name_hit + 0.4 * order_frac
    arg_scores, used = [], set()
    for g in gold:
        for i, p in enumerate(pred):
            if i in used or p["name"] != g["name"]:
                continue
            used.add(i)
            ga, pa = g["arguments"], p["arguments"]
            if not ga:
                arg_scores.append(1.0)
            else:
                hit = sum(1 for k, v in ga.items() if str(pa.get(k)) == str(v))
                arg_scores.append(hit / len(ga))
            break
    arg_score = sum(arg_scores) / len(gold) if gold else 0.0
    return 0.7 * name_score + 0.3 * arg_score


def eval_tool_calls(output: str, gold) -> float:
    """Score a tool-call plan vs gold. Reuses the _parse_plan/_score_plan
    mirrors above (identical to toolscale_data.py) so this evaluator runs
    offline without triggering toolscale_data's top-level datasets import."""
    gold = gold if isinstance(gold, list) else json.loads(gold)
    pred = _parse_plan(output or "")
    if pred is None:
        return 0.0
    return _score_plan(pred, gold)


def eval_python_tests(output: str, gold) -> float:
    """MVP stub: do NOT execute untrusted code. gold may be a dict of
    {tests: [...], expected_pass: n}. Without a sandbox we only check that the
    output contains the expected function signature markers. A real
    implementation must run in a sandbox/timeout."""
    g = gold if isinstance(gold, dict) else {}
    markers = g.get("markers") or []
    if not markers:
        return 0.0
    hit = sum(1 for mk in markers if mk in (output or ""))
    return hit / len(markers)


EVALUATORS = {
    "numeric": eval_numeric,
    "exact": eval_exact,
    "choice": eval_choice,
    "regex": eval_regex,
    "json_fields": eval_json_fields,
    "tool_calls": eval_tool_calls,
    "python_tests": eval_python_tests,
}


def score_sample(sample: dict, output: str) -> float:
    """Dispatch the sample's evaluator. Returns 0.0 on unknown evaluator."""
    fn = EVALUATORS.get(sample.get("evaluator"))
    if fn is None:
        return 0.0
    try:
        return float(fn(output, sample.get("gold")))
    except Exception:
        return 0.0


def _extract_json(text: str) -> str:
    """Pull the first balanced {...} or [...] block out of text."""
    t = text or ""
    for i, ch in enumerate(t):
        if ch in "{[":
            depth = 0
            for j in range(i, len(t)):
                if t[j] in "{[":
                    depth += 1
                elif t[j] in "}]":
                    depth -= 1
                    if depth == 0:
                        return t[i:j + 1]
    return t


# ---- sample helpers --------------------------------------------------------
def make_sample(id, domain, prompt, gold, evaluator, weight=1.0, **metadata) -> dict:
    return {"id": id, "domain": domain, "prompt": prompt, "gold": gold,
            "evaluator": evaluator, "weight": float(weight), "metadata": metadata}


def write_jsonl(samples, path: str):
    with open(path, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---- dataset registry (breadth-first) --------------------------------------
# Each adapter is a generator(limit) -> list[sample]. Adapters that need network
# datasets import lazily so the module is import-safe offline. Adapters that
# can't run (no datasets lib / no data) raise or return [] and the builder skips.

def gsm8k_adapter(limit: int = 1000) -> list[dict]:
    """openai/gsm8k — math, numeric answer after '####'."""
    from datasets import load_dataset
    ds = load_dataset("openai/gsm8k", "main", split=f"train[:{limit}]")
    out = []
    for i, r in enumerate(ds):
        gold = r["answer"].split("####")[-1].strip().replace(",", "")
        out.append(make_sample(f"gsm8k-{i}", "math", r["question"], gold, "numeric"))
    return out


def toolscale_adapter(limit: int = 1000) -> list[dict]:
    """nvidia/ToolScale — tool-call sequence, reuses toolscale_data reward."""
    from datasets import load_dataset
    from toolscale_data import _expected_actions
    ds = load_dataset("nvidia/ToolScale", split="train")
    ds = ds.shuffle(seed=42).select(range(min(limit, len(ds))))
    out = []
    for i, r in enumerate(ds):
        gold = _expected_actions(r.get("evaluation_criteria"))
        if not gold:
            continue
        us = r.get("user_scenario") or {}
        instr = (us.get("instructions") or {})
        task = instr.get("task_instructions") or instr.get("reason_for_call") or ""
        tools = sorted({a["name"] for a in gold if a["name"]})
        prompt = ("Available tools: " + ", ".join(tools) + "\n\n" +
                  "USER QUESTION: " + task).strip()
        out.append(make_sample(f"toolscale-{i}", "tool", prompt, gold, "tool_calls"))
    return out


def mmlu_choice_adapter(limit: int = 1000) -> list[dict]:
    """cais/mmlu — multiple choice. gold = the answer letter."""
    from datasets import load_dataset
    ds = load_dataset("cais/mmlu", "all", split=f"test[:{limit}]")
    letters = ["A", "B", "C", "D"]
    out = []
    for i, r in enumerate(ds):
        choices = r.get("choices") or []
        opts = "\n".join(f"{letters[j]}. {c}" for j, c in enumerate(choices))
        prompt = f"{r['question']}\n{opts}\nAnswer with the letter."
        out.append(make_sample(f"mmlu-{i}", "choice", prompt,
                               letters[r["answer"]], "choice"))
    return out


def humaneval_adapter(limit: int = 1000) -> list[dict]:
    """openai/openai_humaneval — code. MVP: skeleton, no execution."""
    from datasets import load_dataset
    ds = load_dataset("openai/openai_humaneval", split=f"test[:{limit}]")
    out = []
    for i, r in enumerate(ds):
        # gold = markers we expect a correct solution to contain (signature).
        gold = {"markers": [r["entry_point"] + "("]}
        out.append(make_sample(f"humaneval-{i}", "code", r["prompt"],
                               gold, "python_tests",
                               metadata={"task_id": r.get("task_id", "")}))
    return out


def custom_jsonl_adapter(path: str, limit: int = 1000) -> list[dict]:
    """User's own commercial data in the unified schema. This is the MOST
    important source. Each line is already a sample dict."""
    out = []
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            # normalize missing fields
            s.setdefault("id", f"custom-{i}")
            s.setdefault("domain", "qa")
            s.setdefault("weight", 1.0)
            s.setdefault("metadata", {})
            out.append(s)
            if len(out) >= limit:
                break
    return out


# registry keyed by source name
DATASET_REGISTRY = {
    "gsm8k": gsm8k_adapter,
    "toolscale": toolscale_adapter,
    "mmlu": mmlu_choice_adapter,
    "humaneval": humaneval_adapter,
}


def list_sources() -> list[str]:
    return list(DATASET_REGISTRY.keys())


# ---- offline self-test (no network) ----------------------------------------
def _self_test():
    # numeric
    assert eval_numeric("the total is 1,234.", "1234") == 1.0
    assert eval_numeric("no number here", "5") == 0.0
    # exact
    assert eval_exact("Paris\n", "paris") == 1.0
    assert eval_exact("Paris", "London") == 0.0
    # choice
    assert eval_choice("The answer is B.", "B") == 1.0
    assert eval_choice("C. maybe", "B") == 0.0
    # regex
    assert eval_regex("error code 42 occurred", r"code \d+") == 1.0
    assert eval_regex("nothing", r"\d+") == 0.0
    # json_fields
    out = '{"name": "fugu", "count": 7}'
    assert eval_json_fields(out, {"name": "fugu", "count": "7"}) == 1.0
    assert eval_json_fields(out, {"name": "fugu", "missing": 1}) == 0.5
    # tool_calls
    gold = [{"name": "get_forecast", "arguments": {"id": "76"}}]
    assert eval_tool_calls('<answer>' + json.dumps(gold) + '</answer>', gold) > 0.9
    assert eval_tool_calls('<answer>[]</answer>', gold) == 0.0
    # python_tests (marker-only stub)
    assert eval_python_tests("def add(a, b): return a+b", {"markers": ["add("]}) == 1.0
    print("PASS — verifiable_data evaluators (numeric/exact/choice/regex/json/tool/python)")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
