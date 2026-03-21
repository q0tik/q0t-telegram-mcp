# Task 2: Disambiguation — username in dialogs + dialog_id-based addressing

## Problem

When a user has multiple contacts with similar names (e.g. two "Adilet"), the AI assistant
has no way to tell them apart and sends messages to the wrong person.

Two root causes:

1. `tg_dialogs` returns `id` and `name` but **no `username`**. The AI can't distinguish
   `@adilet` from `@adilet_c` — they both appear as `"Adilet"`.

2. `tg_send`, `tg_dialog`, `tg_read` accept only `name: str`. The numeric `id` returned by
   `tg_dialogs` is **useless** — there's no way to pass it. Resolution goes through
   `get_entity(name)` which can silently pick the wrong entity when names collide.

   > **Note:** Telethon's `get_entity(str)` only understands usernames and phone numbers — it
   > does **not** resolve human display names like `"Алёна Любимая 😍"`. Passing a display name
   > will raise `ValueError` or `UsernameNotOccupiedError`. `name`-based resolution must
   > therefore scan the cached dialog list and match on the `name` field, then use the entity
   > object (or its ID) obtained from that scan.

## What to change

### 1. Add `username` to `Dialog` model (`models.py`)

```python
@dataclass(frozen=True)
class Dialog:
    id: int
    name: str
    username: str | None   # ← NEW: @handle without leading @, None for groups/channels without one
    unread_count: int
    is_user: bool
    is_group: bool
    is_channel: bool
    last_message: str | None
    date: str | None
```

### 2. Populate `username` in `client.py` → `get_dialogs()`

```python
Dialog(
    ...
    username=getattr(d.entity, "username", None),
    ...
)
```

### 3. Add `dialog_id` and `username` parameters to `tg_send`, `tg_dialog`, `tg_read` (`server.py`)

All three tools now accept three mutually-substitutable entity selectors. Exactly one must be
provided; passing all three is also valid — priority is applied (see §4).

```python
async def tg_send(ctx, text: str, dialog_id: int | None = None, username: str | None = None, name: str | None = None)
async def tg_dialog(ctx, dialog_id: int | None = None, username: str | None = None, name: str | None = None, limit: int = 20, offset: int = 0)
async def tg_read(ctx, dialog_id: int | None = None, username: str | None = None, name: str | None = None)
```

- `dialog_id` — numeric Telegram ID from `tg_dialogs`. Unambiguous, always preferred.
- `username` — Telegram @handle. Leading `@` is accepted but not required (strip it before calling `get_entity`).
- `name` — human display name (e.g. "Adilet"). Least reliable: may match the wrong entity when names collide.

Validation: raise `ValueError` if none of `dialog_id`, `username`, `name` is provided.

#### Tool docstrings / parameter descriptions (important for LLM UX)

MCP does not have a native OneOf / discriminated-union type for parameters, so the intent must
be expressed in plain-text descriptions. Each tool that accepts the three selectors **must**
include a docstring that makes the relationship explicit. Example for `tg_send`:

```python
"""Send a message to a Telegram dialog.

To identify the recipient, supply **exactly one** of the following (all three solve the same
problem — pick the most specific one you have):

- `dialog_id` (int) — numeric ID from `tg_dialogs`. Most reliable, no ambiguity.
- `username` (str) — Telegram @handle, e.g. `adilet_c` or `@adilet_c` (leading @ is optional).
- `name` (str) — display name, e.g. `"Adilet"`. Use only when dialog_id/username are unavailable;
  may resolve to the wrong contact when names are not unique.

Priority when multiple are given: dialog_id > username > name.
"""
```

Apply the same pattern (adapted for the tool's purpose) to `tg_dialog` and `tg_read`.

### 4. Update `_resolve_entity` in `client.py`

Add `username` as a middle tier. Strip a leading `@` before passing to `get_entity` so both
`"adilet_c"` and `"@adilet_c"` work identically.

```python
async def _resolve_entity(
    self,
    dialog_id: int | None = None,
    username: str | None = None,
    name: str | None = None,
) -> Any:
    # Priority: dialog_id > username > name
    if dialog_id is not None:
        try:
            return await self._client.get_entity(dialog_id)
        except Exception:
            raise ValueError(f"Dialog not found: id={dialog_id}")
    if username is not None:
        handle = username.lstrip("@")
        try:
            return await self._client.get_entity(handle)
        except (ValueError, UsernameNotOccupiedError):
            raise ValueError(f"Dialog not found: username={username}")
    if name is not None:
        # get_entity() does NOT accept display names — scan cached dialogs instead.
        async for dialog in self._client.iter_dialogs():
            if dialog.name == name:
                return dialog.entity
        raise ValueError(f"Dialog not found: name={name}")
    raise ValueError("One of dialog_id, username, or name must be provided")
```

Resolution priority: **`dialog_id` > `username` > `name`**.

## Invariants that must hold after this change

- `tg_dialogs` response always includes `username` field (may be `null`)
- Passing `dialog_id` to `tg_send`/`tg_dialog`/`tg_read` always resolves to the exact entity, no ambiguity
- Passing `username` (with or without leading `@`) resolves via Telegram handle
- `name`-only calls still work (backwards compatible) — resolved by scanning `iter_dialogs()`, not via `get_entity(str)`
- Passing none of `dialog_id`, `username`, `name` returns an error
- When multiple selectors are passed, priority is `dialog_id > username > name`
- Each tool's docstring clearly explains the three selectors and their priority
- Tests must not make real Telegram API calls

## Execution order

```
IMPLEMENTER
    └── modifies: src/tg_mcp/models.py, client.py, server.py
            │
            └── TESTER
                    └── adds/updates tests for new parameters and _resolve_entity logic
                            │
                            └── DOCUMENTER
                                    └── updates README.md tool table + docs/
```

## Agent tasks

### IMPLEMENTER
Apply the four changes described above. Do not touch anything else.
Run `pytest` before finishing — all tests must pass.

### TESTER
Add tests covering:
- `tg_dialogs` returns `username` field populated for users, `None` for groups without username
- `tg_send` / `tg_dialog` / `tg_read` work with `dialog_id` only
- `tg_send` / `tg_dialog` / `tg_read` work with `username` only (with and without leading `@`)
- `tg_send` / `tg_dialog` / `tg_read` work with `name` only (regression)
- Passing none of the three raises `ValueError`
- Priority: when `dialog_id` + `username` both given, `dialog_id` wins; when `username` + `name` given, `username` wins
- `_resolve_entity` calls `get_entity(int)` when `dialog_id` is given, `get_entity(str)` (stripped handle) for `username`

Coverage must stay above 80%.

### DOCUMENTER
Update `README.md`:
- Tool table: add `dialog_id` column / note to `tg_send`, `tg_dialog`, `tg_read`
- Tool table: add `username` to `tg_dialogs` response description
- Add a short "Disambiguation" section explaining when to use `dialog_id` vs `name`

Update `docs/` accordingly.
