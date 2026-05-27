"""Labeled-pair eval for email-draft quality.

What this is:
- `email_labels.jsonl` holds the ground truth: ~10 hand-graded email drafts
  scored 0-10 on three dimensions — hook_specificity, tone, cta_clarity —
  with one-line reasoning.
- This script uses Claude as a judge to score the same drafts on the same
  rubric, then compares judge → human per-dimension. Reports correlation,
  MAE, and per-dimension agreement.

Why labeled-pair (not just judge-alone):
- A judge marking its own homework is a sanity check, not an eval. Pairing
  judge scores against human labels measures whether the judge tracks
  human taste — which is what you'd need before trusting it to grade at
  scale.

Run:
    uv run python -m evals.run_email_eval
    uv run python -m evals.run_email_eval --quiet

If labels are not yet filled in (scores are null), the script prints
which entries need labeling and exits cleanly.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic


LABELS_PATH = Path(__file__).parent / "email_labels.jsonl"
REPORT_PATH = Path(__file__).parent / "last_email_run_report.md"

DIMS = ("hook_specificity", "tone", "cta_clarity")
JUDGE_MODEL = os.getenv("EMAIL_JUDGE_MODEL", "claude-sonnet-4-6")

JUDGE_SYSTEM = """You are grading first-touch outbound emails written by an SDR copilot
to K-12 special-education district leaders. Score each draft on three dimensions, 0-10:

- hook_specificity (0-10): Does the opener cite a *specific* signal (event attended,
  whitepaper pulled, RFP number, named conference, dated webinar)? Generic intros
  score low. A specific, dated, named hook scores high.

- tone (0-10): Is the voice direct, peer-to-peer, and free of AI tells (em dashes,
  "delve", "robust", "seamless", "comprehensive", "leverage", "synergy")? Reads
  like a human SDR who knows the space. Stiff, marketing-speak, or AI-slop scores low.

- cta_clarity (0-10): Is there exactly one clear ask, with a specific next step
  (a 15-min call, a reply, a calendar link)? Two CTAs or a vague "let me know what
  you think" scores low.

Be calibrated: 10 = best draft you can imagine for this dim; 5 = competent but
unremarkable; 0 = fails on the dim entirely. Return a JSON object with the three
integer scores and one short sentence of reasoning per dim. No prose outside JSON.
"""


# ─── Stats helpers (vendored to avoid scipy) ──────────────────────────

def _rank(values: List[float]) -> List[float]:
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


# ─── Judge ─────────────────────────────────────────────────────────────

def _judge_one(client: Anthropic, subject: str, body: str) -> Dict[str, Any]:
    msg = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        system=JUDGE_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Subject: {subject}\n\nBody:\n{body}\n\nReturn JSON only.",
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    # Strip code fences if model adds them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


# ─── Eval driver ───────────────────────────────────────────────────────

def _load_labels() -> List[Dict[str, Any]]:
    rows = []
    for line in LABELS_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _is_labeled(row: Dict[str, Any]) -> bool:
    return all(row.get(f"{d}_label") is not None for d in DIMS)


def evaluate(verbose: bool = True) -> Dict[str, Any]:
    rows = _load_labels()
    labeled = [r for r in rows if _is_labeled(r)]
    unlabeled = [r["district_id"] for r in rows if not _is_labeled(r)]

    if not labeled:
        return {"paired": 0, "unlabeled": unlabeled, "rows": []}

    client = Anthropic()
    out_rows = []
    for r in labeled:
        if verbose:
            print(f"  [judge] {r['district_id']}...", file=sys.stderr)
        try:
            j = _judge_one(client, r["draft_subject"], r["draft_body"])
        except Exception as e:
            if verbose:
                print(f"          judge failed: {e}", file=sys.stderr)
            continue
        out_rows.append({
            "district_id": r["district_id"],
            "name": r.get("name"),
            "human": {d: r[f"{d}_label"] for d in DIMS},
            "judge": {d: int(j.get(d, -1)) for d in DIMS},
            "judge_notes": {d: j.get(f"{d}_reasoning", "") for d in DIMS},
            "human_reasoning": r.get("reasoning", ""),
        })

    return {
        "paired": len(out_rows),
        "unlabeled": unlabeled,
        "rows": out_rows,
        "summary": _per_dim_summary(out_rows),
    }


def _per_dim_summary(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for d in DIMS:
        h = [float(r["human"][d]) for r in rows]
        j = [float(r["judge"][d]) for r in rows]
        out[d] = {
            "spearman": round(spearman(h, j), 3),
            "pearson": round(pearson(h, j), 3),
            "mae": round(mae(h, j), 2),
            "human_mean": round(statistics.fmean(h), 2),
            "judge_mean": round(statistics.fmean(j), 2),
        }
    return out


# ─── Reporting ─────────────────────────────────────────────────────────

def _format_report(result: Dict[str, Any]) -> str:
    if result["paired"] == 0:
        out = ["# Email-quality eval", "", "**No labels filled in yet.**", ""]
        if result["unlabeled"]:
            out.append("Pending district labels:")
            for did in result["unlabeled"]:
                out.append(f"- {did}")
        out.append("")
        out.append("Each entry in `evals/email_labels.jsonl` has the draft inline.")
        out.append("Fill in `hook_specificity_label`, `tone_label`, `cta_clarity_label` (0-10) and `reasoning`, then rerun.")
        return "\n".join(out) + "\n"

    s = result["summary"]
    lines = [
        "# Email-quality eval report",
        "",
        f"- Paired drafts: **{result['paired']}** "
        f"(judge: {JUDGE_MODEL}, human: SDR-style 3-dim rubric)",
        "",
        "## Per-dimension agreement",
        "",
        "| Dimension | Spearman ρ | Pearson r | MAE | Human μ | Judge μ |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for d in DIMS:
        v = s[d]
        lines.append(
            f"| {d} | {v['spearman']} | {v['pearson']} | {v['mae']} | {v['human_mean']} | {v['judge_mean']} |"
        )
    lines.append("")
    lines.append("## Per-district scores")
    lines.append("")
    lines.append("| District | hook (H/J) | tone (H/J) | CTA (H/J) |")
    lines.append("|---|---:|---:|---:|")
    for r in result["rows"]:
        h, j = r["human"], r["judge"]
        lines.append(
            f"| {r['district_id']} ({(r.get('name') or '?')[:24]}) "
            f"| {h['hook_specificity']}/{j['hook_specificity']} "
            f"| {h['tone']}/{j['tone']} "
            f"| {h['cta_clarity']}/{j['cta_clarity']} |"
        )
    if result["unlabeled"]:
        lines.append("")
        lines.append(f"_Unlabeled (skipped): {', '.join(result['unlabeled'])}_")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    result = evaluate(verbose=not args.quiet)
    report = _format_report(result)
    REPORT_PATH.write_text(report)
    print(report)
    print(f"(report written to {REPORT_PATH})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
