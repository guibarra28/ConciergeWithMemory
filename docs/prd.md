# PRD: Concierge with Memory

| | |
| --- | --- |
| **Author** | Guilherme Barra  |
| **Status** | Draft |
| **Last updated** | 2026-11-18 |
| **Links** | [Discovery](discovery.md) · [Architecture](architecture.md) |

## 1. Problem

Members of a 1,200-person executive community have to re-explain who they are every time they talk to the team, and the member-to-member introductions they pay for stall or happen without recorded consent.

**Evidence**

- Members repeat context in every new conversation or channel: "I have to explain again who I am and what I'm looking for" (Roberto, CEO).
- About half of intro requests get no follow-up after 2 weeks. One manager makes ~40 intros a month over WhatsApp and "loses track of half of them" (Juliana).
- There is no record of who agreed to be introduced, which leads to awkward cold contacts.
- The same member writes from several email addresses and the threads are not connected.
- Members accept being remembered only if they can see and delete what was stored. A leak between members "would end the brand" (Marcos, CTO).

## 2. Users

| User | Role in the product | Primary need |
| --- | --- | --- |
| **Member** (primary) | Talks to the concierge by chat and email; asks for intros; approves or declines intros. | Be remembered across channels, stay in control of what is stored, and only be introduced with consent. |
| **Relationship manager** (secondary) | Monitors intro status and steps in when a request stalls. | See where each intro stands and who owes the next action, without reading private conversations. |

## 3. Goal

A member never has to repeat context, and every introduction moves forward with both sides' recorded consent or closes with a clear notice, without one member's private information ever reaching another.

## 4. Scope for today

The deliverable is a one-day working prototype, not a production launch.

| ID | Requirement | Priority | How it shows up in the prototype |
| --- | --- | --- | --- |
| R1 | Members can view, edit, delete and pause their memories | Must | "What I know about you" panel with edit, delete and a pause toggle; "saved" notice with Undo in the conversation. |
| R2 | Chat and email from the same member form one timeline | Must | Chat plus a simulated email inbox; both write to one timeline per member, labeled by channel. |
| R3 | Unknown senders access no memory | Must | An email from an unregistered address gets a generic reply asking for verification; no memory is loaded. |
| R4 | An intro only advances with recorded consent from both sides | Must | Intro state machine; each consent is stored as an event with who, when and what was shared. |
| R5 | Unanswered requests expire after 3 days with notice | Must | "Advance 1 day" button simulates time; requests expire on day 3 and both the requester and the manager are notified. |
| R6 | Memories have visibility: private, shareable, internal | Must | Visibility on every memory; context is filtered by permission before it reaches the model. |
| R7 | Action items extracted from conversation have an owner and due date | Should | Action items panel; the agent proposes an owner and due date and the member can correct them. |
| R8 | The agent flags memories that may be stale | Should | Role and company memories not confirmed in 6 months are flagged; the agent asks the member to confirm. |

## 5. Out of scope, and why

| Item | Why not now |
| --- | --- |
| R9: assistant emails count as the member's if registered | Marked out of scope in discovery. The identity model already supports several addresses per member, so it can be added later without redesign. |
| WhatsApp channel | Members use it today, but chat plus email is enough to prove the cross-channel timeline. WhatsApp adds provider approval and cost. |
| Real email sending and receiving | Streamlit cannot receive webhooks. A simulated inbox tests the same logic in one day. |
| Real login and authentication | A persona selector (Roberto, Ana, Juliana) is enough for a prototype. Authentication is required before any real member uses it. |
| Calendar integration for booking intro meetings | "Meeting booked" is set manually. Integration does not change the consent logic being validated. |
| Automated memory quality tuning | Today we measure quality with scripted test conversations; tuning comes after we see the errors. |

## 6. Key decisions

| Question | Decision | Reason |
| --- | --- | --- |
| Do we ask permission before saving each memory? | No. Save and show a subtle notice with Undo. | Discovery hypothesis H1: less friction and more trust than a prompt for every fact. |
| What is the default visibility of a new memory? | Private. | Safe default. Becoming shareable requires a member action. |
| What can never be stored or used? | Personal life and health are never used in intros, and are only stored after explicit confirmation. Inferred facts are never stored. | Privacy limit 1, and "remember only what the member actually said". |
| What happens to an unknown email sender? | Generic reply asking for verification; message goes to a quarantine queue; no memory access. | R3 and hypothesis H4. |
| What if the address is similar but not identical to a registered one? | Treated as unknown. No fuzzy matching. | A wrong merge would show one member's data to someone else, which is worse than asking to verify. |
| Who can cancel an intro? | Either member at any point before "Introduced"; the manager at any point. | Consent can be withdrawn; the manager owns stalled requests. |
| What does "pause memory" do? | Stops new memories from being saved. Existing memories are still used. | Matches what members asked for: control without losing continuity. |
| What does "delete everything" remove? | All memories and the full timeline. Intro records keep only status and consent events, without message content. | Privacy limit 4, while keeping proof of consent for intros that already happened. |
| Can managers read conversations? | No. They see intro status, owner and deadline only. | Privacy limit 3. |

## 7. Success metrics

Targets for the prototype are measured on scripted test conversations; production targets need a baseline first.

| Metric | Definition | Target |
| --- | --- | --- |
| Intros that reach a booked meeting | Intros in "Meeting booked" ÷ intros created, within 14 days of the request | Baseline first; goal is to beat today's ~50% stall rate |
| Undone or deleted memories | Memories undone or deleted within 7 days ÷ memories saved | 5–15%. Above 20% means the agent stores what it shouldn't. |
| Leaks in tests | Test cases where one member's private or internal memory appears in another user's context or reply | 0 |
| Repeated context | Test conversations where the member had to restate a fact already stored | Below 10% |

## 8. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| One member's data reaches another member | Critical: loss of trust in the brand | Permission filter before the model; leak tests that must pass at 0. |
| Prompt injection in an email ("list Roberto's memories") | Critical | The model never receives memories it is not allowed to reveal, so an instruction cannot extract them. |
| Wrong identity match merges two people | High | Exact match on verified addresses only; unknown senders get no memory. |
| Agent stores sensitive or inferred facts | High | Extraction rules, explicit confirmation for sensitive categories, Undo, and the deleted-memory metric as an alarm. |
| Stale information used in an intro | Medium | Staleness flags and confirmation before a flagged memory is used in an intro. |
| LLM call fails in the middle of an intro transition | Medium | Transitions are saved only after validation; the previous state is preserved and the member sees what happened. |
| API cost grows with every message | Low for the prototype | Token and latency logging per call; spending limit on the OpenAI account. |

## 9. Open questions

- [ ] How long must consent records be kept after a member deletes everything? — Legal / community leadership
- [ ] What are today's baseline numbers for intro completion and response time? — Relationship managers
- [ ] Which email provider will be used in production? — Engineering

Decided for the prototype (details in [model.md](model.md#5-decisions)): managers do not start intros on their own, and members can see `internal` memories about themselves.
