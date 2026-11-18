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
