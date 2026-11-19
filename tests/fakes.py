"""A fake call_llm that answers by schema, so tests never hit the API."""

from __future__ import annotations

from app.actions import ActionItemCandidate
from app.llm import LLMError
from app.memory import ExtractionResult, MemoryCandidate
from app.workflow import Suggestion, Suggestions


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []
        self.candidates: list[MemoryCandidate] = []          # next extraction: memories
        self.action_items: list[ActionItemCandidate] = []    # next extraction: action items
        self.used_memory_ids: list[int] = []                 # next reply
        self.wants_intro = False                             # next reply
        self.intro_reason = ""
        self.suggested_ids: list[int] = []                   # every candidate suggestion call
        self.fail_extraction = False
        self.fail_suggestions = False
        self.fail_reply = False

    def __call__(self, system, messages, schema):
        self.calls.append({"system": system, "messages": messages, "schema": schema.__name__})
        if schema is ExtractionResult:
            if self.fail_extraction:
                raise LLMError("extraction down")
            result = ExtractionResult(candidates=self.candidates, action_items=self.action_items)
            self.candidates, self.action_items = [], []
            return result
        if schema is Suggestions:
            if self.fail_suggestions:
                raise LLMError("matching down")
            return Suggestions(candidates=[Suggestion(member_id=i, why="Good fit") for i in self.suggested_ids])
        if self.fail_reply:
            raise LLMError("reply down")
        reply = schema(language="English", reply=f"reply #{len(self.calls)}",
                       used_memory_ids=self.used_memory_ids,
                       wants_intro=self.wants_intro, intro_reason=self.intro_reason)
        self.wants_intro, self.intro_reason = False, ""
        return reply

    def calls_for(self, schema_name: str) -> list[dict]:
        return [c for c in self.calls if c["schema"] == schema_name]


def candidate(content="Wants to automate quality control", excerpt="automate quality control",
              category="goal", confidence=0.95, sensitive=False, expires_in_days=None,
              replaces=None) -> MemoryCandidate:
    return MemoryCandidate(content=content, category=category, confidence=confidence,
                           sensitive=sensitive, source_excerpt=excerpt, expires_in_days=expires_in_days,
                           replaces_memory_ids=list(replaces or []))


def action_item(description="Send the QC deck to Ana", owner="member", due_in_days=3,
                excerpt="send you the deck") -> ActionItemCandidate:
    return ActionItemCandidate(description=description, owner=owner, due_in_days=due_in_days,
                               source_excerpt=excerpt)
