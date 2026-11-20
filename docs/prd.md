# PRD: Concierge with Memory

| | |
| --- | --- |
| **Author** | Guilherme Barra |
| **Status** | Final for the prototype. The next step is a production pilot (section 9). |
| **Last updated** | 2026-11-20 |
| **Links** | [Discovery](discovery.md) · [Architecture](architecture.md) · [Data model](model.md) · [Decisions](decisions.md) · [Edge cases](edge_cases.md) · [Eval report](eval_report.md) · [Copy](copy.md) |

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
| **Relationship manager** (secondary) | Proposes matches, monitors intro status and steps in when a request stalls. | See where each intro stands and who owes the next action, without reading private conversations. |

## 3. Goal

A member never has to repeat context, and every introduction moves forward with both sides' recorded consent or closes with a clear notice, without one member's private information ever reaching another.

## 4. What the prototype does

A working prototype, deployed with demo accounts. Every requirement below is built and covered by automated tests (182, plus 15 scripted conversations run against the real model).

| ID | Requirement | Priority | How it works in the prototype |
| --- | --- | --- | --- |
| R1 | Members can view, edit, delete and pause their memories | Must | "What I know about you" panel with edit, delete and a pause toggle. A "Saved to memory · Undo" notice appears in the conversation. Deleting also removes the exchange from the model's context. |
| R2 | Chat and email from the same member form one timeline | Must | Chat plus a simulated email inbox. Both write to one timeline per member, labeled by channel. A failed reply keeps the message and offers Try again. |
| R3 | Unknown senders access no memory | Must | An unregistered address gets a fixed reply written in code. The model is never called and the message is held for the team. |
| R4 | An intro only advances with recorded consent from both sides | Must | State machine persisted in SQLite. Each side picks exactly which items the other will see, and every transition is stored as an event. |
| R5 | Unanswered requests expire after 3 days with notice | Must | A demo clock ("Advance 1 day") drives reminders, expiry and follow-ups. Every ending reads "won't go ahead", so nobody learns the reason. |
| R6 | Memories have visibility: private, shareable, internal | Must | One permission module filters what the model receives for each purpose before the call. Leak tests check every text sent to the model. |
| R7 | Action items extracted from conversation have an owner and due date | Should | Extracted in the same call as memories. Team requests go to the manager, the due date defaults to 7 days, and the member can change it. |
| R8 | The agent flags memories that may be stale | Should | Memories not confirmed in 6 months are flagged in the panel ("Still true?"). The concierge asks the member to confirm before relying on one. |

## 5. What changed while building, and why

The draft from the start of the day held up. These are the decisions that the build, the tests and the evals added. Full log: [decisions.md](decisions.md).

| Change | Why |
| --- | --- |
| A memory is saved only if its supporting words are really in the member's message. | The model must not save what the member did not say. Character comparison failed on punctuation, so the check compares words within one sentence. |
| A fact in the same sentence as a health fact waits for the same confirmation. | In the evals, "cut back on travel because of my heart condition" saved the travel part and only the heart part was held, which exposed the health context. |
| A new fact can replace an outdated one, with Undo, and the new version starts private. | "I left Metalúrgica Silva" used to leave both companies in memory. Agreeing to share the old fact is not agreeing to share the new one. |
| Before deleting a memory another member already saw, the member is warned that it can't be unread. | Deleting can't take back what was read. The warning is the honest version of "delete". |
| The reply is told what was actually saved this turn. | The concierge kept saying "I've noted that" whether or not anything was saved. After the fix, a false "noted" appeared in 4 of 160 replies. |
| The concierge says it has no information about events, venues or schedules. | It invented a dress code in 3 of 3 runs. After the fix it did so in 0 of 10. |
| A deleted memory also hides its exchange from the model. | A deleted "no vendors" preference was still applied because the model read it in the history. |
| Interface copy follows one set of rules ([copy.md](copy.md)): say what happened, then what to do; lead with what is safe; one name per thing. | Members judge trust by these sentences. Delete had no confirmation at all, and one error showed an internal state name. |

## 6. Out of scope, and why

| Item | Why not now |
| --- | --- |
| R9: assistant emails count as the member's if registered | Marked out of scope in discovery. The identity model already supports several addresses per member, so it can be added later without redesign. |
| WhatsApp channel | Chat plus email is enough to prove the cross-channel timeline. WhatsApp adds provider approval and cost. |
| Real email sending and receiving | Streamlit cannot receive webhooks. The simulated inbox runs the same identity and timeline logic. |
| Single sign-on, password reset and persistent sessions | Need an email provider and cookies, and don't change what the prototype validates. |
| Calendar integration for intro meetings | "Meeting booked" is set by hand. Integration doesn't change the consent logic. |
| Forgetting a single message that never became a memory | Found in the evals. Today the only way is "Delete everything". |

## 7. Results

**Quality, from 15 scripted conversations run 10 times each against gpt-4o-mini** ([eval report](eval_report.md)):

| Metric | First run | After fixes |
| --- | --- | --- |
| Memory write precision (saved memories that were correct) | 76% | 88% (67 of 76) |
| Memory recall (expected memories that were saved) | 100% | 100% |
| Sensitive facts held for confirmation | 100% | 100% |
| Correct use of memories in replies | 100% | 93% |
| Leaks of another member's data | 0 of 12 | 0 of 40 |
| Replies that claim to have "noted" something not saved | 7 of 48* | 4 of 160 |
| Cost per conversation | | $0.00035 |
| Average latency per turn | | 3.3 s |

\*The first runs used 3 repetitions and counted every "noted", because the reply had no way to know what was saved. The later runs used 10 repetitions and count only false claims.

**Remaining errors:**
- low-value memories, such as "Gets enough sales pitches already" in 8 of 10 runs, which hold precision at 88%;
- "I'll make a note" right after a health fact that is still waiting for confirmation;
- the concierge saying it "passed it to your manager" when no action item was created.

**Product metrics for a pilot.** None of these can be measured in the prototype. They need real members.

| Metric | Definition | Target |
| --- | --- | --- |
| Intros that reach a booked meeting | Intros in "Meeting booked" ÷ intros created, within 14 days | Beat today's ~50% stall rate after a baseline |
| Undone or deleted memories | Memories undone or deleted within 7 days ÷ memories saved | 5–15%. Above 20% means the agent stores what it shouldn't. |
| Leaks | Private or internal memory of one member in another user's context or reply | 0, checked on every change by the leak tests |
| Repeated context | Conversations where the member restates a fact already stored | Below 10% |

## 8. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| One member's data reaches another member | Critical | The permission filter runs before the model; leak tests must pass at 0. |
| Prompt injection in an email | Critical | The model never receives memories it isn't allowed to reveal. Tested with a hidden instruction in an email: 0 leaks. |
| Spoofed email sender | Critical in production | The prototype refuses another member's address. Production must check SPF, DKIM and DMARC before trusting the From address. |
| Wrong identity match merges two people | High | Exact match on verified addresses only; unknown senders get no memory. |
| Agent stores sensitive, inferred or low-value facts | High | Excerpt check, confirmation for health and personal facts, Undo, and the undo rate as an alarm. Low-value memories are the main remaining eval error. |
| Data loss on redeploy | High in production | The prototype's SQLite file is lost when Railway redeploys. See section 9. |
| Stale information used in an intro | Medium | Flag after 6 months; the concierge asks before relying on it. Holding flagged memories out of matching is still open. |
| LLM call fails mid-flow | Medium | The message is saved before any model call, Try again answers it once, and intro transitions are versioned and idempotent. |
| API cost | Low | $0.00035 per conversation, usage logged per call, and a spending limit on the account. |

## 9. Path to production

| Area | Prototype | Production | Why |
| --- | --- | --- | --- |
| Database | SQLite on Railway's ephemeral disk | **Postgres, managed by Supabase.** A Railway volume is the short-term fix for the demo. | Backups and point-in-time recovery. Row-level security as a second permission layer under app/permissions.py. pgvector for memory search when members have hundreds of memories. Ids are never reused, which removes the class of bug found in c2. Migration estimate: about half a day, since all SQL is in a few modules. |
| Email | Simulated inbox | Inbound and outbound provider (for example Postmark or SES) with SPF, DKIM and DMARC checks | Spoofing is the main identity risk (E17). |
| Sign-in | Email and password, shared demo password | SSO or magic link, persistent sessions | Members shouldn't manage another password. |
| Time | Demo clock | Scheduled job for reminders and expiry | Same `tick` function, called by a scheduler. |
| Quality | Evals run by hand | Evals on every prompt or model change, with the leak cases as a release gate | The c3 regression was caught only because the full suite was re-run. |

## 10. Open questions

- [ ] How long must consent records be kept after a member deletes everything? (Legal and community leadership)
- [ ] What are today's baseline numbers for intro completion and response time? (Relationship managers)
- [ ] Which email provider will be used in production? (Engineering)
- [ ] Should a member be able to forget a single message, not just a memory? (Product, from the c3 evals)
- [ ] Should flagged stale memories be held out of intro matching until confirmed? (Product)

Decided for the prototype (details in [model.md](model.md#5-decisions)): managers don't start intros on their own, and members can see `internal` memories about themselves.
