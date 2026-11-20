# Interface copy

Every sentence a member or manager reads in the prototype, reviewed as a UX writer in block d1. The code is the source of truth: [app/main.py](../app/main.py), plus the notices in [app/workflow.py](../app/workflow.py), [app/actions.py](../app/actions.py) and [app/orchestrator.py](../app/orchestrator.py).

## The rules

1. **Say what happened, then what the person can do.** "Your message is saved" comes before "Select Try again".
2. **Lead with what is safe.** After a failure, the first thing to say is what was *not* lost.
3. **No apologies, no blame.** No "sorry" and no "oops". The member needs a next step, not an emotion.
4. **No internal words.** State names (`awaiting_b`), "quarantine", "durable fact" and "verified identity" stay in the code.
5. **One name per thing.** The memory panel is always "What I know about you", the tab is always "Pending", and the visibility levels are always "Private" and "Shareable". A word on a button is the same word in the sentence that points to it.
6. **Voice.** The concierge says "I" when it talks about its own memory ("I won't use it again"). App status, such as errors and the demo clock, talks about "the concierge" in the third person, because those messages come from the product, not from the assistant.
7. **Short and specific.** Name the item ("Deleted: Prefers meetings before 9am"), not the category ("Memory deleted").

## The five moments the plan asked for

### 1. Memory saved

| | Text |
| --- | --- |
| Before | Saved: *Wants to automate quality control* · [Undo] |
| After | Saved to memory: *Wants to automate quality control* · [Undo] |
| After Undo (new) | Undone. I won't remember: *Wants to automate quality control*. |

- "Saved" alone didn't say *where*. In a chat, "saved" can mean the message itself.
- Undo used to remove the notice without a word, so the member couldn't tell whether it had worked. Now it confirms.

**Memory updated** (an old memory was replaced):

| | Text |
| --- | --- |
| Before | Updated: *Runs Aço Novo* (was: *Runs Metalúrgica Silva*) |
| After | Memory updated: *Runs Aço Novo*. Before: *Runs Metalúrgica Silva*. |
| Before | The old version was shared for intros. The new one is private until you share it again in the memory panel. |
| After | The old version was Shareable. The new one stays Private until you make it Shareable in What I know about you. |

- A sentence reads better than a bracketed aside.
- The caption now uses the level names the member sees on every memory, and the panel's real name. Nothing on screen is called "memory panel".

**Sensitive fact waiting for a yes:**

| | Text |
| --- | --- |
| Before | This sounds personal: **Has a heart condition**. Do you want me to remember it? / It would stay private and never be used in intros. · [Remember privately] [Don't remember] |
| After | Should I remember this? **Has a heart condition** / It looks like health or personal information, so I save it only if you say yes. It would stay private and never be used in intros. · [Remember privately] [Don't save] |

- The question comes first, and the caption says *why* it is asking. Otherwise the member can't tell why this fact needs a yes when others were saved without asking.
- "Don't remember" sounded like an instruction to forget something already kept. "Don't save" says exactly what happens.

### 2. Memory deleted

| | Text |
| --- | --- |
| Before | *(nothing: the memory disappeared from the list)* |
| After | Deleted: *Prefers meetings before 9am*. I won't use it again. |
| Before | Forgotten: you deleted a memory from this exchange, so I no longer read it. |
| After | You deleted a memory from this exchange, so I no longer read it. |
| Before (delete everything) | [x] I understand |
| After | [x] I understand this can't be undone |
| After (new) | Everything is deleted. I start from a blank page with you. |

- Deleting is the most important control a member has, and it was the only action with no confirmation at all.
- "I won't use it again" is the promise the member actually cares about, and the code keeps it: the memory is removed, and so is the exchange it came from from the model's context.
- A checkbox that says only "I understand" lets someone tick it without reading. Naming the consequence in the checkbox itself means anyone who ticks it has read it.
- The "delete everything" caption now says what is kept: "only the record that you agreed to them". A member who reads "everything" and later finds a consent record would feel lied to.

**Deleting a memory another member already saw** (built in c2). Kept as is, because it already follows the rules:

> You shared this in an intro, so Ana Souza (Thu 19 Nov) already saw it. Deleting removes it from the intro and from my memory, but can't take back what they read. They won't be told. · [Delete anyway] [Keep it]

### 3. Consent request

**The requester (A), before anyone is asked:**

| | Text |
| --- | --- |
| Before | Before I ask Ana, choose what they can see about you: |
| After | Choose what Ana will see about you if you go ahead. Nothing else is shared. |
| Before | Waiting for you · until Mon 23 Nov |
| After | Ana isn't told anything until you select Ask Ana. Answer by Mon 23 Nov, or the request closes. |
| Before | [Ask Ana] [Someone else] [Not now] |
| After | [Ask Ana] [Suggest someone else] [Close request] |

- "Nothing else is shared" is the sentence the discovery asked for ("I want to know who and why first").
- The caption answers the question every requester has at this step: does Ana already know?
- "Not now" promised a later that doesn't exist, because the button ends the request. "Close request" is what it does.
- "Waiting for you until" didn't say what happens on that date. Now it does.

**The other member (B):**

| | Text |
| --- | --- |
| Before | **Roberto Silva, CEO at Metalúrgica Silva** would like to meet you. They shared: |
| After | **Roberto Silva, CEO at Metalúrgica Silva** would like to meet you. They chose to share: |
| Before | If you accept, choose what they can see about you: |
| After | If you accept, Roberto will see what you check below. Nothing else about you is shared. |
| Before | Waiting for you · until Mon 23 Nov |
| After | If you decline, Roberto only sees that the intro won't go ahead, not why. Answer by Mon 23 Nov, or the request closes. |
| Before | [Accept] [Decline] |
| After | [Accept and share] [Decline] |

- "They chose to share" tells B that A made a deliberate choice, which is the point of the consent design.
- Declining socially is the hardest part, so the card says up front that a decline stays private. The neutral "won't go ahead" notice already existed; B just didn't know about it.
- "Accept and share" names both things the button does.

**When an intro ends:**

| | Text |
| --- | --- |
| Before | This intro won't go ahead. |
| After | This intro won't go ahead. You can ask the concierge for another one anytime. |

- The wording stays neutral on purpose: declined, expired and cancelled read the same, so nobody learns the reason (model.md). The new sentence adds the next step.

### 4. Identity error

**The reply an unknown email address receives** (fixed text, never written by the model):

| | Text |
| --- | --- |
| Before | Thanks for your message. We couldn't match this address to a member account, so we haven't used any account information to reply. If you're a member, please write from the email registered with the community, or ask your relationship manager to add this address. |
| After | Thanks for your message. This address isn't registered to a member account, so this reply doesn't use any account information. If you're a member, write from the email you registered with the community, or ask your relationship manager to add this address. |

- "We couldn't match" sounds like the system failed. "Isn't registered" is the actual fact, and it points the sender at the fix.

**What the member sees in the simulated inbox:**

| | Text |
| --- | --- |
| Before | x@y.com is not a verified address, so no account data was used. |
| After | x@y.com isn't registered to any member, so the reply below uses no account information. If it's yours, ask your relationship manager to add it. |
| Before | Reply sent to roberto@… It is also in your timeline. |
| After | This is the reply roberto@… would receive. It's also in your timeline. |
| Before | That address belongs to another member. You can simulate emails from your own addresses or from an address nobody registered. |
| After | That address belongs to another member, so it can't be used here. Send from one of your addresses, or from an address nobody registered. |

- "Verified" is our word; the member only knows whether they registered an address.
- "Reply sent" was false: the prototype sends nothing. A demo that overclaims is the fastest way to lose an interviewer's trust.

**Login.** Kept: "Email or password is incorrect." It deliberately doesn't say which one, so nobody can learn which addresses have accounts. "Too many attempts. Try again in 30 seconds." already says what to do.

### 5. Temporary failure

| | Text |
| --- | --- |
| Before | The concierge couldn't reply just now. Your message is saved, so you don't need to type it again. Use Try again under it. |
| After | Your message is saved, but the concierge couldn't reply. Select Try again under your message; you don't need to type it again. |
| Before (box under the message) | I haven't replied to your last message yet. It's saved, so you don't need to type it again. |
| After | Your last message has no reply yet. It's saved, so you don't need to type it again. |
| Before (second failure) | Still couldn't reply. Your message is still saved; try again in a minute. |
| After | Still no reply. Your message is saved; try again in a minute. |
| Before (intro step) | The assistant couldn't finish that step, so nothing changed. Try again in a minute. |
| After | That step didn't finish, so nothing changed. Try again in a minute. |

- Rule 2: the member's first worry is losing what they wrote, so that comes first.
- "Just now" adds nothing. "Use" became "Select", which works for mouse, touch and keyboard.
- The box under the message spoke as "I", while the error above it spoke as "the concierge". It now uses the app voice (rule 6).
- "The assistant" was a third name for the same thing. It is gone.

**Intro conflicts** (two people acting on the same intro):

| | Text |
| --- | --- |
| Before | This intro is awaiting b, so that action isn't available. |
| After | This intro is now at "Waiting for the other member", so that action isn't available anymore. |
| Before | This intro changed while you were looking at it. Please review it again. |
| After | Someone updated this intro while you had it open, so nothing changed. Check its new status and try again. |

- The first one showed an internal state name to members. It now uses the same label as the cards, and a test checks that internal names never appear.
- The second one now says that nothing changed. Without that, the member doesn't know whether their click counted.

## Other changes

| Where | Before | After | Why |
| --- | --- | --- | --- |
| Under a reply | Used: *Prefers meetings before 9am* | This reply used: *Prefers meetings before 9am* | "Used" alone didn't say used by what. |
| Empty memory panel | Nothing saved yet. As we talk, I'll save durable facts you tell me, and you can undo any of them. | Nothing saved yet. When you tell me something worth keeping, like a goal or a preference, I'll save it and show you, so you can undo it. | "Durable facts" is our policy term. An example works better than a definition. |
| Visibility choice | Who can it help? Only my conversations / Intros I approve | Where can I use it? Private: only in my conversations / Shareable: also in intros I approve | The options now carry the level names shown on every memory, and "also" makes clear that Shareable includes Private. |
| Consent checklist, nothing to offer | Only memories marked Shareable can be offered. You can change that in your memory panel. | Only Shareable memories can be offered. You can make a memory Shareable in What I know about you. | The panel's real name (rule 5). |
| Stale banner | 1 memory has not been confirmed in 6 months. Check that they're still true. | 1 memory not confirmed in 6 months. Check that it is still true. | Fixes singular and plural. |
| After an intro request | Intro request created. Follow it in the Pending tab. | Intro requested. Your relationship manager will look for the right person; follow it in Pending. | Says who acts next. |
| New action item | Added to your pending items: … | Added to Pending: … | The tab's name. |
| Intro searching | Looking for the right person. Your relationship manager confirms the match. | … confirms the match before anyone is asked. | Answers "has anyone been contacted?" |
| Demo clock | Moved to Tue 24 Nov. 1 intro updates, 1 reminders. | It's now Tue 24 Nov. 1 intro update, 1 reminder sent. | Plurals were wrong. |
| Notifications | Reminder: an intro is waiting for you until 2026-11-23. | Reminder: an intro is waiting for you until Mon 23 Nov. Open Pending to answer. | Dates match the rest of the app; adds the next step. |
| Manager metric | Unknown senders in quarantine | Emails from unknown senders (with a help tooltip) | "Quarantine" is jargon. The tooltip explains what happens to them. |
| Manager caption | You never see the content of member conversations. | You never see what members tell the concierge. | Plain words. |
| Pause on | Memory is paused. I won't save anything new. | … until you turn it back on. | Says how to undo. |
| Login (new) | | This is a public prototype. Don't type real personal or company information: anyone with a demo account can read what's saved in it. | The deployed demo shares one password, so this is the honest warning (agreed in c2). |

## Kept on purpose

- **"What I know about you"** names the panel from the member's side, not ours ("Memory").
- **"Won't go ahead"** is neutral for every unsuccessful intro (see above).
- **"These two members can't be introduced right now."** is shown to the manager when a pair is blocked. It is vague because the reason (a decline in the last 90 days) belongs to a member.
- **"Thinking..."** is the spinner text. It is short, and every reply takes about 3 seconds (eval report).

## Still open

- **When memory extraction fails, the member isn't told.** The reply still arrives, but nothing says the message wasn't checked for things to remember. Proposed text: "I couldn't check this message for things to remember. Nothing new was saved." This needs a flag from the orchestrator, so it was left for later (edge_cases.md, E7).
- **The concierge's own replies** follow the system prompt, not this document. The evals check the most important cases: no "noted" when nothing was saved (4 of 160), and no invented events (0 of 10).
