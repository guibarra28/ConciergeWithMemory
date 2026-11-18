"""A fake call_llm that answers by schema, so tests never hit the API."""

from __future__ import annotations

from app.llm import LLMError
from app.memory import ExtractionResult, MemoryCandidate


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []
        self.candidates: list[MemoryCandidate] = []  # returned by the next extraction call
        self.used_memory_ids: list[int] = []         # returned by the next reply call
        self.fail_extraction = False

    def __call__(self, system, messages, schema):
        self.calls.append({"system": system, "messages": messages, "schema": schema.__name__})
        if schema is ExtractionResult:
            if self.fail_extraction:
                raise LLMError("extraction down")
            result, self.candidates = ExtractionResult(candidates=self.candidates), []
            return result
        return schema(language="English", reply=f"reply #{len(self.calls)}", used_memory_ids=self.used_memory_ids)

    def calls_for(self, schema_name: str) -> list[dict]:
        return [c for c in self.calls if c["schema"] == schema_name]


def candidate(content="Wants to automate quality control", excerpt="automate quality control",
              category="goal", confidence=0.95, sensitive=False, expires_in_days=None) -> MemoryCandidate:
    return MemoryCandidate(content=content, category=category, confidence=confidence,
                           sensitive=sensitive, source_excerpt=excerpt, expires_in_days=expires_in_days)
