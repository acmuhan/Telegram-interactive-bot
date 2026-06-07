# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram two-way customer-service bot (`python-telegram-bot` 22.x, async). Users DM the bot; each user's messages are forwarded into a dedicated **forum topic** inside an admin group. Admins reply in that topic and the bot relays the reply back to the user. Reply-context, media albums, captcha gating, rate limiting, and ban/broadcast admin tooling are layered on top.

## Commands

```bash
pip install -r requirements.txt   # install deps
python -m interactive_bot         # run the bot (reads ./.env)
```

- Config comes from a `.env` file in the repo root (copy `.env_example`). The package fails fast at import time if `BOT_TOKEN`, `ADMIN_GROUP_ID`, or `ADMIN_USER_IDS` are missing/malformed (see `interactive_bot/__init__.py`).
- Docker: `docker compose up -d` (mounts `./data` → `/app/data`).
- **No test suite and no linter config exist** in this repo. Don't claim tests pass — there are none to run.

## Architecture

Four source files carry everything; the rest is assets/docs.

- **`interactive_bot/__init__.py`** — loads `.env`, validates and exposes all config as module-level constants (`bot_token`, `admin_group_id`, `admin_user_ids`, feature flags, `DATABASE_URL`, etc.), and configures the shared `logger`. Imported for its side effects; everything downstream reads config from here.
- **`interactive_bot/__main__.py`** — the entire bot (~1100 lines): all sync DB helper functions, captcha logic, both forwarding directions, admin commands, and `build_application()` which wires up handlers. This is the file you'll edit most.
- **`db/database.py`** — SQLAlchemy 2.x `engine`, `Base`, and the `session_scope()` context manager. Note the cross-package import `from interactive_bot import DATABASE_URL`, so `interactive_bot` config must load before the DB layer.
- **`db/model.py`** — four tables (`User`, `MessageMap`, `ForumStatus`, `MediaGroupMessage`) plus `run_schema_migrations()`.
- `interactive_bot/utils.py` — generic job-queue helpers; largely not used by the current `__main__.py`.

### Handler routing is the map

`build_application()` (`interactive_bot/__main__.py`) is the routing table. Direction is decided by **chat-type filters**, not message content:
- `filters.ChatType.PRIVATE` → `forwarding_message_u2a` (user → admin)
- `filters.Chat([admin_group_id])` → `forwarding_message_a2u` (admin → user) and all admin commands
- `^captcha:` / `^admin:` callback-query patterns drive the inline-button flows.

Start here to understand or extend behavior.

## Conventions that will trip you up

- **All DB access is synchronous and must be wrapped.** Every DB helper uses `session_scope()` (a per-operation session — there is no shared global session). In async handlers, never call these helpers directly; always go through `db_call(fn, ...)`, which is `asyncio.to_thread`. Calling a sync DB helper inline in an async handler blocks the event loop.

- **Bidirectional message-ID mapping.** `MessageMap` stores user-side ↔ group-side message IDs for every relayed message. This is what makes "reply to a message" preserve context in both directions (`find_group_reply_id` / `find_user_reply_id`). Any new relay path should also write a `MessageMap` row, or replies will lose threading.

- **One forum topic per user, created lazily.** `User.message_thread_id` links a user to their admin-group topic. `ensure_user_topic()` creates it on demand, guarded by a per-user `asyncio.Lock` (`_topic_locks`) to avoid duplicate topics under concurrent messages. A topic is created **only after** the user passes captcha and sends their first real message — captcha button presses never create one.

- **Captcha state lives in `context.user_data`, not the DB.** `_new_captcha()` randomly picks one of three challenge types — `sort` (tap shuffled digits in ascending order; `_shuffled_unsorted()` guarantees the displayed order is never already sorted), `arith` (tap the answer to a small `+`/`-` problem), `pick` (tap a named digit). All three share one state shape (`options`, `solution`, `progress`) so the step-by-step verifier in `callback_query_vcode` is type-agnostic: single-tap types just have a 1-element `solution`. Exponential-backoff penalty on repeated failure. Only persists across restarts if `ENABLE_PICKLE_PERSISTENCE=TRUE`.

- **Media groups are debounced, not relayed inline.** Album items are saved to `MediaGroupMessage`, then a single `job_queue.run_once` job (delay `MEDIA_GROUP_DELAY`) copies them as a batch via `copy_messages`. Jobs are de-duplicated by name.

- **Thread lifecycle.** `ForumStatus` tracks `opened`/`closed`/`deleted`. Admins closing or reopening a forum topic produces Telegram service messages (`forum_topic_closed` / `forum_topic_reopened`) that `forwarding_message_a2u` intercepts to update status and notify the user. A `closed` thread blocks relaying in both directions. When `IDLE_CLOSE_HOURS > 0`, a `run_repeating` job (`_close_idle_topics`, hourly) closes `opened` topics whose user's `last_message_at` is older than the window. Users with `last_message_at IS NULL` are skipped so brand-new topics aren't closed.

- **User activity timestamps.** `User.first_seen_at` is set on first `update_user_db`; `User.last_message_at` is bumped by `touch_user_activity()` on every verified inbound user message (powers `/info`, `/stats`, and idle-close). Per-user message totals come from counting `MessageMap` rows, not a stored counter.

- **Schema migrations are hand-rolled and SQLite-only.** `run_schema_migrations()` runs on startup and does conservative `ALTER TABLE ... ADD COLUMN` for new nullable columns; it no-ops on non-SQLite. There is no Alembic. When you add a column to a model, also add the matching guarded `ALTER TABLE` here, or existing SQLite DBs won't get it.

- **Legacy misspellings are intentional.** Table name `formn_status` and the aliases `MediaGroupMesssage` / `FormnStatus` in `db/model.py` are kept for backwards compatibility with existing databases — don't "fix" them.

- **Editing inline-button messages goes through `_safe_edit_text`.** Telegram raises `BadRequest: Message is not modified` when an `edit_message_text` produces content+markup identical to the current message (tapping the same admin panel / captcha button twice, or double-tapping before the first edit lands). `_safe_edit_text(query, ...)` swallows exactly that error and re-raises any other `BadRequest`. Use it for every callback-query edit; never call `query.edit_message_text` directly.

- **Admin command surface.** Admin commands are registered both as handlers (in `build_application`, filtered to the admin group) **and** in the `/` menu (in `post_init`, scoped via `BotCommandScopeChat(chat_id=admin_group_id)`). When adding an admin command, update both places or it won't show in the menu. Private chats only ever see `/start`.

## Known repo discrepancies

- `db/database.py` imports `from interactive_bot import DATABASE_URL`, so the `interactive_bot` package (and its `.env` validation) must import successfully before any DB code runs. A config error surfaces at import time, not at first query.

## Security-sensitive flags (in `.env`)

- `DELETE_USER_MESSAGE_ON_CLEAR_CMD` — when TRUE, `/clear` also deletes the user's side of the chat. Default FALSE; keep it that way unless explicitly asked.
- `ENABLE_PICKLE_PERSISTENCE` — pickle is only safe from a trusted `data/` dir. Default FALSE.
- `DELETE_TOPIC_AS_FOREVER_BAN` — changes whether deleting a topic permanently blocks the user vs. lets a new session recreate it.
- `IDLE_CLOSE_HOURS` — hours of user silence before a session topic is auto-closed. `0` (default) disables the feature.
