import csv
from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel

from app import llm


class Answer(BaseModel):
    text: str


class FakeResponses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok_response(text="hello"):
    return SimpleNamespace(
        output_parsed=Answer(text=text),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _timeout_error():
    # Build the exception without an HTTP request object; only its type matters here.
    err = openai.APITimeoutError.__new__(openai.APITimeoutError)
    Exception.__init__(err, "Request timed out.")
    return err


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr(llm, "USAGE_LOG_PATH", tmp_path / "llm_usage.csv")
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    def install(outcomes):
        responses = FakeResponses(outcomes)
        monkeypatch.setattr(llm, "_get_client", lambda: SimpleNamespace(responses=responses))
        return responses

    return install


def _read_log(tmp_path):
    with (tmp_path / "llm_usage.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_returns_parsed_output_and_logs_usage(fake_client, tmp_path):
    fake_client([_ok_response("hi")])

    result = llm.call_llm("system", [{"role": "user", "content": "hey"}], Answer)

    assert result == Answer(text="hi")
    rows = _read_log(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["total_tokens"] == "15"
    assert rows[0]["model"] == "test-model"


def test_retries_once_on_timeout(fake_client, tmp_path):
    responses = fake_client([_timeout_error(), _ok_response()])

    result = llm.call_llm("system", [{"role": "user", "content": "hey"}], Answer)

    assert result.text == "hello"
    assert responses.calls == 2
    assert [r["status"] for r in _read_log(tmp_path)] == ["error", "ok"]


def test_raises_after_second_failure(fake_client):
    responses = fake_client([_timeout_error(), _timeout_error()])

    with pytest.raises(llm.LLMError):
        llm.call_llm("system", [{"role": "user", "content": "hey"}], Answer)
    assert responses.calls == 2


def test_raises_when_no_parsed_output(fake_client):
    fake_client([SimpleNamespace(output_parsed=None, usage=None)])

    with pytest.raises(llm.LLMError):
        llm.call_llm("system", [{"role": "user", "content": "hey"}], Answer)


def test_missing_model_env(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(llm.LLMError):
        llm.call_llm("system", [], Answer)
