"""The eval runner itself, checked with the fake model (no API calls, no cost)."""

from tests.evals import run_evals
from tests.evals.cases import CASES
from tests.fakes import FakeLLM, candidate


def by_id(case_id):
    return next(c for c in CASES if c.id == case_id)


def test_concepts_match_any_alternative_and_every_term():
    assert run_evals.matches(("automat", "quality|qc"), "Wants to automate QC")
    assert not run_evals.matches(("automat", "quality|qc"), "Wants to automate sales")


def test_every_case_runs_and_the_report_is_written(tmp_path):
    runs = run_evals.run_all(CASES, real_llm=FakeLLM(), repeat=1, track_usage=False)
    assert len(runs) == len(CASES) == 15
    report = run_evals.write_report(runs, 1, path=tmp_path / "report.md")
    assert "## Results" in report and "## Analysis" in report


def test_the_analysis_section_survives_a_new_run(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("old numbers\n\n## Analysis\n\nMy notes.\n", encoding="utf-8")
    run_evals.write_report(run_evals.run_all([by_id("03")], FakeLLM(), 1, False), 1, path=path)
    text = path.read_text(encoding="utf-8")
    assert "My notes." in text and "old numbers" not in text


def test_a_leak_in_the_reply_is_caught():
    class Leaky(FakeLLM):
        def __call__(self, system, messages, schema):
            result = super().__call__(system, messages, schema)
            if schema.__name__ == "ConciergeReply":
                result.reply = "He plans to sell the company to a competitor."
            return result

    run, = run_evals.run_all([by_id("12")], Leaky(), 1, False)
    assert run.leaks and any(err.startswith("LEAK") for err in run.errors)


def test_an_unexpected_memory_is_an_error():
    fake = FakeLLM()
    fake.candidates = [candidate("Prefers a formal dress code", "dress code")]
    run, = run_evals.run_all([by_id("03")], fake, 1, False)
    assert run.checks["write_precision"] == (0, 1)
    assert any("nobody expected" in err for err in run.errors)


def test_a_promise_to_remember_is_flagged():
    class Promising(FakeLLM):
        def __call__(self, system, messages, schema):
            result = super().__call__(system, messages, schema)
            if schema.__name__ == "ConciergeReply":
                result.reply = "Got it, I've noted that for next time."
            return result

    run, = run_evals.run_all([by_id("03")], Promising(), 1, False)
    assert run.checks["no_promise"] == (0, 1)
