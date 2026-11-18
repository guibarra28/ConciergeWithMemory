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
| 2026-11-18 | The reply schema asks the model for the message's language before the reply itself. | A prompt instruction alone was ignored in testing (English questions got Portuguese and Spanish answers). Committing to the language first fixed it. |
