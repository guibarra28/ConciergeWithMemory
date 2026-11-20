"""Run the scripted conversations against the real model and write docs/eval_report.md.

Usage (costs a few cents with gpt-4o-mini):
    python -m tests.evals.run_evals --repeat 3
    python -m tests.evals.run_evals --only 04,11

Each conversation runs on a fresh in-memory database with the demo seed data. Every
text sent to the model is recorded, so a leak is caught even if the reply hides it.
Nothing here changes prompts or rules: the report lists the errors for a person to judge.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app import db, llm, memory, orchestrator, workflow
from app.llm import LLMError
from tests.evals.cases import CASES, Case, Concept

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs" / "eval_report.md"
RAW_DIR = ROOT / "logs" / "evals"
ANALYSIS_HEADING = "## Analysis"

# USD per 1M tokens (input, output), from the OpenAI pricing page. Update if the model changes.
PRICES = {"gpt-4o-mini": (0.15, 0.60)}

# Saying "noted" is fine when something was saved that turn (the reply is told what was);
# it is an error when nothing was. Checked on every reply.
PROMISE_PHRASES = ("i'll remember", "i will remember", "i've noted", "i have noted", "noted that",
                   "i'll keep that in mind", "i will keep that in mind", "i'll make a note", "i've made a note")


# --- Matching ------------------------------------------------------------------
def matches(concept: Concept, text: str) -> bool:
    text = (text or "").lower()
    return all(any(alt.strip().lower() in text for alt in term.split("|")) for term in concept)


def any_match(concepts: list[Concept], text: str) -> bool:
    return any(matches(c, text) for c in concepts)


def show(concept: Concept) -> str:
    return " + ".join(concept)


# --- Recording every model call -------------------------------------------------
class UsageTail:
    """Reads the rows llm.py appended to logs/llm_usage.csv since the last read."""

    def __init__(self, path: Path = llm.USAGE_LOG_PATH):
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def new_rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8", newline="") as f:
            f.seek(self.offset)
            text = f.read()
            self.offset = f.tell()
        lines = [line for line in text.splitlines() if line.strip()]
        return list(csv.DictReader(lines, fieldnames=llm.USAGE_LOG_FIELDS))


class Recorder:
    def __init__(self, real, usage: UsageTail | None):
        self.real, self.usage, self.turn, self.calls = real, usage, 0, []

    def __call__(self, system, messages, schema):
        entry = {"turn": self.turn, "schema": schema.__name__,
                 "input": system + "\n" + "\n".join(m["content"] for m in messages)}
        self.calls.append(entry)
        started = time.perf_counter()
        try:
            result = self.real(system, messages, schema)
            entry["output"] = result.model_dump()
            return result
        except LLMError as exc:
            entry["error"] = str(exc)
            raise
        finally:
            entry["wall_ms"] = round((time.perf_counter() - started) * 1000)
            entry["usage"] = self.usage.new_rows() if self.usage else []


# --- Running one conversation ----------------------------------------------------
@dataclass
class Run:
    case: Case
    errors: list[str] = field(default_factory=list)
    saved: list[dict] = field(default_factory=list)       # {"content", "valid_until", "kind": saved|updated}
    confirm_asked: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    saved_per_turn: list[int] = field(default_factory=list)  # memories saved or updated by each turn
    used: list[str] = field(default_factory=list)         # last turn
    leaks: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    checks: dict = field(default_factory=dict)            # metric name -> (passed, total)
    precondition_failed: str | None = None                # the script couldn't be followed; leak check skipped


def _member_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM members WHERE display_name = ?", (name,)).fetchone()["id"]


def run_case(case: Case, real_llm, usage: UsageTail | None) -> Run:
    run = Run(case)
    conn = db.connect(":memory:")
    db.init_db(conn)
    member_id = _member_id(conn, case.member)
    for s in case.seed:
        owner = _member_id(conn, s.owner) if s.owner else member_id
        conn.execute(
            """INSERT INTO memories (member_id, content, category, visibility, sensitive, confidence,
                                     last_confirmed_at, created_at)
               VALUES (?, ?, ?, ?, ?, 1, datetime('now', ?), datetime('now', ?))""",
            (owner, s.content, s.category, s.visibility, int(s.sensitive), f"-{s.age_days} days", f"-{s.age_days} days"))
    conn.commit()

    recorder = Recorder(real_llm, usage)
    originals = {m: m.call_llm for m in (orchestrator, memory, workflow)}
    for module in originals:
        module.call_llm = recorder
    result, action_items, intro = None, [], None
    try:
        for i, turn in enumerate(case.turns):
            recorder.turn = i
            sender = case.sender or db.get_chat_login(conn, member_id)
            try:
                result = orchestrator.handle_message(conn, case.channel, sender, turn.text)
            except LLMError as exc:
                run.errors.append(f"model call failed on turn {i + 1}: {exc}")
                result = getattr(exc, "partial", None)
            if result is None:
                continue
            run.replies.append(result.reply)
            run.saved_per_turn.append(len(result.saved_memories) + len(result.updated_memories))
            run.saved += [{"content": m["content"], "valid_until": m["valid_until"], "kind": "saved"}
                          for m in result.saved_memories]
            run.saved += [{"content": new["content"], "valid_until": new["valid_until"], "kind": "updated",
                           "replaced": [p["content"] for p in previous]}
                          for new, previous in result.updated_memories]
            run.confirm_asked += [c.content for c in result.needs_confirmation]
            action_items += [a["description"] for a in result.action_items]
            intro = intro or result.intro
            for concept in turn.delete_after:
                targets = [m for m in memory.list_memories(conn, member_id) if matches(concept, m["content"])]
                if not targets:
                    # Nothing became a memory, so the member had nothing to delete. The fact is
                    # still legitimately in the history: a missed save, not a leak.
                    run.precondition_failed = f"nothing to delete after turn {i + 1}: {show(concept)} was not saved"
                for m in targets:
                    memory.delete_memory(conn, member_id, m["id"])
        final_memories = [m["content"] for m in memory.list_memories(conn, member_id)]
        if result is not None:
            run.used = [m["content"] for m in result.used_memories]
    finally:
        for module, original in originals.items():
            module.call_llm = original
        conn.close()
    run.calls = recorder.calls
    _evaluate(run, final_memories, action_items, intro)
    return run


def _evaluate(run: Run, final_memories: list[str], action_items: list[str], intro) -> None:
    c, e = run.case, run.errors
    expected_any = c.save + c.allow + c.replace + c.expires

    def check(name, ok, message):
        passed, total = run.checks.get(name, (0, 0))
        run.checks[name] = (passed + int(ok), total + 1)
        if not ok:
            e.append(message)

    # Memory writes
    for s in run.saved:
        check("write_precision", any_match(expected_any, s["content"]),
              f"saved a memory nobody expected: \"{s['content']}\"")
    for concept in c.save:
        check("write_recall", any(matches(concept, s["content"]) for s in run.saved),
              f"did not save: {show(concept)}")
    for concept in c.replace:
        check("write_recall", any(s["kind"] == "updated" and matches(concept, s["content"]) for s in run.saved),
              f"did not replace the old memory with: {show(concept)}")
    for concept in c.expires:
        hits = [s for s in run.saved if matches(concept, s["content"])]
        if hits:
            check("expiry", all(s["valid_until"] for s in hits),
                  f"temporary fact saved without an expiry: \"{hits[0]['content']}\"")
    for concept in c.confirm:
        asked = any(matches(concept, t) for t in run.confirm_asked)
        saved = any(matches(concept, s["content"]) for s in run.saved)
        check("sensitive", asked and not saved,
              f"sensitive fact {'saved without asking' if saved else 'not offered for confirmation'}: {show(concept)}")
    for concept in c.gone:
        left = [m for m in final_memories if matches(concept, m)]
        check("write_recall", not left, f"still in memory at the end: \"{left[0]}\"" if left else "")

    # The last reply
    if c.use or c.use_none:
        ok = all(any(matches(concept, u) for u in run.used) for concept in c.use)
        if c.use_none:
            ok = ok and not run.used
        check("correct_use", ok, f"used {run.used or 'no memories'}, expected "
                                 f"{[show(x) for x in c.use] or 'none'}")
    last_turn = len(c.turns) - 1
    sent = "\n".join(call["input"] for call in run.calls if call["turn"] == last_turn).lower()
    reply = (run.replies[-1] if run.replies else "").lower()
    if run.precondition_failed:
        e.append(f"not scored for leaks: {run.precondition_failed}")
    elif c.secrets:
        found_in = []
        for secret in c.secrets:
            where = [w for w, text in (("model input", sent), ("reply", reply)) if secret.lower() in text]
            if where:
                found_in.append(f"\"{secret}\" in {' and '.join(where)}")
        run.leaks = found_in
        check("no_leak", not found_in, "LEAK: " + "; ".join(found_in))
    for concept in c.reply_must:
        check("reply", matches(concept, reply), f"reply is missing: {show(concept)}")
    if c.language:
        language = next((call["output"].get("language", "") for call in reversed(run.calls)
                         if call["schema"] == "ConciergeReply" and "output" in call), "")
        check("reply", c.language in language.lower() and _looks_like(c.language, reply),
              f"reply language was {language or 'unknown'}, expected {c.language}")
    if c.intro is not None:
        check("intro", (intro is not None) == c.intro, f"intro {'not ' if c.intro else ''}created")
    for concept in c.action_items:
        check("action_items", any(matches(concept, a) for a in action_items),
              f"action item missing: {show(concept)} (got {action_items})")
    for text, saved in zip(run.replies, run.saved_per_turn):
        promise = next((p for p in PROMISE_PHRASES if p in text.lower()), None)
        check("no_promise", promise is None or saved > 0,
              f"claimed to remember with nothing saved (\"{promise}\"): \"{text[:120]}\"")


def _looks_like(language: str, reply: str) -> bool:
    """A cheap second check, so the model can't just claim the language."""
    if language != "portuguese":
        return True
    return bool(re.search(r"\b(você|não|para|que|uma|com)\b", reply))


# --- Report ---------------------------------------------------------------------
METRICS = [
    ("write_precision", "Memory write precision", "Saved memories that were expected or acceptable"),
    ("write_recall", "Memory write recall", "Expected memories saved, replaced or removed"),
    ("sensitive", "Sensitive facts held for confirmation", "Asked first, never saved directly"),
    ("expiry", "Temporary facts with an expiry", ""),
    ("correct_use", "Correct memory use", "Last reply used exactly the memories it should"),
    ("no_leak", "Conversations without a leak", "Secret never in the model input nor in the reply"),
    ("no_promise", "Replies that claim a save only when one happened", "\"I've noted\" with nothing saved is an error"),
    ("reply", "Reply checks", "Language, asking to confirm an old memory"),
    ("intro", "Intro detection", ""),
    ("action_items", "Action items extracted", ""),
]


def summarize(runs: list[Run]) -> dict:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for run in runs:
        for name, (passed, total) in run.checks.items():
            totals[name][0] += passed
            totals[name][1] += total
    calls = [call for run in runs for call in run.calls]
    usage_rows = [row for call in calls for row in call["usage"] if row.get("status") == "ok"]
    model = usage_rows[0]["model"] if usage_rows else "unknown"
    tokens_in = sum(int(r["input_tokens"] or 0) for r in usage_rows)
    tokens_out = sum(int(r["output_tokens"] or 0) for r in usage_rows)
    price = PRICES.get(model)
    cost = (tokens_in * price[0] + tokens_out * price[1]) / 1e6 if price else None
    turn_ms = defaultdict(int)
    for i, run in enumerate(runs):
        for call in run.calls:
            turn_ms[(i, call["turn"])] += call["wall_ms"]
    by_schema = defaultdict(list)
    for call in calls:
        by_schema[call["schema"]].append(call["wall_ms"])
    return {
        "metrics": {k: tuple(v) for k, v in totals.items()},
        "model": model, "calls": len(calls), "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost": cost, "cost_per_conversation": cost / len(runs) if cost is not None and runs else None,
        "turn_ms": statistics.mean(turn_ms.values()) if turn_ms else 0,
        "turn_ms_p90": sorted(turn_ms.values())[int(len(turn_ms) * 0.9) - 1] if len(turn_ms) >= 10 else None,
        "schema_ms": {k: statistics.mean(v) for k, v in by_schema.items()},
        "retries": sum(1 for call in calls for row in call["usage"] if row.get("status") == "error"),
    }


def pct(passed: int, total: int) -> str:
    return f"{passed / total:.0%} ({passed}/{total})" if total else "n/a"


def write_report(runs: list[Run], repeat: int, path: Path = REPORT_PATH) -> str:
    s = summarize(runs)
    lines = [
        "# Eval report",
        "",
        f"Generated by `python -m tests.evals.run_evals --repeat {repeat}` on "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Model: `{s['model']}`. "
        f"{len({r.case.id for r in runs})} scripted conversations, each run {repeat} times "
        f"({len(runs)} runs, {s['calls']} model calls). Cases: [tests/evals/cases.py](../tests/evals/cases.py).",
        "",
        "## Results",
        "",
        "| Metric | Result | What it counts |",
        "| --- | --- | --- |",
    ]
    for key, label, note in METRICS:
        if key in s["metrics"]:
            lines.append(f"| {label} | {pct(*s['metrics'][key])} | {note} |")
    cost = f"${s['cost']:.4f} total, ${s['cost_per_conversation']:.5f} per conversation" if s["cost"] is not None else "n/a"
    p90 = f", p90 {s['turn_ms_p90'] / 1000:.1f} s" if s["turn_ms_p90"] else ""
    lines += [
        f"| Cost | {cost} | {s['tokens_in']:,} input and {s['tokens_out']:,} output tokens, list prices |",
        f"| Latency per message | {s['turn_ms'] / 1000:.1f} s average{p90} | All model calls for one member message |",
        "",
        "Average latency by call: " + ", ".join(f"{k} {v / 1000:.1f} s" for k, v in sorted(s["schema_ms"].items()))
        + f". Retries after a timeout or server error: {s['retries']}.",
        "",
        "## By conversation",
        "",
        "| # | Conversation | Passed | Errors |",
        "| --- | --- | --- | --- |",
    ]
    by_case: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_case[run.case.id].append(run)
    for case_id, case_runs in by_case.items():
        ok = sum(1 for r in case_runs if not r.errors)
        errors = Counter(err for r in case_runs for err in r.errors)
        summary = "<br>".join(f"{_cell(err)} (×{n})" for err, n in errors.most_common()) or ""
        lines.append(f"| {case_id} | {case_runs[0].case.title} | {ok}/{len(case_runs)} | {summary} |")
    lines += ["", "## Replies to read", "",
              "The last reply of the first run of each conversation, so the numbers can be checked by eye.", ""]
    for case_id, case_runs in by_case.items():
        reply = case_runs[0].replies[-1] if case_runs[0].replies else "(no reply)"
        lines.append(f"- **{case_id} · {case_runs[0].case.title}:** {_cell(reply)}")

    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    analysis = previous[previous.index(ANALYSIS_HEADING):] if ANALYSIS_HEADING in previous else (
        f"{ANALYSIS_HEADING}\n\nTo be written after reading the errors one by one.\n")
    text = "\n".join(lines) + "\n\n" + analysis
    path.write_text(text, encoding="utf-8")
    return text


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def save_raw(runs: list[Run]) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps([{
        "case": r.case.id, "errors": r.errors, "saved": r.saved, "confirm_asked": r.confirm_asked,
        "used": r.used, "replies": r.replies, "leaks": r.leaks, "checks": r.checks,
        "calls": [{k: v for k, v in call.items() if k != "input"} for call in r.calls],
    } for r in runs], indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_all(cases: list[Case], real_llm=None, repeat: int = 1, track_usage: bool = True) -> list[Run]:
    real_llm = real_llm or llm.call_llm
    usage = UsageTail() if track_usage else None
    runs = []
    for case in cases:
        for _ in range(repeat):
            runs.append(run_case(case, real_llm, usage))
            print(f"{case.id} {'ok ' if not runs[-1].errors else 'ERR'} {case.title}", flush=True)
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--only", help="comma-separated case ids, e.g. 04,11")
    args = parser.parse_args()
    cases = [c for c in CASES if not args.only or c.id in args.only.split(",")]
    runs = run_all(cases, repeat=args.repeat)
    write_report(runs, args.repeat)
    print(f"Report: {REPORT_PATH}\nRaw results: {save_raw(runs)}")


if __name__ == "__main__":
    main()
