# Memory policy

What the concierge remembers about a member, what it never remembers, how long a memory lasts, and how the member stays in control. The rules marked **(code)** are enforced in [app/memory.py](../app/memory.py), not left to the model.

## What it remembers

Durable facts the member **said about themselves**, in one of these categories:

| Category | Example |
| --- | --- |
| Role | "Is the CEO of Metalúrgica Silva" |
| Company | "Company has 400 employees" |
| Goal | "Wants to automate quality control this year" |
| Preference | "Does not want to be introduced to vendors" |
| Interest | "Interested in AI in operations" |
| Expertise | "Led an ERP migration" |

Every memory keeps the exact words it came from, the message and channel, and when it was saved, so the member can always see *why* the concierge knows something.

## What it never remembers

- **Anything inferred.** A memory is saved only if the member's own words supporting it are found in the message. **(code)** The model can propose; it cannot invent.
- **Low-confidence statements** ("maybe", "I might"). **(code)**
- **Sensitive facts without a yes.** Personal life (family, relationships, finances) and health are never saved automatically. The member is asked "Do you want me to remember it?" and only an explicit yes saves it. **(code)**
- **Facts about other people**, small talk, questions and one-off requests.
- **Anything from an unknown sender.** Unverified addresses never reach memory. **(code)**

## Visibility

| Visibility | Who can it help | Who sets it |
| --- | --- | --- |
| Private (default) | Only the member's own conversations | Default for everything |
| Shareable | Also intros the member approves, one by one | The member |
| Internal | Team logistics (e.g. event attendance) | The team only |

Sensitive memories are always private and can never be made shareable. **(code and database constraint)**

## How long it lasts

- Durable facts last until the member edits or deletes them.
- Temporary facts ("travelling less these months") get an expiry date and stop being used after it. **(code)**
- Memories not confirmed in 6 months are flagged in the panel with "Still true?", and the model is told they may be out of date, so it asks before relying on them (R8). This applies to every category, not only role and company. **(code)** Not built yet: holding a flagged memory back from intro matching until it is confirmed.
- When the member says something that makes a memory outdated ("I now run Aço Novo"), the old memory is updated in place with a "Updated · Undo" notice, and it becomes private again. **(code)**

## How the member controls it

| Control | What happens |
| --- | --- |
| **Undo** on the "Saved" notice | Deletes the memory right away. |
| **Edit** | Changes the wording or visibility; counts as a fresh confirmation. |
| **Delete** | Removes the memory row. The exchange it came from stays visible to the member but is no longer read by the concierge, so it is really forgotten. **(code)** |
| **Pause memory** | Nothing new is saved, sensitive confirmations included. Existing memories are still used. **(code)** |
| **Delete everything** | Removes all memories, action items and the whole conversation history. |
| **"Used:" under each reply** | Shows which memories shaped the answer, so wrong ones are easy to spot. |

## Why these rules

- Members accept being remembered if they can see and delete what was stored (discovery). Every control above maps to R1.
- Saving with a visible Undo, instead of asking before each fact, keeps the conversation natural (hypothesis H1). The undo rate is the alarm for an agent that saves too much (PRD metric).
- Rules that protect members live in code because a prompt can be ignored or manipulated; code can't.
