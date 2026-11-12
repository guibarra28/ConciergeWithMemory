Ready-made discovery (fictional)
The main finding: members accept being remembered as long as they can see what was stored and delete it. The biggest operational bottleneck is member-to-member introductions, done by hand today with no recorded consent.
Client context. A paid community of about 1,200 members (CEOs, investors and C-level executives), with monthly in-person events and a team of 6 relationship managers. Members reach the team by email and WhatsApp, and history is scattered across spreadsheets and personal inboxes.
Who we talked to
Person
Role
Key quote
Roberto
Manufacturing CEO, member for 2 years
Every time I talk to someone new on the team, I have to explain again who I am and what I'm looking for.
Ana
Retail COO, member for 8 months
I'm fine being introduced, but I want to know who and why first.
Juliana
Relationship manager
I make about 40 intros a month over WhatsApp and lose track of half of them.
Marcos
Community CTO
We can't have a leak. One member seeing another's data would end the brand.
Pain points
1. Members repeat context in every new conversation or channel.
2. Intro requests stall: about half get no follow-up after 2 weeks.
3. There is no record of who agreed to be introduced, which causes awkward cold contacts.
4. The same member writes from different emails (personal, work, assistant) and the team doesn't connect the threads.
5. Action items agreed in conversation (send a deck, book a meeting) get lost.
6. Information goes stale: members change companies or titles and nobody updates it.
Stated privacy limits
1. Nothing about personal life or health can be used in introductions.
2. What a member says privately cannot reach another member without explicit permission.
3. Community managers can see intro status, but not the content of private conversations.
4. Members want to delete everything at once.
5. Messages from executive assistants count as the member's only if the member registered that email.
Technical hypotheses
1. A subtle saved notice with Undo builds more trust and less friction than asking permission for every fact.
2. Filtering memories by permission before they reach the model removes most of the leakage risk.
3. An explicit state machine for intros reduces stalled requests, because each state has an owner and a deadline.
4. Resolving identity before any memory access prevents an unknown sender from seeing anyone's data.
Requirements
ID
Requirement
Priority
R1
Members can view, edit, delete and pause their memories
Must
R2
Chat and email from the same member form one timeline
Must
R3
Unknown senders access no memory
Must
R4
An intro only advances with recorded consent from both sides
Must
R5
Unanswered requests expire after 3 days with notice
Must
R6
Memories have visibility: private, shareable, internal
Must
R7
Action items extracted from conversation have an owner and due date
Should
R8
The agent flags memories that may be stale
Should
R9
Assistant emails count as the member's if registered
Out of scope for the day
Success metrics
Share of intros that reach a booked meeting, rate of undone or deleted memories (too high means the agent stores what it shouldn't), leaks in tests (target: zero), and conversations where the member had to repeat context.