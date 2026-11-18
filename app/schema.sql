-- SQLite schema for Concierge with Memory.
-- Design notes and diagrams: docs/model.md

PRAGMA foreign_keys = ON;

-- People who use the product: members, relationship managers and guests.
CREATE TABLE IF NOT EXISTS members (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('member', 'manager', 'guest')),
    company         TEXT,
    title           TEXT,
    memory_paused   INTEGER NOT NULL DEFAULT 0 CHECK (memory_paused IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Channels the concierge listens on. New channels (e.g. WhatsApp) are rows, not schema changes.
CREATE TABLE IF NOT EXISTS channels (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE          -- 'chat', 'email'
);

-- Every address that resolves to a member. A member can have several (personal and work email).
-- Only verified identities resolve; anything else is an unknown sender (R3).
CREATE TABLE IF NOT EXISTS identities (
    id          INTEGER PRIMARY KEY,
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    channel_id  INTEGER NOT NULL REFERENCES channels(id),
    address     TEXT    NOT NULL,         -- stored lowercased and trimmed; exact match only
    verified    INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    is_primary  INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (channel_id, address)
);

-- One timeline per member across channels (R2).
-- Messages from unknown senders have member_id NULL and quarantined = 1.
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY,
    member_id        INTEGER REFERENCES members(id) ON DELETE CASCADE,
    channel_id       INTEGER NOT NULL REFERENCES channels(id),
    direction        TEXT    NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sender_address   TEXT,
    body             TEXT    NOT NULL,
    quarantined      INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
    used_memory_ids  TEXT,                -- JSON array of memory ids shown under an outbound reply
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (member_id IS NOT NULL OR quarantined = 1)
);
CREATE INDEX IF NOT EXISTS idx_messages_member_time ON messages (member_id, created_at);

-- Durable facts the member told the concierge (R1, R6, R8).
-- Deleting a memory removes the row; there is no soft delete.
CREATE TABLE IF NOT EXISTS memories (
    id                 INTEGER PRIMARY KEY,
    member_id          INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    content            TEXT    NOT NULL,
    category           TEXT    NOT NULL,  -- e.g. role, company, goal, preference, personal, health
    sensitive          INTEGER NOT NULL DEFAULT 0 CHECK (sensitive IN (0, 1)),
    visibility         TEXT    NOT NULL DEFAULT 'private'
                               CHECK (visibility IN ('private', 'shareable', 'internal')),
    confidence         REAL    NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source_message_id  INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    source_excerpt     TEXT,              -- the part of the message that justifies the memory
    channel_id         INTEGER REFERENCES channels(id),
    valid_until        TEXT,              -- optional expiry for time-bound facts
    last_confirmed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Sensitive categories are never shareable (privacy limit 1).
    CHECK (NOT (sensitive = 1 AND visibility = 'shareable'))
);
CREATE INDEX IF NOT EXISTS idx_memories_member ON memories (member_id, visibility);

-- Commitments extracted from conversations (R7).
CREATE TABLE IF NOT EXISTS action_items (
    id                 INTEGER PRIMARY KEY,
    member_id          INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,  -- whose conversation
    owner_id           INTEGER NOT NULL REFERENCES members(id),                    -- who must do it
    description        TEXT    NOT NULL,
    due_date           TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    source_message_id  INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at       TEXT
);

-- Current state of each introduction (R4, R5). History lives in workflow_events.
CREATE TABLE IF NOT EXISTS intro_workflows (
    id             INTEGER PRIMARY KEY,
    requester_id   INTEGER NOT NULL REFERENCES members(id),   -- member A
    target_id      INTEGER REFERENCES members(id),            -- member B, NULL until a match is proposed
    reason         TEXT    NOT NULL,                          -- why A wants the intro, in A's words
    state          TEXT    NOT NULL CHECK (state IN (
                       'requested', 'awaiting_a', 'awaiting_b', 'introduced', 'scheduled',
                       'follow_up', 'completed', 'no_match', 'declined', 'expired', 'cancelled')),
    owner_id       INTEGER REFERENCES members(id),            -- whose action is pending; NULL when terminal
    deadline_at    TEXT,                                      -- NULL when terminal
    shared_by_a    TEXT,                                      -- what A authorized B to see
    shared_by_b    TEXT,                                      -- what B authorized A to see
    meeting_at     TEXT,
    outcome        TEXT,                                      -- follow-up answer, or 'no answer'
    version        INTEGER NOT NULL DEFAULT 1,                -- optimistic lock: transitions must match it
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (target_id IS NULL OR target_id <> requester_id)
);
CREATE INDEX IF NOT EXISTS idx_intros_state_deadline ON intro_workflows (state, deadline_at);

-- Append-only log of every transition, including consent (R4).
-- Consent is the event 'a_consented' / 'b_consented' with what was shared in payload.
CREATE TABLE IF NOT EXISTS workflow_events (
    id               INTEGER PRIMARY KEY,
    workflow_id      INTEGER NOT NULL REFERENCES intro_workflows(id) ON DELETE CASCADE,
    event            TEXT    NOT NULL,
    from_state       TEXT,
    to_state         TEXT    NOT NULL,
    actor_id         INTEGER REFERENCES members(id),   -- NULL when the scheduler acted
    payload          TEXT,                             -- JSON; never message content
    idempotency_key  TEXT    NOT NULL UNIQUE,          -- a retried transition is not applied twice
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO channels (name) VALUES ('chat'), ('email');
