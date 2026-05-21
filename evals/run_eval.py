"""Labeled-pair eval for fit-score accuracy.

What this is:
- `labels.jsonl` is the ground truth: 24 districts hand-scored 0-100 against
  the brief's 5-criterion rubric (enrollment, intent, decision-maker,
  pain, public traction). Each label carries its reasoning so a panel
  can challenge any individual score.
- This script runs enrichment on each labeled district (skips if already
  cached in the DB), then compares the model's `fit_score` to the human
  label. Reports: Spearman + Pearson correlation, MAE, tier-bucket
  accuracy, and a per-district table.

Run:
    uv run python -m evals.run_eval                  # against running backend
    uv run python -m evals.run_eval --no-run         # use cached enrichments only
    uv run python -m evals.run_eval --base http://127.0.0.1:8000

The output is what you quote in the interview. "Spearman 0.X across 24
labeled districts, MAE Y points, tier accuracy Z%."
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx


LABELS_PATH = Path(__file__).parent / "labels.jsonl"
REPORT_PATH = Path(__file__).parent / "last_run_report.md"


# ─── Stats helpers (no scipy dep) ──────────────────────────────────

def _rank(values: List[float]) -> List[float]:
    """Average-rank for Spearman. Ties get the mean of their tied ranks."""
    indexed = sorted(enumerate(values), key=lambda p: p[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def pearson(x: List[float], y: List[float]) -> float:
    n = len(x)
    if n < 2:
        return float("nan")
    mx, my = statistics.fmean(x), statistics.fmean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x) ** 0.5
    dy = sum((b - my) ** 2 for b in y) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def spearman(x: List[float], y: List[float]) -> float:
    return pearson(_rank(x), _rank(y))


def mae(x: List[float], y: List[float]) -> float:
    return statistics.fmean(abs(a - b) for a, b in zip(x, y))


def rmse(x: List[float], y: List[float]) -> float:
    return (statistics.fmean((a - b) ** 2 for a, b in zip(x, y))) ** 0.5


def tier_of(score: float) -> str:
    if score >= 75:
        return "strong"
    if score >= 50:
        return "moderate"
    return "weak"


# ─── Pipeline driver ───────────────────────────────────────────────

def _load_labels() -> List[Dict[str, Any]]:
    labels = []
    for line in LABELS_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            labels.append(json.loads(line))
    return labels


def _get_district(client: httpx.Client, did: str) -> Optional[Dict[str, Any]]:
    r = client.get(f"/api/districts/{did}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _run_pipeline(client: httpx.Client, did: str) -> Dict[str, Any]:
    r = client.post(f"/api/districts/{did}/run-pipeline", timeout=60.0)
    r.raise_for_status()
    return r.json()


def _predicted_fit(district_payload: Dict[str, Any]) -> Optional[int]:
    e = district_payload.get("enrichment")
    if not e:
        return None
    return e.get("fit_score")


def evaluate(base_url: str, run_missing: bool, verbose: bool = True) -> Dict[str, Any]:
    labels = _load_labels()
    rows: List[Dict[str, Any]] = []

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        for lbl in labels:
            did = lbl["district_id"]
            d = _get_district(client, did)
            if d is None:
                if verbose:
                    print(f"  [skip] {did}: not in DB", file=sys.stderr)
                continue

            status = d.get("status")
            predicted = _predicted_fit(d)

            if predicted is None and run_missing and status not in ("duplicate", "non_fit", "rejected"):
                if verbose:
                    print(f"  [run]  {did}: running pipeline...", file=sys.stderr)
                t0 = time.time()
                try:
                    _run_pipeline(client, did)
                except httpx.HTTPError as e:
                    if verbose:
                        print(f"         pipeline failed: {e}", file=sys.stderr)
                    continue
                d = _get_district(client, did)
                predicted = _predicted_fit(d) if d else None
                if verbose:
                    print(f"         done in {time.time() - t0:.1f}s, predicted={predicted}", file=sys.stderr)

            rows.append({
                "district_id": did,
                "name": d.get("name") if d else None,
                "status": status,
                "labeled": lbl["fit_label"],
                "labeled_tier": lbl["tier"],
                "predicted": predicted,
                "predicted_tier": tier_of(predicted) if predicted is not None else None,
                "reasoning": lbl["reasoning"],
            })

    return _summarize(rows)


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    paired = [r for r in rows if r["predicted"] is not None]
    if not paired:
        return {"rows": rows, "paired": 0, "warning": "no paired predictions to score"}

    x = [float(r["labeled"]) for r in paired]
    y = [float(r["predicted"]) for r in paired]

    tier_correct = sum(1 for r in paired if r["labeled_tier"] == r["predicted_tier"])

    confusion: Dict[str, Dict[str, int]] = {t: {"strong": 0, "moderate": 0, "weak": 0} for t in ("strong", "moderate", "weak")}
    for r in paired:
        confusion[r["labeled_tier"]][r["predicted_tier"]] += 1

    return {
        "rows": rows,
        "paired": len(paired),
        "unpaired": len(rows) - len(paired),
        "spearman": round(spearman(x, y), 3),
        "pearson": round(pearson(x, y), 3),
        "mae": round(mae(x, y), 2),
        "rmse": round(rmse(x, y), 2),
        "tier_accuracy": round(tier_correct / len(paired), 3),
        "tier_confusion": confusion,
    }


# ─── Reporting ─────────────────────────────────────────────────────

def _format_report(summary: Dict[str, Any]) -> str:
    if summary["paired"] == 0:
        return f"# Eval report\n\n**No paired predictions.** {summary.get('warning', '')}\n"

    out = ["# Fit-score eval report", ""]
    out.append(f"- Paired predictions: **{summary['paired']}** (unpaired: {summary['unpaired']})")
    out.append(f"- **Spearman ρ:** {summary['spearman']}  *(rank agreement — does the model order districts like a human?)*")
    out.append(f"- **Pearson r:** {summary['pearson']}  *(linear agreement on raw score values)*")
    out.append(f"- **MAE:** {summary['mae']} points  *(mean absolute error on 0-100 scale)*")
    out.append(f"- **RMSE:** {summary['rmse']} points")
    out.append(f"- **Tier accuracy:** {summary['tier_accuracy']:.1%}  *(strong/moderate/weak bucket match)*")
    out.append("")
    out.append("## Tier confusion")
    out.append("")
    out.append("|              | pred strong | pred moderate | pred weak |")
    out.append("|--------------|:-----------:|:-------------:|:---------:|")
    for tier in ("strong", "moderate", "weak"):
        c = summary["tier_confusion"][tier]
        out.append(f"| label {tier:8} | {c['strong']:>11} | {c['moderate']:>13} | {c['weak']:>9} |")
    out.append("")
    out.append("## Per-district")
    out.append("")
    out.append("| District | Name | Labeled | Predicted | Δ | Match? |")
    out.append("|---|---|---:|---:|---:|:---:|")
    for r in sorted(summary["rows"], key=lambda r: -(r["predicted"] or -1)):
        if r["predicted"] is None:
            out.append(f"| {r['district_id']} | {(r['name'] or '?')[:36]} | {r['labeled']} | — | — | skip |")
            continue
        delta = r["predicted"] - r["labeled"]
        match = "✓" if r["labeled_tier"] == r["predicted_tier"] else "✗"
        out.append(f"| {r['district_id']} | {(r['name'] or '?')[:36]} | {r['labeled']} | {r['predicted']} | {delta:+d} | {match} |")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--no-run", action="store_true", help="Skip running pipeline; only score cached enrichments")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-district progress")
    args = parser.parse_args()

    summary = evaluate(args.base, run_missing=not args.no_run, verbose=not args.quiet)
    report = _format_report(summary)
    REPORT_PATH.write_text(report)
    print(report)
    print(f"(report written to {REPORT_PATH})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
