"""Optimize a support-routing prompt with 120 evaluations and usage accounting.

Run from the checkout with uv; see examples/README.md. No credentials or model
responses are written to the repository. Dollar amounts are usage-based estimates.
"""

from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import sys
import threading
from pathlib import Path
from tempfile import mkdtemp

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "plugins/fleet-optimize/skills/gepa-omni-skill/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "packages/gepa-omni/src"))

from codex_agent_proposer import CodexAgentProposer  # noqa: E402
from gepa_omni import OmniConfig, optimize  # noqa: E402
from gepa_omni.security import resolve_endpoint  # noqa: E402
from gepa_omni.transport import pinned_urlopen  # noqa: E402
from native_omni.chat_completions import OpenAIChatCompletionsClient  # noqa: E402
from native_omni.runners import OpenAIChatCompletionRunner  # noqa: E402

SEED = "Classify this customer support ticket."
LABELS = {"billing", "technical", "account"}
TRAIN = [
    ("I was charged twice for my subscription.", "billing"),
    ("Please send me an invoice for last month.", "billing"),
    ("The application crashes when I upload a file.", "technical"),
    ("The API returns a server error.", "technical"),
    ("I forgot my password.", "account"),
    ("How can I change my login email?", "account"),
]
VALIDATION = [
    ("My refund has not arrived.", "billing"),
    ("The price on my invoice is incorrect.", "billing"),
    ("The dashboard stays blank after loading.", "technical"),
    ("Exporting a report produces a corrupted file.", "technical"),
    ("I cannot sign in after enabling two-factor authentication.", "account"),
    ("Please help me recover my locked profile.", "account"),
]
TEST = [
    ("My card was billed after I cancelled.", "billing"),
    ("Where can I download a receipt?", "billing"),
    ("Search returns an error for every query.", "technical"),
    ("The mobile app freezes when I open settings.", "technical"),
    ("I need to reset my authentication method.", "account"),
    ("How do I update my profile's email address?", "account"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--dataset", type=Path, help="JSON with seed, objective, train, validation, test")
    parser.add_argument("--report-concurrency", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-evals", type=int, default=120)
    parser.add_argument("--input-per-million", type=float, required=True)
    parser.add_argument("--cached-input-per-million", type=float, required=True)
    parser.add_argument("--output-per-million", type=float, required=True)
    args = parser.parse_args()
    benchmark = json.loads(args.dataset.read_text()) if args.dataset else {}
    seed = benchmark.get("seed", SEED)
    train = benchmark.get("train", TRAIN)
    validation = benchmark.get("validation", VALIDATION)
    test = benchmark.get("test", TEST)
    objective = benchmark.get(
        "objective",
        (
            "Route support tickets to exactly one lowercase label: billing (charges, invoices, receipts, "
            "refunds), technical (malfunctioning software), or account (identity, login, profile access). "
            "Return no punctuation or explanation."
        ),
    )
    if args.report_concurrency < 1:
        parser.error("--report-concurrency must be positive")
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    endpoint = resolve_endpoint()
    output = (args.output or Path(mkdtemp(prefix="support-prompt-"))).expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("--output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmark.json").write_text(
        json.dumps(
            {"seed": seed, "objective": objective, "train": train, "validation": validation, "test": test}, indent=2
        )
        + "\n"
    )
    opener = pinned_urlopen(endpoint)
    phase = "baseline_validation"
    usage_rows = []
    lock = threading.Lock()

    def metered_opener(kind):
        def open_request(request, timeout=120):
            with opener(request, timeout=timeout) as response:
                raw = response.read()
            payload = json.loads(raw)
            usage = payload.get("usage", {})
            row = {"phase": phase, "kind": kind, "usage": usage, "model": payload.get("model")}
            with lock:
                usage_rows.append(row)
                (output / "usage.json").write_text(json.dumps(usage_rows, indent=2) + "\n")
            return io.BytesIO(raw)

        return open_request

    client = OpenAIChatCompletionsClient(opener=metered_opener("evaluation"), timeout_seconds=120)
    evaluations = []

    def evaluate(prompt, example):
        ticket, expected = example
        answer = (
            client.complete(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": ticket},
                ]
            )
            .final_text.strip()
            .lower()
        )
        score = float(answer == expected and answer in LABELS)
        feedback = {
            "ticket": ticket,
            "expected": expected,
            "actual": answer,
            "error": None if score else "Return exactly the correct lowercase label, with no explanation.",
        }
        with lock:
            evaluations.append({"phase": phase, "score": score, **feedback})
            (output / "evaluations.json").write_text(json.dumps(evaluations, indent=2) + "\n")
        print(f"evaluation {len(evaluations)} [{phase}]: {score:g}", flush=True)
        return score, feedback

    def accuracy(prompt, examples):
        with ThreadPoolExecutor(max_workers=args.report_concurrency) as pool:
            scores = list(pool.map(lambda example: evaluate(prompt, example)[0], examples))
        return sum(scores) / len(scores)

    def proposer_factory(**kwargs):
        return CodexAgentProposer(**kwargs, opener=metered_opener("proposal"))

    def runner_factory(backend, model, timeout_seconds, input_cost_per_million, output_cost_per_million):
        return OpenAIChatCompletionRunner(
            backend=backend,
            model=model,
            timeout_seconds=timeout_seconds,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            opener=metered_opener("proposal"),
        )

    print(f"Model: {endpoint.model}; optimizer budget: {args.max_evals}; artifacts: {output}", flush=True)
    baseline_validation = accuracy(seed, validation)
    phase = "optimization"
    result = optimize(
        seed_candidate=seed,
        evaluator=evaluate,
        dataset=train,
        valset=validation,
        objective=objective,
        config=OmniConfig(
            strategy="auto",
            max_evals=args.max_evals,
            max_concurrency=1,
            model=endpoint.model,
            run_dir=str(output / "runs"),
            output_dir=str(output / "outputs"),
        ),
        codex_proposer_factory=proposer_factory,
        native_runner_factory=runner_factory,
        codex_timeout_seconds=120,
    )
    (output / "best-prompt.txt").write_text(result.best_candidate + "\n")
    phase = "optimized_validation"
    optimized_validation = accuracy(result.best_candidate, validation)
    # Both test measurements happen after search; neither influences selection.
    phase = "baseline_test"
    baseline_test = accuracy(seed, test)
    phase = "optimized_test"
    optimized_test = accuracy(result.best_candidate, test)
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "estimated_usd": 0.0}
    for row in usage_rows:
        usage = row["usage"]
        inputs = usage.get("prompt_tokens", 0)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        outputs = usage.get("completion_tokens", 0)
        cost = (
            (inputs - cached) * args.input_per_million
            + cached * args.cached_input_per_million
            + outputs * args.output_per_million
        ) / 1_000_000
        row["estimated_usd"] = cost
        totals["input_tokens"] += inputs
        totals["cached_input_tokens"] += cached
        totals["output_tokens"] += outputs
        totals["estimated_usd"] += cost
    report = {
        "benchmark": benchmark.get("name", "support-routing-demo"),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest() if args.dataset else None,
        "split_sizes": {"train": len(train), "validation": len(validation), "test": len(test)},
        "model": endpoint.model,
        "max_evals": args.max_evals,
        "baseline_validation_accuracy": baseline_validation,
        "optimized_validation_accuracy": optimized_validation,
        "baseline_test_accuracy": baseline_test,
        "optimized_test_accuracy": optimized_test,
        "optimizer_selection_score": result.best_score,
        "optimizer_reported_evaluations": result.total_evals,
        "observed_optimization_evaluations": sum(row["phase"] == "optimization" for row in evaluations),
        "total_evaluator_calls": len(evaluations),
        "total_model_calls": len(usage_rows),
        "usage_and_estimated_cost": totals,
        "pricing_per_million": {
            "input": args.input_per_million,
            "cached_input": args.cached_input_per_million,
            "output": args.output_per_million,
        },
        "cost_by_kind": {
            kind: sum(row["estimated_usd"] for row in usage_rows if row["kind"] == kind)
            for kind in ("evaluation", "proposal")
        },
        "auto": result.metadata.get("auto"),
    }
    (output / "usage.json").write_text(json.dumps(usage_rows, indent=2) + "\n")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print("BEST PROMPT:\n" + result.best_candidate, flush=True)


if __name__ == "__main__":
    main()
