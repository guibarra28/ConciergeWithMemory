# Concierge with Memory

An AI concierge for members of an executive community. It remembers who each member is, what they care about and what they have asked before, so every conversation picks up where the last one left off.

## The problem

Executive communities promise curated connections, relevant events and trusted recommendations. In practice, members repeat the same context to staff over and over, and the community team can't keep track of hundreds of individual preferences, goals and past requests.

## What the concierge does

- **Remembers each member**: role, company, industry, goals, interests, preferences and past conversations.
- **Answers in context**: recommendations take into account what the member has already shared, without asking again.
- **Connects people**: suggests introductions to other members based on complementary goals and expertise.
- **Surfaces the right opportunities**: events, content and resources that match each member's current priorities.
- **Keeps the team informed**: gives community managers a structured view of what members need.

## Tech stack

- **Python** + **Streamlit** for the interface
- **OpenAI API** with structured outputs (Pydantic schemas)
- **pytest** for tests

## Project structure

```
app/        Application code (LLM wrapper, memory, UI)
data/       Member data and memory storage
docs/       Product and technical documentation
tests/      Automated tests
scripts/    Utility scripts
logs/       Runtime logs, e.g. LLM token usage and latency (not committed)
```

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
cp .env.example .env          # then add your OpenAI key and model
pytest
```

### Environment variables

| Variable | Description |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI API key |
| `OPENAI_MODEL` | Model used for every LLM call |
| `OPENAI_TIMEOUT_SECONDS` | Request timeout in seconds (optional, default 30) |

## LLM usage logging

Every call to the model goes through `app/llm.py::call_llm`, which:

- returns the response parsed into a Pydantic schema (structured outputs);
- applies a timeout and retries once on transient errors;
- appends tokens and latency for each attempt to `logs/llm_usage.csv`.

## Status

Early development.
