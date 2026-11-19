# Decisions log

Product and UX decisions that are not obvious from the code. Business rules for intros live in [model.md](model.md#5-decisions); product scope lives in the [PRD](prd.md).

## UX decisions (wireframe)

Reference: [wireframe.html](wireframe.html).

### 1. Saved memories appear as a quiet notice with Undo, not as a permission prompt

**Decision.** When the concierge saves a fact, a small "Saved: … · Undo" line appears under the reply. The conversation does not stop to ask.

**Why.** Discovery hypothesis H1: asking permission for every fact adds friction and makes the agent feel intrusive, while a visible notice with an immediate Undo gives the same control. The undo rate is also our main signal that the agent stores things it shouldn't (PRD metric).

### 2. Consent is a card with checkboxes for exactly what will be shared

**Decision.** Before an intro request reaches the other member, the requester sees a card listing each item the other person would see, each with its own checkbox. Nothing is shared by default beyond what is checked.

**Why.** "I'm fine being introduced, but I want to know who and why first" (Ana). A yes/no question hides what is being shared; a checklist makes consent specific and is what gets stored in the consent event (R4).

### 3. Every reply shows which memories it used, and intros that fail say only "Won't go ahead"

**Decision.** Under each concierge reply, a "Used: …" line links to the memories that shaped it. In the pending panel, a declined, expired or cancelled intro shows the same neutral status, with a link to ask for another.

**Why.** Showing the memories used makes memory inspectable in context, which builds trust and makes wrong memories easy to spot and fix (R1). Using one neutral status for every unsuccessful intro protects the other member's privacy and avoids the awkwardness discovery identified as a pain.

## Why filter before the model

A model cannot reveal what it never received, so removing other people's data before the call makes a leak impossible instead of unlikely. Asking the model to keep a secret depends on it following instructions every time, and a single prompt injection, an ambiguous request or a model update can break that. Filtering in code is also testable: the leak tests check every text sent to the model, which is a guarantee we can run on every change, while "the model behaved" can only be sampled.

In this product the filter lives in one place, [app/permissions.py](../app/permissions.py), and the prompt instruction ("don't share other members' information") is a second layer that shapes the wording of the refusal, not the protection itself.

## Other decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-12 | Simulated email inbox instead of a real provider for the prototype. | Streamlit cannot receive webhooks; the identity and timeline logic is the same. |
| 2026-11-18 | An email address that is similar but not identical to a registered one is treated as unknown. Matching is exact after lowercasing and trimming. | A false match would show one member's data to someone else; a false "unknown" only costs the member one verification step. Typos and new addresses are fixed by registering the address, not by guessing. |
| 2026-11-18 | Unknown senders get a fixed reply, written in code, and the model is never called. | Not calling the model is the only way to guarantee no member data is used. The message is kept in quarantine for the team. |
| 2026-11-18 | Email and password login replaces the persona selector. Passwords are stored as salted scrypt hashes; the login email follows the same identity rules as inbound email; every failure shows the same message; 5 failures lock the form for 30 seconds. | The member should not have to say who they are on every message. Using the identity rules for login means one definition of "who is this" across the product. |
| 2026-11-18 | The simulated email inbox offers the signed-in member's own addresses, plus "another address" for testing unknown senders. | Typing a sender every time was friction; the free-text option keeps the R3 test possible. |
| 2026-11-18 | A memory candidate is saved only if its supporting excerpt is found in the member's message (comparing words, ignoring punctuation). | The model must not save what the member did not say. The first version compared characters, and a period the model added made a health fact disappear instead of triggering the confirmation question. |
| 2026-11-18 | Deleting a memory also hides the exchange it came from from the model; the member still sees it in the timeline. | In testing, a deleted "no vendors" preference was still applied because the model read it in the conversation history. Forgetting has to cover both. |
| 2026-11-18 | Memory extraction is a separate model call from the reply, and its failure does not block the reply. | Separate calls make extraction testable on its own (evals in c3). Answering the member matters more than remembering. |
| 2026-11-18 | The concierge states it has no real-time data. | In testing it invented a weather forecast. |
| 2026-11-18 | Who can cancel an intro, by state: `requested` and `awaiting_a`: the requester or the manager. `awaiting_b`, `introduced`, `scheduled`: either member or the manager. `follow_up` and terminal states: nobody (it closes on its own). | B can't cancel what B hasn't been told about. After contact is shared, cancelling stops reminders but can't take details back. See [model.md](model.md#5-decisions). |
| 2026-11-18 | The model only detects that the member wants an intro (`wants_intro`). Candidates come from a separate matching call that sees only shareable, non-sensitive memories of members who are not blocked; the manager must pick one of those suggestions. | Who gets suggested and what is shared is decided by code and a person, not by the chat reply. Blocked pairs are removed before the model sees the list. |
| 2026-11-18 | A shareable memory can be used for matching, and its one-line "why" is shown to the manager and the requester before the candidate consents. The candidate's other details are shown only after they accept. | "Shareable" means "can be used for intros I approve". Without a reason, neither the manager nor the requester can judge the match. |
| 2026-11-18 | A simulated clock ("Advance 1 day") drives deadlines, reminders and follow-ups. | A multi-day flow has to be demonstrable in minutes. The clock is stored in the database, so a restart keeps the same date. |
| 2026-11-18 | Action items are extracted in the same model call as memories, and still while memory is paused. Team requests go to the relationship manager; missing due dates default to 7 days and the member can change them. | Action items are commitments, not facts about the member. R7 requires an owner and a due date on every item. |
| 2026-11-18 | The concierge never says it will book or send something itself. | In testing it promised to reserve an event seat, which only the team can do. |
| 2026-11-19 | One module ([permissions.py](../app/permissions.py)) decides memory visibility for every purpose: own conversation (all own memories), matching (shareable, not sensitive), team (shareable and internal, not sensitive, managers only). | One place to review and test. Before this, three modules had their own SQL filters. |
| 2026-11-19 | The manager gets a "Member profiles" view and can ask the concierge questions, both limited to the team view. | Managers need shared profiles to judge matches. Limiting the concierge to the same view means the manager can't get through chat what the profiles don't show. |
| 2026-11-19 | The simulated inbox refuses to send from another member's verified address. | Found while looking for leaks: signed in as Ana, sending "from" Roberto's address showed his concierge reply on Ana's screen. **Production risk:** real email senders can be spoofed, so the email adapter must verify sender authentication (SPF, DKIM, DMARC) before trusting the From address. |
| 2026-11-19 | Team notes (`internal`) are not used as the requester's preferences in matching. | Found by the leak tests: a team note about Ana was sent to the matching model as if it were her preference. |
| 2026-11-19 | Known limitation: a member's *private* preference (e.g. "no vendors") guides their own intro matching, but the manager's chat can't see it and may suggest a vendor. | Private means private, even from the team. The member can make it shareable if they want the team to know. |
| 2026-11-18 | The reply schema asks the model for the message's language before the reply itself. | A prompt instruction alone was ignored in testing (English questions got Portuguese and Spanish answers). Committing to the language first fixed it. |
