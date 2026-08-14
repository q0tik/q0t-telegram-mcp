# Telegram MCP — Chat Folders (v0.11.0)

**Date:** 2026-08-14
**Status:** Approved (design)
**Base:** v0.10.0 (Telethon 1.44, TL layer 227, mcp 2.0.0)

## Overview

Telegram chat folders (TL: *dialog filters*) are the one piece of everyday client
state the server cannot touch. A model can read and write messages but cannot see
that "Work" holds twelve chats, cannot drop a new group into it, and cannot create
a folder to organise what it just found.

This spec adds six tools that cover what a person does by hand in the client:
list folders, create one, rename/retune it, delete it, and move chats in and out.

## Goals

- Full CRUD over the account's own folders, plus membership editing.
- Expose the auto-inclusion filters (contacts, groups, bots, channels) and the
  exclusion filters (muted, read, archived) as ordinary tool parameters.
- Folder contents come back with the same marked dialog IDs `tg_dialogs` returns,
  so the two tools compose without translation.
- Every Telegram limit error becomes a sentence a model can act on.

## Non-Goals

- **Shareable folders / invite links** (`DialogFilterChatlist`, Premium). They are
  listed with `kind: "shared"` and refuse writes with a clear message; creating
  links, joining by link, and `chatlists.*` RPCs are out of scope.
- **Reordering folders** (`UpdateDialogFiltersOrderRequest`).
- **Folder tags and colours** (`color`, `tags_enabled`).
- **Styled titles.** Titles are plain text; `TextWithEntities.entities` is written
  empty and dropped when reading.

## Telegram Facts This Rests On

Verified against the pinned Telethon 1.44:

- `functions.messages.GetDialogFiltersRequest()` → `types.messages.DialogFilters`
  with `.filters: list[TypeDialogFilter]` and `.tags_enabled`.
- `types.DialogFilter(id, title, pinned_peers, include_peers, exclude_peers,
  contacts, non_contacts, groups, broadcasts, bots, exclude_muted, exclude_read,
  exclude_archived, title_noanimate, emoticon, color)`.
- **`title` is `TextWithEntities(text, entities)`, not `str`** in this layer. Passing
  a bare string fails at serialisation. This is the single easiest thing to get wrong.
- `types.DialogFilterDefault()` — the "All chats" pseudo-folder. Carries no fields.
- `types.DialogFilterChatlist(id, title, pinned_peers, include_peers, has_my_invites, …)`
  — a shared folder. Note: **no `exclude_peers`, no auto-flags.**
- `functions.messages.UpdateDialogFilterRequest(id, filter=None)`:
  - `filter` set, `id` unused → create;
  - `filter` set, `id` existing → **full replace** (there is no partial update);
  - `filter=None` → delete.
- Folder IDs are chosen by the *client*, valid range 2–255 (0 and 1 are reserved
  for the built-in All/Archive views).
- `include_peers`/`exclude_peers`/`pinned_peers` are `InputPeer`s; `pinned_peers`
  must be a subset of `include_peers`.

## Tool Inventory (6 new, 19 total)

| Tool | Group | Signature → Return |
|------|-------|--------------------|
| `tg_folders` | READ | `(resolve_names=True)` → `list[Folder]` |
| `tg_folder_create` | WRITE | `(title, emoji?, chats?, contacts?, non_contacts?, groups?, broadcasts?, bots?, exclude_muted?, exclude_read?, exclude_archived?)` → `{ok, folder_id, folder}` |
| `tg_folder_update` | WRITE | `(folder_id? \| title?, new_title?, emoji?, <same eight flags>?)` → `{ok, folder}` |
| `tg_folder_delete` | WRITE | `(folder_id? \| title?)` → `{ok, deleted_id, deleted_title}` |
| `tg_folder_add_chats` | WRITE | `(chats, folder_id? \| title?)` → `{ok, added, skipped, folder}` |
| `tg_folder_remove_chats` | WRITE | `(chats, folder_id? \| title?)` → `{ok, removed, skipped, folder}` |

`tg_folders` joins `READ_TOOLS`; the other five join `WRITE_TOOLS`, so `TG_READONLY`
leaves folder listing available and folder editing gone.

### Addressing a folder

Exactly one of `folder_id` (preferred) or `title`, mirroring the dialog selector
convention. `title` matches case-insensitively after stripping whitespace. Two
folders with the same title → `ValueError` naming both IDs, never a silent pick of
the first. `title` matching `kind: "default"` (i.e. "All chats") is rejected: the
pseudo-folder is not editable.

### Addressing chats

`chats: list[str]`, each element one of:

| Form | Example | Resolved as |
|------|---------|-------------|
| Marked numeric ID | `"-1001234567890"` | `dialog_id` |
| `@handle` | `"@durov"` | `username` |
| Anything else | `"Team standup"` | `name` |

This is the one deliberate departure from the existing three-scalar-selector
pattern (`dialog_id`/`username`/`name`) — that pattern does not extend to a list.
Each element is turned into `_resolve_entity` kwargs by a pure function and
resolved through the existing machinery, so behaviour (including the `@`-in-`name`
fallback) is unchanged.

An element that does not resolve is reported in `skipped` with its reason; the
remaining chats are still applied. A wholly unresolvable list is an error.

### Patch semantics

`tg_folder_update` takes every mutable field as `Optional[...] = None`, where
`None` means *leave alone*. Implementation reads the current `DialogFilter`,
copies it field by field applying only what was passed, and sends the result.
Membership is never touched by `tg_folder_update` — that is what the add/remove
tools are for. Because Telegram only accepts a whole filter, a caller that forgot
a field would otherwise wipe it; this design makes that impossible.

`tg_folder_add_chats`/`tg_folder_remove_chats` read-modify-write the same way:
current peers in, merged/subtracted peers out. Removal drops the peer from
`pinned_peers` as well as `include_peers`, since a pinned peer outside the include
list is an invalid filter.

## Data Model (`models.py`)

```python
@dataclass(frozen=True)
class FolderChat:
    """One chat inside a folder."""
    dialog_id: int        # marked ID, same space as Dialog.id
    name: str | None      # None when the entity is not resolvable from cache


@dataclass(frozen=True)
class Folder:
    """A Telegram chat folder (TL: dialog filter). Returned by tg_folders."""
    id: int                       # 0 for the "All chats" pseudo-folder
    title: str
    kind: str                     # "default" | "folder" | "shared"
    emoji: str | None
    include: list[FolderChat]
    pinned: list[FolderChat]
    exclude: list[FolderChat]
    contacts: bool
    non_contacts: bool
    groups: bool
    broadcasts: bool
    bots: bool
    exclude_muted: bool
    exclude_read: bool
    exclude_archived: bool
```

`kind: "default"` returns `id: 0`, `title: "All chats"`, empty lists, all flags
`False`. `kind: "shared"` fills what a `DialogFilterChatlist` carries and leaves
`exclude` empty with all flags `False` — the type has no such fields.

## New Module: `folders.py`

Pure, synchronous, network-free — testable in full, exactly like `media.py`.
`client.py` is already 688 lines; none of this logic belongs there.

```python
FOLDER_ID_MIN = 2
FOLDER_ID_MAX = 255

def next_free_id(taken: Iterable[int]) -> int
    # smallest unused ID in [2, 255]; raises ValueError when full

def filter_kind(f) -> str
    # "default" | "shared" | "folder"

def filter_title(f) -> str
    # unwraps TextWithEntities; tolerates a plain str from older layers

def to_folder(f, names: dict[int, str | None]) -> Folder
    # TL object + resolved names -> dataclass; uses telethon.utils.get_peer_id

def peer_ids(peers) -> list[int]
    # marked IDs, order preserved

def merge_peers(current, added) -> list
    # append, dedupe by marked ID, keep original order

def subtract_peers(current, remove_ids: set[int]) -> list

def parse_chat_ref(raw: str) -> dict[str, Any]
    # "-100…" -> {"dialog_id": int}; "@x" -> {"username": …}; else {"name": …}

def patched_filter(existing, **changes)
    # copy of a DialogFilter with only the non-None changes applied

def has_any_source(include_count: int, flags: Mapping[str, bool]) -> bool
    # a filter with no peers and no inclusion flag is rejected by Telegram

def friendly_error(exc: Exception) -> str | None
    # RPC error -> plain sentence, or None when it is not one we recognise
```

### Error translation

| Telegram error | Message returned |
|----------------|------------------|
| `FILTER_ID_INVALID` | `Folder not found: id=<n>. Call tg_folders for the current list.` |
| `FILTERS_TOO_MUCH` | `Telegram folder limit reached (10 without Premium, 20 with). Delete a folder first.` |
| `FILTER_INCLUDE_EMPTY` | `A folder needs at least one chat or one of contacts/non_contacts/groups/broadcasts/bots.` |
| `FILTER_TITLE_EMPTY` | `Folder title must not be empty.` |
| `CHATLISTS_TOO_MUCH` | `Telegram shared-folder limit reached.` |

Unrecognised errors keep the existing `_error_message` path (which already
truncates the pathological `TypeNotFoundError` payload).

The "would leave the folder empty" case is caught locally rather than by
round-tripping to Telegram: removing the last chat from a folder with no
inclusion flags returns `Removing these chats would leave "<title>" with no
source. Delete the folder with tg_folder_delete instead.`

## Client Methods (`client.py`)

Thin async wrappers; all arithmetic lives in `folders.py`.

```python
async def get_folders(self, resolve_names: bool = True) -> list[Folder]
async def create_folder(self, title, emoji, chats: list[str], **flags) -> Folder
async def update_folder(self, folder_id, title, new_title, emoji, **flags) -> Folder
async def delete_folder(self, folder_id, title) -> tuple[int, str]
async def add_folder_chats(self, chats, folder_id, title) -> tuple[Folder, list, list]
async def remove_folder_chats(self, chats, folder_id, title) -> tuple[Folder, list, list]
```

Two private helpers:

- `_find_filter(folder_id, title)` — fetches the filter list once, resolves the
  selector, raises the disambiguation errors described above.
- `_resolve_peers(chats)` → `(input_peers, skipped)` — maps each ref through
  `parse_chat_ref` → `_resolve_entity` → `get_input_entity`, collecting failures
  rather than aborting.

Name resolution in `get_folders` is best-effort: each peer goes through
`get_entity` inside a `try`, and a failure yields `name: None`. `resolve_names=False`
skips it entirely for a cheap, purely-ID listing.

## Testing (`tests/test_folders.py`, plus edits)

No network, per the project rule. The pure module carries the weight:

- `next_free_id` — empty set → 2; gaps filled ascending; full range → `ValueError`.
- `filter_title` — `TextWithEntities` and bare-`str` inputs.
- `filter_kind` — all three TL types.
- `to_folder` — marked IDs match `telethon.utils.get_peer_id`; missing names → `None`;
  `DialogFilterDefault` → `id: 0`; `DialogFilterChatlist` → `kind: "shared"`, empty
  `exclude`.
- `merge_peers` — dedupe by marked ID across differing `InputPeer` instances, order kept.
- `subtract_peers` — removes from include and pinned.
- `parse_chat_ref` — the three forms, including a negative channel ID and a bare
  name containing digits.
- `patched_filter` — `None` leaves a field untouched; `False` is applied, not skipped
  (the classic `if flag:` bug).
- `has_any_source`, `friendly_error` — each mapped code.

Client methods are tested against a fake Telethon client (existing `conftest.py`
fixtures) asserting the exact `UpdateDialogFilterRequest` sent: create picks a free
ID, delete sends `filter=None`, update preserves untouched fields, remove strips
pinned peers.

`test_server.py` gains the six tools in its registration check (a test already fails
for a tool in neither `READ_TOOLS` nor `WRITE_TOOLS`); `test_readonly.py` asserts
`tg_folders` survives `TG_READONLY` and the five writers do not.

## Documentation

`README.md` gains a folders section in the tool table with the `chats` list format
spelled out, and `CHANGELOG.md` a v0.11.0 entry. Per CLAUDE.md, a DOCUMENTER agent
runs after implementation to sync `README.md` and `docs/`.

## Risks

- **Read-modify-write races.** Two clients editing the same folder concurrently:
  last write wins, as it does in the official clients. Not mitigated.
- **Whole-filter replacement.** Any field this spec does not model (`color`,
  `title_noanimate`, `has_my_invites`) must be carried through untouched on update,
  or editing a folder from here would silently strip settings made in the app.
  `patched_filter` copies the source object rather than building a fresh one, which
  is what makes this safe; a test pins it.
- **Name resolution cost.** `get_folders(resolve_names=True)` may trigger
  `users.GetUsers` for peers absent from the session cache. Bounded by folder
  membership (≤100 per folder without Premium) and opt-out via the parameter.
