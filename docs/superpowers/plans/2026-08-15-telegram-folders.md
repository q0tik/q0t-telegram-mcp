# Telegram Chat Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the MCP server six tools that list, create, retune, delete, and re-populate Telegram chat folders.

**Architecture:** All arithmetic — folder-ID allocation, title unwrapping, peer merging, patch semantics, error translation — lives in a new pure module `src/tg_mcp/folders.py`, exactly as `media.py` holds album grouping. `client.py` gains thin async methods that fetch the filter list, hand it to those functions, and send the result back with `UpdateDialogFilterRequest`. `server.py` gains six tool functions in the established shape (`Annotated` params, try/except returning `{"error": …}`).

**Tech Stack:** Python 3.11+, Telethon 1.44.0 (pinned), mcp 2.0.0 (pinned), pytest + pytest-asyncio, `uv` as package manager.

**Spec:** `docs/superpowers/specs/2026-08-14-telegram-folders-design.md`

## Global Constraints

- **Type hints everywhere.** Every function signature, including test helpers where it aids clarity.
- **All code is async** where it touches Telethon; `folders.py` is deliberately synchronous and network-free.
- **Tests must not make real network calls.** Use the `conftest.py` fixtures; `mock_telegram_client` is `AsyncMock(spec_set=TelegramClient)` — touching a method Telethon does not have is an `AttributeError`, and that is intentional.
- **Never commit until all tests pass.** Run `uv run pytest` before every commit step.
- **`DialogFilter.title` is `TextWithEntities`, not `str`** in TL layer 227. Always build it with `folders.title_value()`.
- **Folder IDs are client-chosen**, valid range 2–255. 0 and 1 are reserved.
- **Every new tool must be listed in `READ_TOOLS` or `WRITE_TOOLS`** in `server.py` — `tests/test_readonly.py::TestToolPartition::test_every_tool_is_classified_exactly_once` fails otherwise.
- Run commands with `uv run` (e.g. `uv run pytest tests/test_folders.py -v`).

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/tg_mcp/folders.py` | create | Pure helpers over `DialogFilter`: IDs, titles, kinds, peers, patching, error text |
| `src/tg_mcp/models.py` | modify | `+FolderChat`, `+Folder` dataclasses |
| `src/tg_mcp/client.py` | modify | Six async wrapper methods + three private helpers |
| `src/tg_mcp/server.py` | modify | Six tool functions, `_folder_error`, `READ_TOOLS`/`WRITE_TOOLS` entries |
| `tests/test_folders.py` | create | Everything in `folders.py`, plus the client methods against the mock |
| `tests/test_readonly.py` | modify | Tool-name sets grow by six |
| `README.md`, `CHANGELOG.md`, `pyproject.toml` | modify | Docs and the 0.11.0 version bump |

---

### Task 1: Folder dataclasses and the first pure helpers

**Files:**
- Create: `src/tg_mcp/folders.py`
- Modify: `src/tg_mcp/models.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `models.FolderChat`, `models.Folder`; `folders.FOLDER_ID_MIN/MAX`, `folders.KIND_DEFAULT/KIND_FOLDER/KIND_SHARED`, `folders.INCLUSION_FLAGS`, `folders.EXCLUSION_FLAGS`, `folders.ALL_FLAGS`, `folders.next_free_id(taken) -> int`, `folders.filter_kind(f) -> str`, `folders.filter_title(f) -> str`, `folders.title_value(text) -> TextWithEntities`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_folders.py`:

```python
"""Tests for chat-folder helpers. No network: every Telethon object is built by hand."""

from __future__ import annotations

import pytest
from telethon.tl import types

from tg_mcp import folders
from tg_mcp.models import Folder, FolderChat


def make_filter(
    folder_id: int = 2,
    title: str = "Work",
    include: list | None = None,
    pinned: list | None = None,
    exclude: list | None = None,
    **flags,
) -> types.DialogFilter:
    """A real DialogFilter, not a mock — the whole point is the TL field shapes."""
    return types.DialogFilter(
        id=folder_id,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=pinned or [],
        include_peers=include or [],
        exclude_peers=exclude or [],
        **flags,
    )


class TestNextFreeId:
    def test_empty_account_starts_at_two(self) -> None:
        assert folders.next_free_id([]) == folders.FOLDER_ID_MIN == 2

    def test_fills_the_lowest_gap(self) -> None:
        assert folders.next_free_id([2, 3, 5]) == 4

    def test_appends_when_contiguous(self) -> None:
        assert folders.next_free_id([2, 3, 4]) == 5

    def test_ignores_reserved_ids(self) -> None:
        """0 and 1 are the built-in All/Archive views and are never candidates."""
        assert folders.next_free_id([0, 1]) == 2

    def test_full_range_raises(self) -> None:
        with pytest.raises(ValueError, match="No free folder ID"):
            folders.next_free_id(range(folders.FOLDER_ID_MIN, folders.FOLDER_ID_MAX + 1))


class TestFilterKind:
    def test_plain_folder(self) -> None:
        assert folders.filter_kind(make_filter()) == folders.KIND_FOLDER

    def test_default_pseudo_folder(self) -> None:
        assert folders.filter_kind(types.DialogFilterDefault()) == folders.KIND_DEFAULT

    def test_shared_chatlist(self) -> None:
        shared = types.DialogFilterChatlist(
            id=7, title=types.TextWithEntities(text="Team", entities=[]),
            pinned_peers=[], include_peers=[],
        )
        assert folders.filter_kind(shared) == folders.KIND_SHARED


class TestFilterTitle:
    def test_unwraps_text_with_entities(self) -> None:
        assert folders.filter_title(make_filter(title="Work")) == "Work"

    def test_tolerates_a_bare_string(self) -> None:
        """Older TL layers sent title as a plain str; a session upgraded in place
        can still hand us one."""
        legacy = make_filter()
        legacy.title = "Legacy"
        assert folders.filter_title(legacy) == "Legacy"

    def test_default_folder_has_a_readable_name(self) -> None:
        assert folders.filter_title(types.DialogFilterDefault()) == "All chats"


class TestTitleValue:
    def test_wraps_plain_text(self) -> None:
        wrapped = folders.title_value("Work")
        assert isinstance(wrapped, types.TextWithEntities)
        assert wrapped.text == "Work"
        assert wrapped.entities == []


class TestFolderDataclass:
    def test_defaults_are_empty(self) -> None:
        folder = Folder(id=0, title="All chats", kind=folders.KIND_DEFAULT)
        assert folder.include == [] and folder.pinned == [] and folder.exclude == []
        assert folder.contacts is False and folder.exclude_muted is False

    def test_folder_chat_name_is_optional(self) -> None:
        assert FolderChat(dialog_id=-100123).name is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tg_mcp.folders'`.

- [ ] **Step 3: Add the dataclasses**

In `src/tg_mcp/models.py`, change the import line to `from dataclasses import dataclass, field` and append:

```python
@dataclass(frozen=True)
class FolderChat:
    """One chat inside a folder.

    ``dialog_id`` is a marked ID — the same value :class:`Dialog` carries, so a
    folder listing and a dialog listing can be joined without translation.
    """

    dialog_id: int
    name: str | None = None   # None when the entity is not resolvable from cache


@dataclass(frozen=True)
class Folder:
    """A Telegram chat folder (TL: dialog filter).

    Returned by :func:`tg_folders`. ``kind`` is one of:

    ``default``
        The built-in "All chats" view. Carries ``id: 0`` and nothing else; not editable.
    ``folder``
        An ordinary folder owned by this account.
    ``shared``
        A folder joined from an invite link (``DialogFilterChatlist``). The TL type
        has no exclusions and no auto-flags, so those come back empty/False.
    """

    id: int
    title: str
    kind: str
    emoji: str | None = None
    include: list[FolderChat] = field(default_factory=list)
    pinned: list[FolderChat] = field(default_factory=list)
    exclude: list[FolderChat] = field(default_factory=list)
    contacts: bool = False
    non_contacts: bool = False
    groups: bool = False
    broadcasts: bool = False
    bots: bool = False
    exclude_muted: bool = False
    exclude_read: bool = False
    exclude_archived: bool = False
```

- [ ] **Step 4: Create `folders.py` with the first helpers**

```python
"""Pure helpers for Telegram chat folders (TL: dialog filters).

Telegram has no partial update for a folder: ``UpdateDialogFilterRequest``
replaces the whole ``DialogFilter``. Everything that decides *what* the
replacement should contain — which ID a new folder gets, how a title is
unwrapped, which peers survive an edit — is arithmetic over TL objects with no
network in sight, so it lives here rather than in ``client.py``, and is tested
directly.

The one trap worth stating up front: in TL layer 227 ``DialogFilter.title`` is a
``TextWithEntities``, not a ``str``. Build it with :func:`title_value`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from telethon.tl import types

FOLDER_ID_MIN = 2
FOLDER_ID_MAX = 255

#: The built-in "All chats" view. Telegram sends it as a fieldless
#: DialogFilterDefault; we give it a stable ID and name so a listing is uniform.
DEFAULT_FOLDER_ID = 0
DEFAULT_FOLDER_TITLE = "All chats"

KIND_DEFAULT = "default"
KIND_FOLDER = "folder"
KIND_SHARED = "shared"

#: Auto-inclusion flags: each pulls a whole category of chats into the folder.
INCLUSION_FLAGS = ("contacts", "non_contacts", "groups", "broadcasts", "bots")
#: Exclusion flags: each removes chats that would otherwise be shown.
EXCLUSION_FLAGS = ("exclude_muted", "exclude_read", "exclude_archived")
ALL_FLAGS = INCLUSION_FLAGS + EXCLUSION_FLAGS


def next_free_id(taken: Iterable[int]) -> int:
    """Smallest unused folder ID in [2, 255].

    Telegram lets the *client* pick the ID, and reuses nothing: 0 and 1 are the
    built-in All and Archive views, so a fresh account's first folder is 2.
    """
    used = {i for i in taken if isinstance(i, int)}
    for candidate in range(FOLDER_ID_MIN, FOLDER_ID_MAX + 1):
        if candidate not in used:
            return candidate
    raise ValueError(f"No free folder ID left in {FOLDER_ID_MIN}-{FOLDER_ID_MAX}")


def filter_kind(f: Any) -> str:
    """Classify a TL dialog filter as default, shared, or an ordinary folder."""
    if isinstance(f, types.DialogFilterDefault):
        return KIND_DEFAULT
    if isinstance(f, types.DialogFilterChatlist):
        return KIND_SHARED
    return KIND_FOLDER


def filter_title(f: Any) -> str:
    """Plain-text title of a filter.

    Unwraps ``TextWithEntities`` but still accepts a bare ``str``: a session
    written by an older layer can hand one back.
    """
    if isinstance(f, types.DialogFilterDefault):
        return DEFAULT_FOLDER_TITLE
    title = getattr(f, "title", None)
    if title is None:
        return ""
    text = getattr(title, "text", title)
    return text if isinstance(text, str) else ""


def title_value(text: str) -> types.TextWithEntities:
    """Wrap a plain title the way layer 227 wants it. Styling is not supported."""
    return types.TextWithEntities(text=text, entities=[])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS, all of them.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — nothing else touches these names yet.

- [ ] **Step 7: Commit**

```bash
git add src/tg_mcp/folders.py src/tg_mcp/models.py tests/test_folders.py
git commit -m "feat: folder dataclasses and dialog-filter identity helpers"
```

---

### Task 2: Peer helpers

**Files:**
- Modify: `src/tg_mcp/folders.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: Task 1's constants.
- Produces: `folders.peer_key(peer, self_id=None) -> int | None`, `folders.peer_ids(peers, self_id=None) -> list[int]`, `folders.merge_peers(current, added, self_id=None) -> list`, `folders.subtract_peers(current, remove_ids, self_id=None) -> list`, `folders.parse_chat_ref(raw) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
def user_peer(user_id: int, access_hash: int = 1) -> types.InputPeerUser:
    return types.InputPeerUser(user_id=user_id, access_hash=access_hash)


def channel_peer(channel_id: int, access_hash: int = 1) -> types.InputPeerChannel:
    return types.InputPeerChannel(channel_id=channel_id, access_hash=access_hash)


class TestPeerKey:
    def test_user_peer_keeps_its_id(self) -> None:
        assert folders.peer_key(user_peer(42)) == 42

    def test_channel_peer_is_marked(self) -> None:
        assert folders.peer_key(channel_peer(777)) == -1000000000777

    def test_chat_peer_is_negated(self) -> None:
        assert folders.peer_key(types.InputPeerChat(chat_id=55)) == -55

    def test_saved_messages_resolves_to_the_account_id(self) -> None:
        """Saved Messages sits in folders as InputPeerSelf, which get_peer_id
        refuses to cast. Without the account ID there is nothing to return."""
        assert folders.peer_key(types.InputPeerSelf(), self_id=999) == 999
        assert folders.peer_key(types.InputPeerSelf()) is None


class TestPeerIds:
    def test_order_is_preserved(self) -> None:
        peers = [user_peer(3), user_peer(1), user_peer(2)]
        assert folders.peer_ids(peers) == [3, 1, 2]

    def test_unkeyable_peers_are_dropped(self) -> None:
        assert folders.peer_ids([user_peer(3), types.InputPeerSelf()]) == [3]

    def test_none_is_empty(self) -> None:
        assert folders.peer_ids(None) == []


class TestMergePeers:
    def test_appends_new_peers(self) -> None:
        merged = folders.merge_peers([user_peer(1)], [user_peer(2)])
        assert folders.peer_ids(merged) == [1, 2]

    def test_dedupes_across_distinct_instances(self) -> None:
        """The same chat arrives with a different access_hash than the one stored
        in the filter; identity is the marked ID, not object equality."""
        merged = folders.merge_peers([user_peer(1, access_hash=111)], [user_peer(1, access_hash=222)])
        assert folders.peer_ids(merged) == [1]

    def test_existing_order_is_untouched(self) -> None:
        merged = folders.merge_peers([user_peer(5), user_peer(4)], [user_peer(4), user_peer(6)])
        assert folders.peer_ids(merged) == [5, 4, 6]


class TestSubtractPeers:
    def test_removes_by_marked_id(self) -> None:
        remaining = folders.subtract_peers([user_peer(1), channel_peer(777)], {-1000000000777})
        assert folders.peer_ids(remaining) == [1]

    def test_missing_ids_are_a_no_op(self) -> None:
        remaining = folders.subtract_peers([user_peer(1)], {999})
        assert folders.peer_ids(remaining) == [1]


class TestParseChatRef:
    def test_positive_id(self) -> None:
        assert folders.parse_chat_ref("12345") == {"dialog_id": 12345}

    def test_marked_channel_id(self) -> None:
        assert folders.parse_chat_ref("-1001234567890") == {"dialog_id": -1001234567890}

    def test_handle(self) -> None:
        assert folders.parse_chat_ref("@durov") == {"username": "durov"}

    def test_display_name(self) -> None:
        assert folders.parse_chat_ref("Team standup") == {"name": "Team standup"}

    def test_name_with_digits_is_not_an_id(self) -> None:
        assert folders.parse_chat_ref("Room 101") == {"name": "Room 101"}

    def test_surrounding_whitespace_is_stripped(self) -> None:
        assert folders.parse_chat_ref("  @durov  ") == {"username": "durov"}

    def test_empty_reference_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty chat reference"):
            folders.parse_chat_ref("   ")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v -k "PeerKey or PeerIds or Merge or Subtract or ParseChatRef"`
Expected: FAIL — `AttributeError: module 'tg_mcp.folders' has no attribute 'peer_key'`.

- [ ] **Step 3: Implement**

Add `import re` and `from collections.abc import Iterable, Sequence` to the imports in `folders.py`, plus `import telethon.utils`, then append:

```python
def peer_key(peer: Any, self_id: int | None = None) -> int | None:
    """Marked ID for *peer*, or None when it cannot be expressed as one.

    ``InputPeerSelf`` is the case that matters: Saved Messages is a perfectly
    ordinary folder member, and ``get_peer_id`` raises on it because the ID is
    not in the object — it is whoever is logged in.
    """
    if isinstance(peer, types.InputPeerSelf):
        return self_id
    try:
        return telethon.utils.get_peer_id(peer)
    except (TypeError, ValueError):
        return None


def peer_ids(peers: Sequence[Any] | None, self_id: int | None = None) -> list[int]:
    """Marked IDs of *peers*, order preserved, unkeyable entries dropped."""
    keys = (peer_key(p, self_id) for p in peers or ())
    return [k for k in keys if k is not None]


def merge_peers(
    current: Sequence[Any] | None,
    added: Sequence[Any],
    self_id: int | None = None,
) -> list[Any]:
    """Append *added* to *current*, skipping chats already present.

    Identity is the marked ID: the same chat resolved twice yields two different
    ``InputPeer`` objects (access hashes differ), and object equality would let a
    duplicate through.
    """
    merged = list(current or ())
    seen = {k for k in (peer_key(p, self_id) for p in merged) if k is not None}
    for peer in added:
        key = peer_key(peer, self_id)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        merged.append(peer)
    return merged


def subtract_peers(
    current: Sequence[Any] | None,
    remove_ids: set[int],
    self_id: int | None = None,
) -> list[Any]:
    """*current* without the peers whose marked ID is in *remove_ids*."""
    return [p for p in current or () if peer_key(p, self_id) not in remove_ids]


_NUMERIC_REF = re.compile(r"^-?\d+$")


def parse_chat_ref(raw: str) -> dict[str, Any]:
    """Turn one element of a ``chats`` list into ``_resolve_entity`` kwargs.

    The folder tools take chats as a list of strings, because the three-scalar
    selector convention (dialog_id / username / name) does not extend to lists.
    Each element is classified here and resolved by the existing machinery.
    """
    ref = (raw or "").strip()
    if not ref:
        raise ValueError("Empty chat reference")
    if _NUMERIC_REF.match(ref):
        return {"dialog_id": int(ref)}
    if ref.startswith("@"):
        return {"username": ref.lstrip("@")}
    return {"name": ref}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/folders.py tests/test_folders.py
git commit -m "feat: peer identity, merge, and chat-reference parsing for folders"
```

---

### Task 3: `to_folder` — TL object to dataclass

**Files:**
- Modify: `src/tg_mcp/folders.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: Task 1 and 2 helpers, `models.Folder`, `models.FolderChat`.
- Produces: `folders.to_folder(f, names=None, self_id=None) -> Folder`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
class TestToFolder:
    def test_maps_every_field(self) -> None:
        f = make_filter(
            folder_id=3, title="Work",
            include=[user_peer(1), channel_peer(777)],
            pinned=[user_peer(1)],
            exclude=[user_peer(2)],
            emoticon="💼", groups=True, exclude_muted=True,
        )
        folder = folders.to_folder(f, names={1: "Alice", -1000000000777: "Newsroom"})
        assert folder.id == 3
        assert folder.title == "Work"
        assert folder.kind == folders.KIND_FOLDER
        assert folder.emoji == "💼"
        assert folder.include == [
            FolderChat(dialog_id=1, name="Alice"),
            FolderChat(dialog_id=-1000000000777, name="Newsroom"),
        ]
        assert folder.pinned == [FolderChat(dialog_id=1, name="Alice")]
        assert folder.exclude == [FolderChat(dialog_id=2, name=None)]
        assert folder.groups is True
        assert folder.exclude_muted is True
        assert folder.bots is False

    def test_unset_flags_are_false_not_none(self) -> None:
        """Telethon leaves unset TL flags as None; a JSON consumer should see false."""
        folder = folders.to_folder(make_filter())
        for flag in folders.ALL_FLAGS:
            assert getattr(folder, flag) is False

    def test_default_filter_becomes_the_all_chats_entry(self) -> None:
        folder = folders.to_folder(types.DialogFilterDefault())
        assert folder.id == 0
        assert folder.title == "All chats"
        assert folder.kind == folders.KIND_DEFAULT
        assert folder.include == []

    def test_shared_chatlist_has_no_exclusions(self) -> None:
        """DialogFilterChatlist carries no exclude_peers and no auto-flags at all;
        reading them off it must not explode."""
        shared = types.DialogFilterChatlist(
            id=7, title=types.TextWithEntities(text="Team", entities=[]),
            pinned_peers=[], include_peers=[user_peer(9)],
        )
        folder = folders.to_folder(shared)
        assert folder.kind == folders.KIND_SHARED
        assert folder.include == [FolderChat(dialog_id=9, name=None)]
        assert folder.exclude == []
        assert folder.groups is False

    def test_saved_messages_uses_the_account_id(self) -> None:
        f = make_filter(include=[types.InputPeerSelf()])
        folder = folders.to_folder(f, names={999: "Saved Messages"}, self_id=999)
        assert folder.include == [FolderChat(dialog_id=999, name="Saved Messages")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py::TestToFolder -v`
Expected: FAIL — `AttributeError: module 'tg_mcp.folders' has no attribute 'to_folder'`.

- [ ] **Step 3: Implement**

Add `from collections.abc import Mapping` to the imports and `from .models import Folder, FolderChat`, then append to `folders.py`:

```python
def to_folder(
    f: Any,
    names: Mapping[int, str | None] | None = None,
    self_id: int | None = None,
) -> Folder:
    """Convert a TL dialog filter into the :class:`Folder` a tool returns.

    *names* maps marked ID to display name and may be partial — an unresolved
    chat comes back with ``name: None`` rather than failing the whole listing.
    """
    lookup = names or {}
    kind = filter_kind(f)
    if kind == KIND_DEFAULT:
        return Folder(id=DEFAULT_FOLDER_ID, title=DEFAULT_FOLDER_TITLE, kind=kind)

    def chats(attr: str) -> list[FolderChat]:
        peers = getattr(f, attr, None)
        return [FolderChat(dialog_id=i, name=lookup.get(i)) for i in peer_ids(peers, self_id)]

    # A shared chatlist has none of these attributes; bool(None) is the answer we want.
    flags = {name: bool(getattr(f, name, False)) for name in ALL_FLAGS}
    return Folder(
        id=getattr(f, "id", DEFAULT_FOLDER_ID),
        title=filter_title(f),
        kind=kind,
        emoji=getattr(f, "emoticon", None),
        include=chats("include_peers"),
        pinned=chats("pinned_peers"),
        exclude=chats("exclude_peers"),
        **flags,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/folders.py tests/test_folders.py
git commit -m "feat: convert dialog filters to Folder dataclasses"
```

---

### Task 4: Patch semantics, emptiness guard, and error text

**Files:**
- Modify: `src/tg_mcp/folders.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: Task 1 constants.
- Produces: `folders.patched_filter(existing, **changes) -> DialogFilter`, `folders.has_any_source(include_count, flags) -> bool`, `folders.error_code(exc) -> str | None`, `folders.friendly_error(exc) -> str | None`, `folders.EMPTY_FOLDER_MESSAGE`, `folders.shared_folder_message(title) -> str`, `folders.would_empty_message(title) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
class TestPatchedFilter:
    def test_untouched_fields_survive(self) -> None:
        original = make_filter(include=[user_peer(1)], emoticon="💼", groups=True)
        patched = folders.patched_filter(original, title=folders.title_value("Renamed"))
        assert folders.filter_title(patched) == "Renamed"
        assert folders.peer_ids(patched.include_peers) == [1]
        assert patched.emoticon == "💼"
        assert patched.groups is True

    def test_none_means_leave_alone(self) -> None:
        original = make_filter(emoticon="💼")
        patched = folders.patched_filter(original, emoticon=None)
        assert patched.emoticon == "💼"

    def test_false_is_applied_not_skipped(self) -> None:
        """`if value:` instead of `if value is not None:` would make turning a
        flag off impossible — the classic version of this bug."""
        original = make_filter(groups=True)
        patched = folders.patched_filter(original, groups=False)
        assert patched.groups is False

    def test_empty_list_is_applied(self) -> None:
        original = make_filter(include=[user_peer(1)], contacts=True)
        patched = folders.patched_filter(original, include_peers=[])
        assert patched.include_peers == []

    def test_unmodelled_fields_are_carried_through(self) -> None:
        """color and title_noanimate are set in the official app and never by us;
        an edit from here must not silently strip them."""
        original = make_filter(title_noanimate=True)
        original.color = 4
        patched = folders.patched_filter(original, emoticon="📌")
        assert patched.color == 4
        assert patched.title_noanimate is True

    def test_the_id_is_preserved(self) -> None:
        patched = folders.patched_filter(make_filter(folder_id=9), emoticon="📌")
        assert patched.id == 9

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown DialogFilter field"):
            folders.patched_filter(make_filter(), nonsense=True)


class TestHasAnySource:
    def test_chats_alone_are_enough(self) -> None:
        assert folders.has_any_source(1, {}) is True

    def test_an_inclusion_flag_alone_is_enough(self) -> None:
        assert folders.has_any_source(0, {"groups": True}) is True

    def test_exclusion_flags_are_not_a_source(self) -> None:
        """exclude_muted narrows a folder; it cannot populate one."""
        assert folders.has_any_source(0, {"exclude_muted": True}) is False

    def test_nothing_at_all(self) -> None:
        assert folders.has_any_source(0, {"groups": None, "bots": False}) is False


class FakeRpcError(Exception):
    """Stands in for telethon's RPCError, which carries the raw code in .message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TestErrorTranslation:
    def test_generated_subclass_is_recognised_by_name(self) -> None:
        from telethon.errors.rpcerrorlist import FilterIdInvalidError

        exc = FilterIdInvalidError(request=None)
        assert folders.error_code(exc) == "FILTER_ID_INVALID"
        assert "tg_folders" in folders.friendly_error(exc)

    def test_unmapped_error_is_read_off_the_message(self) -> None:
        """Telethon 1.44 has no FiltersTooMuchError class; it arrives as a bare
        RPCError whose .message is the raw code."""
        assert folders.error_code(FakeRpcError("FILTERS_TOO_MUCH")) == "FILTERS_TOO_MUCH"
        assert "limit" in folders.friendly_error(FakeRpcError("FILTERS_TOO_MUCH"))

    def test_include_empty_names_the_flags(self) -> None:
        assert "contacts" in folders.friendly_error(FakeRpcError("FILTER_INCLUDE_EMPTY"))

    def test_unknown_error_gets_no_sentence(self) -> None:
        assert folders.friendly_error(FakeRpcError("SOMETHING_ELSE")) is None
        assert folders.friendly_error(ValueError("boom")) is None

    def test_lowercase_message_is_not_a_code(self) -> None:
        assert folders.error_code(FakeRpcError("something went wrong")) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v -k "Patched or HasAnySource or ErrorTranslation"`
Expected: FAIL — `AttributeError: module 'tg_mcp.folders' has no attribute 'patched_filter'`.

- [ ] **Step 3: Implement**

Append to `folders.py`:

```python
def has_any_source(include_count: int, flags: Mapping[str, Any]) -> bool:
    """Would a filter with this much in it actually hold anything?

    Telegram rejects a folder with no explicit chats and no inclusion flag
    (``FILTER_INCLUDE_EMPTY``). Exclusion flags do not count: they narrow a
    folder, they cannot populate one.
    """
    if include_count > 0:
        return True
    return any(bool(flags.get(name)) for name in INCLUSION_FLAGS)


#: Fields patched_filter knows how to carry across a replacement. Anything TL
#: grows later must be added here or an edit will silently drop it.
_PATCHABLE = ("id", "title", "pinned_peers", "include_peers", "exclude_peers",
              "emoticon", "color", "title_noanimate", *ALL_FLAGS)


def patched_filter(existing: Any, **changes: Any) -> types.DialogFilter:
    """Copy *existing* with only the non-None entries of *changes* applied.

    There is no partial update in the API: ``UpdateDialogFilterRequest`` replaces
    the whole filter. Copying the live object — rather than building a fresh one
    from tool arguments — is what stops an edit made here from wiping settings
    made in the official app, ``color`` and ``title_noanimate`` above all.

    ``None`` means "leave alone"; ``False`` and ``[]`` are real values and are
    applied.
    """
    fields: dict[str, Any] = {name: getattr(existing, name, None) for name in _PATCHABLE}
    for name in ("pinned_peers", "include_peers", "exclude_peers"):
        fields[name] = list(fields[name] or [])
    for key, value in changes.items():
        if key not in fields:
            raise ValueError(f"Unknown DialogFilter field: {key}")
        if value is not None:
            fields[key] = value
    return types.DialogFilter(**fields)


EMPTY_FOLDER_MESSAGE = (
    "A folder needs at least one chat or one of "
    "contacts/non_contacts/groups/broadcasts/bots."
)


def shared_folder_message(title: str) -> str:
    """Refusal text for a write aimed at a folder joined from an invite link."""
    return (
        f'"{title}" is a shared folder joined from an invite link; this server '
        "does not edit those. Use an official Telegram client."
    )


def would_empty_message(title: str) -> str:
    """Refusal text for a removal that would leave a folder with no source."""
    return (
        f'Removing these chats would leave "{title}" with no source. '
        "Delete the folder with tg_folder_delete instead."
    )


#: Telethon generates a class for some folder errors but not all. These are the
#: ones that exist in 1.44; the rest are read off RPCError.message below.
_CLASS_CODES = {
    "FilterIdInvalidError": "FILTER_ID_INVALID",
    "FilterIncludeEmptyError": "FILTER_INCLUDE_EMPTY",
    "FilterTitleEmptyError": "FILTER_TITLE_EMPTY",
}

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")

_MESSAGES = {
    "FILTER_ID_INVALID": "Folder not found. Call tg_folders for the current list.",
    "FILTERS_TOO_MUCH": (
        "Telegram folder limit reached (10 without Premium, 20 with). "
        "Delete a folder first."
    ),
    "FILTER_INCLUDE_EMPTY": EMPTY_FOLDER_MESSAGE,
    "FILTER_TITLE_EMPTY": "Folder title must not be empty.",
    "CHATLISTS_TOO_MUCH": "Telegram shared-folder limit reached.",
}


def error_code(exc: BaseException) -> str | None:
    """Telegram's raw error code for *exc*, or None if it does not carry one."""
    name = type(exc).__name__
    if name in _CLASS_CODES:
        return _CLASS_CODES[name]
    message = getattr(exc, "message", None)
    if isinstance(message, str) and _ERROR_CODE.match(message):
        return message
    return None


def friendly_error(exc: BaseException) -> str | None:
    """A sentence a model can act on, or None when we have nothing better to say."""
    return _MESSAGES.get(error_code(exc) or "")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/folders.py tests/test_folders.py
git commit -m "feat: folder patch semantics and Telegram error translation"
```

---

### Task 5: Reading folders from Telegram

**Files:**
- Modify: `src/tg_mcp/client.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: everything in `folders.py`.
- Produces: `TelegramClientWrapper.get_folders(resolve_names=True) -> list[Folder]`, and the private `_dialog_filters()`, `_folder_names(filters, self_id)`, `_find_filter(folder_id=None, title=None)`, `_folder_snapshot(folder_id)`, `_resolve_peers(chats)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest


def set_filters(mock_telegram_client: AsyncMock, filters: list) -> None:
    """Make the client answer GetDialogFilters with *filters* and record writes.

    Telethon dispatches raw RPCs through TelegramClient.__call__, so one
    side_effect covers both the read and the writes the tests assert on.
    """
    async def _call(request):
        if isinstance(request, GetDialogFiltersRequest):
            result = MagicMock()
            result.filters = filters
            return result
        return MagicMock()

    mock_telegram_client.side_effect = _call


def sent_updates(mock_telegram_client: AsyncMock) -> list[UpdateDialogFilterRequest]:
    return [
        call.args[0] for call in mock_telegram_client.call_args_list
        if call.args and isinstance(call.args[0], UpdateDialogFilterRequest)
    ]


class TestGetFolders:
    async def test_lists_folders_with_names(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [
            types.DialogFilterDefault(),
            make_filter(folder_id=2, title="Work", include=[user_peer(1)]),
        ])
        named = MagicMock()
        named.first_name, named.last_name, named.title = "Alice", None, None
        mock_telegram_client.get_entity.return_value = named

        result = await mock_client_wrapper.get_folders()

        assert [f.title for f in result] == ["All chats", "Work"]
        assert result[1].include == [FolderChat(dialog_id=1, name="Alice")]

    async def test_unresolvable_chat_keeps_its_id(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)])])
        mock_telegram_client.get_entity.side_effect = ValueError("no access hash")

        result = await mock_client_wrapper.get_folders()

        assert result[0].include == [FolderChat(dialog_id=1, name=None)]

    async def test_resolve_names_false_skips_lookups(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)])])
        mock_telegram_client.get_entity.reset_mock()

        result = await mock_client_wrapper.get_folders(resolve_names=False)

        assert result[0].include == [FolderChat(dialog_id=1, name=None)]
        assert mock_telegram_client.get_entity.await_count == 0

    async def test_bare_list_response_is_accepted(self, mock_client_wrapper, mock_telegram_client) -> None:
        """Older layers returned the filter list directly rather than wrapped."""
        async def _call(request):
            return [make_filter(title="Work")]

        mock_telegram_client.side_effect = _call
        result = await mock_client_wrapper.get_folders(resolve_names=False)
        assert [f.title for f in result] == ["Work"]


class TestFindFilter:
    async def test_by_id(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(folder_id=2), make_filter(folder_id=3, title="Fun")])
        found, _ = await mock_client_wrapper._find_filter(folder_id=3)
        assert folders.filter_title(found) == "Fun"

    async def test_by_title_is_case_insensitive(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(title="Work")])
        found, _ = await mock_client_wrapper._find_filter(title="  work ")
        assert found.id == 2

    async def test_missing_id_raises(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(folder_id=2)])
        with pytest.raises(ValueError, match="Folder not found: id=9"):
            await mock_client_wrapper._find_filter(folder_id=9)

    async def test_ambiguous_title_names_both_ids(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [
            make_filter(folder_id=2, title="Work"), make_filter(folder_id=5, title="Work"),
        ])
        with pytest.raises(ValueError, match="2, 5"):
            await mock_client_wrapper._find_filter(title="Work")

    async def test_all_chats_is_not_addressable(self, mock_client_wrapper, mock_telegram_client) -> None:
        """The default view is not a folder; editing it is not a thing."""
        set_filters(mock_telegram_client, [types.DialogFilterDefault()])
        with pytest.raises(ValueError, match="Folder not found"):
            await mock_client_wrapper._find_filter(title="All chats")

    async def test_no_selector_raises(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter()])
        with pytest.raises(ValueError, match="One of folder_id or title"):
            await mock_client_wrapper._find_filter()


class TestResolvePeers:
    async def test_unresolvable_chats_are_skipped_not_fatal(self, mock_client_wrapper, mock_telegram_client) -> None:
        async def _get_entity(ref):
            if ref == "ghost":
                raise ValueError("Dialog not found: name=ghost")
            return MagicMock()

        mock_telegram_client.get_entity.side_effect = _get_entity

        peers, skipped = await mock_client_wrapper._resolve_peers(["@alice", "ghost"])

        assert len(peers) == 1
        assert skipped[0]["chat"] == "ghost"
        assert "ghost" in skipped[0]["reason"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v -k "GetFolders or FindFilter or ResolvePeers"`
Expected: FAIL — `AttributeError: 'TelegramClientWrapper' object has no attribute 'get_folders'`.

- [ ] **Step 3: Implement**

In `src/tg_mcp/client.py`, extend the imports:

```python
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    SendReactionRequest,
    UpdateDialogFilterRequest,
)

from .folders import (
    ALL_FLAGS, EMPTY_FOLDER_MESSAGE, INCLUSION_FLAGS, KIND_DEFAULT, KIND_SHARED,
    filter_kind, filter_title, merge_peers, next_free_id, parse_chat_ref,
    patched_filter, peer_ids, peer_key, has_any_source, shared_folder_message,
    subtract_peers, title_value, to_folder, would_empty_message,
)
from .models import Button, Dialog, Folder, FolderChat, Message, Reaction, UserInfo
```

Then add to `TelegramClientWrapper`, before `_resolve_entity`:

```python
    async def get_folders(self, resolve_names: bool = True) -> list[Folder]:
        """Every chat folder on the account, including the "All chats" entry.

        Names are best-effort: a peer the session cannot resolve comes back with
        ``name: None`` rather than failing the listing. ``resolve_names=False``
        skips the lookups entirely, which is the cheap path when the caller only
        needs IDs.
        """
        filters = await self._dialog_filters()
        self_id = (await self._client.get_me()).id
        names = await self._folder_names(filters, self_id) if resolve_names else {}
        return [to_folder(f, names, self_id) for f in filters]

    async def _dialog_filters(self) -> list[Any]:
        """The raw TL filter list.

        Layer 227 wraps it in ``messages.DialogFilters``; older layers returned
        the list itself, and a session written by one can still be read by this.
        """
        result = await self._client(GetDialogFiltersRequest())
        filters = getattr(result, "filters", result)
        return list(filters or [])

    async def _folder_names(self, filters: list[Any], self_id: int) -> dict[int, str | None]:
        """Display names for every peer mentioned by *filters*, keyed by marked ID."""
        names: dict[int, str | None] = {}
        for f in filters:
            for attr in ("include_peers", "pinned_peers", "exclude_peers"):
                for peer in getattr(f, attr, None) or []:
                    key = peer_key(peer, self_id)
                    if key is None or key in names:
                        continue
                    try:
                        entity = await self._client.get_entity(peer)
                        names[key] = telethon.utils.get_display_name(entity) or None
                    except Exception:
                        # A peer whose access hash the session never learned. The
                        # ID is still useful; the whole listing is not worth failing.
                        names[key] = None
        return names

    async def _find_filter(
        self,
        folder_id: int | None = None,
        title: str | None = None,
    ) -> tuple[Any, list[Any]]:
        """Locate one editable folder. Priority: folder_id > title.

        "All chats" is never a match: it is a view, not a folder, and there is
        nothing on it to edit.
        """
        filters = await self._dialog_filters()
        editable = [f for f in filters if filter_kind(f) != KIND_DEFAULT]
        if folder_id is not None:
            for f in editable:
                if getattr(f, "id", None) == folder_id:
                    return f, filters
            raise ValueError(f"Folder not found: id={folder_id}")
        if title is not None:
            wanted = title.strip().casefold()
            matches = [f for f in editable if filter_title(f).strip().casefold() == wanted]
            if not matches:
                raise ValueError(f"Folder not found: title={title!r}")
            if len(matches) > 1:
                ids = ", ".join(str(f.id) for f in matches)
                raise ValueError(
                    f"Several folders are named {title!r} (ids: {ids}); use folder_id"
                )
            return matches[0], filters
        raise ValueError("One of folder_id or title must be provided")

    async def _folder_snapshot(self, folder_id: int) -> Folder:
        """Re-read one folder after a write, so callers see what Telegram stored."""
        filters = await self._dialog_filters()
        self_id = (await self._client.get_me()).id
        for f in filters:
            if filter_kind(f) != KIND_DEFAULT and getattr(f, "id", None) == folder_id:
                return to_folder(f, await self._folder_names([f], self_id), self_id)
        raise ValueError(f"Folder not found: id={folder_id}")

    async def _resolve_peers(self, chats: Sequence[str]) -> tuple[list[Any], list[dict[str, str]]]:
        """Turn chat references into InputPeers, collecting failures rather than raising.

        One bad name in a list of ten should move the other nine, and say which
        one it dropped.
        """
        peers: list[Any] = []
        skipped: list[dict[str, str]] = []
        for raw in chats:
            try:
                entity = await self._resolve_entity(**parse_chat_ref(raw))
                peers.append(await self._client.get_input_entity(entity))
            except Exception as e:
                skipped.append({"chat": raw, "reason": str(e)})
        return peers, skipped
```

Add `Sequence` to the `typing`/`collections.abc` imports at the top of `client.py` (`from collections.abc import Sequence`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — the import changes in `client.py` must not disturb existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/tg_mcp/client.py tests/test_folders.py
git commit -m "feat: read chat folders from Telegram"
```

---

### Task 6: Creating and deleting folders

**Files:**
- Modify: `src/tg_mcp/client.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: Task 5's private helpers.
- Produces: `TelegramClientWrapper.create_folder(title, emoji=None, chats=(), **flags) -> tuple[Folder, list[dict[str, str]]]`, `TelegramClientWrapper.delete_folder(folder_id=None, title=None) -> tuple[int, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
class TestCreateFolder:
    async def test_sends_a_filter_with_the_first_free_id(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [types.DialogFilterDefault(), make_filter(folder_id=2)])

        await mock_client_wrapper.create_folder(title="Fun", chats=["@alice"])

        sent = sent_updates(mock_telegram_client)[0]
        assert sent.id == 3
        assert sent.filter.id == 3
        assert folders.filter_title(sent.filter) == "Fun"
        assert len(sent.filter.include_peers) == 1

    async def test_title_is_wrapped_for_the_wire(self, mock_client_wrapper, mock_telegram_client) -> None:
        """A bare str here fails at serialisation against the real server."""
        set_filters(mock_telegram_client, [])

        await mock_client_wrapper.create_folder(title="Fun", groups=True)

        sent = sent_updates(mock_telegram_client)[0]
        assert isinstance(sent.filter.title, types.TextWithEntities)

    async def test_flags_are_carried(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [])

        await mock_client_wrapper.create_folder(title="Groups", groups=True, exclude_muted=True)

        sent = sent_updates(mock_telegram_client)[0]
        assert sent.filter.groups is True
        assert sent.filter.exclude_muted is True
        assert sent.filter.bots is None

    async def test_a_folder_with_no_source_is_refused_locally(self, mock_client_wrapper, mock_telegram_client) -> None:
        """No chats and no inclusion flag: Telegram would answer
        FILTER_INCLUDE_EMPTY, and a round trip to learn that is wasteful."""
        set_filters(mock_telegram_client, [])

        with pytest.raises(ValueError, match="at least one chat"):
            await mock_client_wrapper.create_folder(title="Empty", exclude_muted=True)

        assert sent_updates(mock_telegram_client) == []

    async def test_unresolvable_chats_are_reported(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [])
        mock_telegram_client.get_entity.side_effect = ValueError("Dialog not found: name=ghost")

        with pytest.raises(ValueError, match="at least one chat"):
            await mock_client_wrapper.create_folder(title="Fun", chats=["ghost"])


class TestDeleteFolder:
    async def test_sends_a_null_filter(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(folder_id=4, title="Old")])

        deleted_id, deleted_title = await mock_client_wrapper.delete_folder(folder_id=4)

        sent = sent_updates(mock_telegram_client)[0]
        assert sent.id == 4
        assert sent.filter is None
        assert (deleted_id, deleted_title) == (4, "Old")

    async def test_shared_folders_are_refused(self, mock_client_wrapper, mock_telegram_client) -> None:
        shared = types.DialogFilterChatlist(
            id=7, title=types.TextWithEntities(text="Team", entities=[]),
            pinned_peers=[], include_peers=[],
        )
        set_filters(mock_telegram_client, [shared])

        with pytest.raises(ValueError, match="shared folder"):
            await mock_client_wrapper.delete_folder(folder_id=7)

        assert sent_updates(mock_telegram_client) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v -k "CreateFolder or DeleteFolder"`
Expected: FAIL — `AttributeError: 'TelegramClientWrapper' object has no attribute 'create_folder'`.

- [ ] **Step 3: Implement**

Add to `TelegramClientWrapper` after `get_folders`:

```python
    async def create_folder(
        self,
        title: str,
        emoji: str | None = None,
        chats: Sequence[str] = (),
        **flags: bool | None,
    ) -> tuple[Folder, list[dict[str, str]]]:
        """Create a folder and return it, along with any chats that did not resolve."""
        filters = await self._dialog_filters()
        folder_id = next_free_id(getattr(f, "id", None) for f in filters)
        peers, skipped = await self._resolve_peers(chats or ())
        if not has_any_source(len(peers), flags):
            raise ValueError(EMPTY_FOLDER_MESSAGE)
        new = types.DialogFilter(
            id=folder_id,
            title=title_value(title),
            pinned_peers=[],
            include_peers=peers,
            exclude_peers=[],
            emoticon=emoji,
            # TL flags are set-or-absent; sending False and sending nothing are the
            # same bit, so None keeps the request minimal.
            **{name: (True if flags.get(name) else None) for name in ALL_FLAGS},
        )
        await self._client(UpdateDialogFilterRequest(id=folder_id, filter=new))
        return await self._folder_snapshot(folder_id), skipped

    async def delete_folder(
        self,
        folder_id: int | None = None,
        title: str | None = None,
    ) -> tuple[int, str]:
        """Delete a folder. The chats in it are untouched — a folder is a view."""
        f, _ = await self._find_filter(folder_id, title)
        if filter_kind(f) == KIND_SHARED:
            raise ValueError(shared_folder_message(filter_title(f)))
        deleted_title = filter_title(f)
        await self._client(UpdateDialogFilterRequest(id=f.id, filter=None))
        return f.id, deleted_title
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py tests/test_folders.py
git commit -m "feat: create and delete Telegram folders"
```

---

### Task 7: Editing folders and their membership

**Files:**
- Modify: `src/tg_mcp/client.py`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: Tasks 5 and 6.
- Produces: `TelegramClientWrapper.update_folder(folder_id=None, title=None, new_title=None, emoji=None, **flags) -> Folder`, `TelegramClientWrapper.add_folder_chats(chats, folder_id=None, title=None) -> tuple[Folder, list[int], list[dict[str, str]]]`, `TelegramClientWrapper.remove_folder_chats(chats, folder_id=None, title=None) -> tuple[Folder, list[int], list[dict[str, str]]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_folders.py`:

```python
class TestUpdateFolder:
    async def test_renames_without_touching_membership(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)], emoticon="💼")])

        await mock_client_wrapper.update_folder(folder_id=2, new_title="Renamed")

        sent = sent_updates(mock_telegram_client)[0]
        assert folders.filter_title(sent.filter) == "Renamed"
        assert folders.peer_ids(sent.filter.include_peers) == [1]
        assert sent.filter.emoticon == "💼"

    async def test_turning_a_flag_off(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)], groups=True)])

        await mock_client_wrapper.update_folder(folder_id=2, groups=False)

        assert sent_updates(mock_telegram_client)[0].filter.groups is False

    async def test_omitted_flags_are_left_alone(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)], groups=True, bots=True)])

        await mock_client_wrapper.update_folder(folder_id=2, emoji="📌")

        sent = sent_updates(mock_telegram_client)[0]
        assert sent.filter.groups is True and sent.filter.bots is True

    async def test_clearing_the_last_source_is_refused(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(groups=True)])

        with pytest.raises(ValueError, match="at least one chat"):
            await mock_client_wrapper.update_folder(folder_id=2, groups=False)

        assert sent_updates(mock_telegram_client) == []

    async def test_shared_folders_are_refused(self, mock_client_wrapper, mock_telegram_client) -> None:
        shared = types.DialogFilterChatlist(
            id=7, title=types.TextWithEntities(text="Team", entities=[]),
            pinned_peers=[], include_peers=[user_peer(1)],
        )
        set_filters(mock_telegram_client, [shared])

        with pytest.raises(ValueError, match="shared folder"):
            await mock_client_wrapper.update_folder(folder_id=7, new_title="Mine")


class TestAddFolderChats:
    async def test_appends_and_reports_what_was_added(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)])])
        mock_telegram_client.get_input_entity.return_value = user_peer(2)

        _, added, skipped = await mock_client_wrapper.add_folder_chats(["@bob"], folder_id=2)

        sent = sent_updates(mock_telegram_client)[0]
        assert folders.peer_ids(sent.filter.include_peers) == [1, 2]
        assert added == [2] and skipped == []

    async def test_an_existing_chat_is_not_duplicated(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)])])
        mock_telegram_client.get_input_entity.return_value = user_peer(1, access_hash=999)

        _, added, _ = await mock_client_wrapper.add_folder_chats(["@alice"], folder_id=2)

        assert folders.peer_ids(sent_updates(mock_telegram_client)[0].filter.include_peers) == [1]
        assert added == []

    async def test_nothing_resolvable_is_an_error(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)])])
        mock_telegram_client.get_entity.side_effect = ValueError("Dialog not found: name=ghost")

        with pytest.raises(ValueError, match="None of the given chats"):
            await mock_client_wrapper.add_folder_chats(["ghost"], folder_id=2)

        assert sent_updates(mock_telegram_client) == []


class TestRemoveFolderChats:
    async def test_drops_from_include_and_pinned(self, mock_client_wrapper, mock_telegram_client) -> None:
        """A pinned peer that is not in include_peers is an invalid filter."""
        set_filters(mock_telegram_client, [
            make_filter(include=[user_peer(1), user_peer(2)], pinned=[user_peer(2)]),
        ])
        mock_telegram_client.get_input_entity.return_value = user_peer(2, access_hash=555)

        _, removed, _ = await mock_client_wrapper.remove_folder_chats(["@bob"], folder_id=2)

        sent = sent_updates(mock_telegram_client)[0]
        assert folders.peer_ids(sent.filter.include_peers) == [1]
        assert sent.filter.pinned_peers == []
        assert removed == [2]

    async def test_a_chat_that_was_not_there(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)], groups=True)])
        mock_telegram_client.get_input_entity.return_value = user_peer(8)

        _, removed, _ = await mock_client_wrapper.remove_folder_chats(["@nobody"], folder_id=2)

        assert removed == []
        assert folders.peer_ids(sent_updates(mock_telegram_client)[0].filter.include_peers) == [1]

    async def test_emptying_the_folder_is_refused(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(title="Work", include=[user_peer(1)])])
        mock_telegram_client.get_input_entity.return_value = user_peer(1)

        with pytest.raises(ValueError, match="tg_folder_delete"):
            await mock_client_wrapper.remove_folder_chats(["@alice"], folder_id=2)

        assert sent_updates(mock_telegram_client) == []

    async def test_an_auto_flag_keeps_the_folder_alive(self, mock_client_wrapper, mock_telegram_client) -> None:
        """Emptying include_peers is fine when a flag still populates the folder."""
        set_filters(mock_telegram_client, [make_filter(include=[user_peer(1)], groups=True)])
        mock_telegram_client.get_input_entity.return_value = user_peer(1)

        await mock_client_wrapper.remove_folder_chats(["@alice"], folder_id=2)

        assert sent_updates(mock_telegram_client)[0].filter.include_peers == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py -v -k "UpdateFolder or AddFolderChats or RemoveFolderChats"`
Expected: FAIL — `AttributeError: 'TelegramClientWrapper' object has no attribute 'update_folder'`.

- [ ] **Step 3: Implement**

Add to `TelegramClientWrapper` after `delete_folder`:

```python
    async def update_folder(
        self,
        folder_id: int | None = None,
        title: str | None = None,
        new_title: str | None = None,
        emoji: str | None = None,
        **flags: bool | None,
    ) -> Folder:
        """Change a folder's name, emoji, or auto-filters. Membership is untouched.

        Every argument is optional and ``None`` means "leave alone". The current
        filter is read and copied rather than rebuilt, so a field this server does
        not model survives the round trip.
        """
        f, _ = await self._find_filter(folder_id, title)
        self._require_editable(f)
        changes: dict[str, Any] = {name: flags.get(name) for name in ALL_FLAGS}
        if new_title is not None:
            changes["title"] = title_value(new_title)
        if emoji is not None:
            changes["emoticon"] = emoji
        updated = patched_filter(f, **changes)
        if not has_any_source(
            len(updated.include_peers or []),
            {name: getattr(updated, name, None) for name in INCLUSION_FLAGS},
        ):
            raise ValueError(EMPTY_FOLDER_MESSAGE)
        await self._client(UpdateDialogFilterRequest(id=f.id, filter=updated))
        return await self._folder_snapshot(f.id)

    async def add_folder_chats(
        self,
        chats: Sequence[str],
        folder_id: int | None = None,
        title: str | None = None,
    ) -> tuple[Folder, list[int], list[dict[str, str]]]:
        """Add chats to a folder. Returns the folder, what was added, what was skipped."""
        f, _ = await self._find_filter(folder_id, title)
        self._require_editable(f)
        self_id = (await self._client.get_me()).id
        peers, skipped = await self._resolve_peers(chats)
        if not peers:
            reasons = "; ".join(s["reason"] for s in skipped) or "no chats given"
            raise ValueError(f"None of the given chats could be resolved: {reasons}")
        before = set(peer_ids(f.include_peers, self_id))
        merged = merge_peers(f.include_peers, peers, self_id)
        added = [i for i in peer_ids(merged, self_id) if i not in before]
        await self._client(
            UpdateDialogFilterRequest(id=f.id, filter=patched_filter(f, include_peers=merged))
        )
        return await self._folder_snapshot(f.id), added, skipped

    async def remove_folder_chats(
        self,
        chats: Sequence[str],
        folder_id: int | None = None,
        title: str | None = None,
    ) -> tuple[Folder, list[int], list[dict[str, str]]]:
        """Remove chats from a folder, from the pinned list as well as the members."""
        f, _ = await self._find_filter(folder_id, title)
        self._require_editable(f)
        self_id = (await self._client.get_me()).id
        peers, skipped = await self._resolve_peers(chats)
        if not peers:
            reasons = "; ".join(s["reason"] for s in skipped) or "no chats given"
            raise ValueError(f"None of the given chats could be resolved: {reasons}")
        remove_ids = set(peer_ids(peers, self_id))
        before = set(peer_ids(f.include_peers, self_id))
        remaining = subtract_peers(f.include_peers, remove_ids, self_id)
        if not has_any_source(
            len(remaining),
            {name: getattr(f, name, None) for name in INCLUSION_FLAGS},
        ):
            raise ValueError(would_empty_message(filter_title(f)))
        updated = patched_filter(
            f,
            include_peers=remaining,
            pinned_peers=subtract_peers(f.pinned_peers, remove_ids, self_id),
        )
        await self._client(UpdateDialogFilterRequest(id=f.id, filter=updated))
        return await self._folder_snapshot(f.id), sorted(before & remove_ids), skipped

    @staticmethod
    def _require_editable(f: Any) -> None:
        """Refuse writes to a folder joined from an invite link."""
        if filter_kind(f) == KIND_SHARED:
            raise ValueError(shared_folder_message(filter_title(f)))
```

Then simplify `delete_folder` from Task 6 to call `self._require_editable(f)` in place of its inline `if filter_kind(f) == KIND_SHARED` check.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py -v`
Expected: PASS, including Task 6's delete tests after the refactor.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py tests/test_folders.py
git commit -m "feat: edit folder settings and membership"
```

---

### Task 8: The six MCP tools

**Files:**
- Modify: `src/tg_mcp/server.py`
- Modify: `tests/test_readonly.py:26-32`
- Test: `tests/test_folders.py`

**Interfaces:**
- Consumes: every `TelegramClientWrapper` folder method.
- Produces: `tg_folders`, `tg_folder_create`, `tg_folder_update`, `tg_folder_delete`, `tg_folder_add_chats`, `tg_folder_remove_chats`; `server._folder_error(exc) -> str`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_readonly.py`, extend the two name sets at lines 26–32:

```python
ALL_TOOL_NAMES = {
    "tg_me", "tg_dialogs", "tg_dialog", "tg_search", "tg_download",
    "tg_send", "tg_send_file", "tg_forward", "tg_read", "tg_click",
    "tg_react", "tg_delete", "tg_edit",
    "tg_folders", "tg_folder_create", "tg_folder_update", "tg_folder_delete",
    "tg_folder_add_chats", "tg_folder_remove_chats",
}
READ_ONLY_NAMES = {
    "tg_me", "tg_dialogs", "tg_dialog", "tg_search", "tg_download", "tg_folders",
}
```

Append to `tests/test_folders.py`:

```python
import dataclasses

from tg_mcp import server


def tool_ctx(wrapper) -> MagicMock:
    """The Context shape every tool reaches through for its client."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context.client = wrapper
    return ctx


class TestFolderTools:
    async def test_tg_folders_returns_dicts(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(title="Work", include=[user_peer(1)])])

        result = await server.tg_folders(tool_ctx(mock_client_wrapper), resolve_names=False)

        assert result[0]["title"] == "Work"
        assert result[0]["include"] == [{"dialog_id": 1, "name": None}]

    async def test_tg_folder_create_reports_the_new_id(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(folder_id=2)])

        result = await server.tg_folder_create(tool_ctx(mock_client_wrapper), title="Fun", groups=True)

        assert result["ok"] is True
        assert result["folder_id"] == 3

    async def test_tg_folder_delete_names_what_it_removed(self, mock_client_wrapper, mock_telegram_client) -> None:
        set_filters(mock_telegram_client, [make_filter(folder_id=4, title="Old")])

        result = await server.tg_folder_delete(tool_ctx(mock_client_wrapper), folder_id=4)

        assert result == {"ok": True, "deleted_id": 4, "deleted_title": "Old"}

    async def test_a_selectorless_call_is_rejected_before_the_network(self, mock_client_wrapper) -> None:
        with pytest.raises(ValueError, match="folder_id or title"):
            await server.tg_folder_delete(tool_ctx(mock_client_wrapper))

    async def test_telegram_errors_become_sentences(self, mock_client_wrapper, mock_telegram_client) -> None:
        """The raw code FILTERS_TOO_MUCH tells a model nothing actionable."""
        set_filters(mock_telegram_client, [])
        mock_telegram_client.side_effect = FakeRpcError("FILTERS_TOO_MUCH")

        result = await server.tg_folder_create(tool_ctx(mock_client_wrapper), title="Fun", groups=True)

        assert "limit" in result["error"]

    async def test_unknown_errors_keep_the_generic_path(self, mock_client_wrapper, mock_telegram_client) -> None:
        mock_telegram_client.side_effect = RuntimeError("kaboom")

        result = await server.tg_folders(tool_ctx(mock_client_wrapper))

        assert result[0]["error"].startswith("RuntimeError")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_folders.py tests/test_readonly.py -v`
Expected: FAIL — `AttributeError: module 'tg_mcp.server' has no attribute 'tg_folders'` and the partition assertion in `test_every_tool_is_classified_exactly_once`.

- [ ] **Step 3: Implement the tools**

In `src/tg_mcp/server.py`, add `from .folders import friendly_error` to the imports, then add after `_error_message`:

```python
def _folder_error(exc: Exception) -> str:
    """Folder errors first, generic fallback second.

    Telegram answers a folder mistake with a bare code — FILTERS_TOO_MUCH — that
    reads as noise. Translating it is the difference between a model retrying
    blindly and a model deleting a folder first.
    """
    return friendly_error(exc) or _error_message(exc)
```

Add the six tools after `tg_download`:

```python
async def tg_folders(
    ctx: Context,
    resolve_names: Annotated[bool, Field(description="Look up display names for the chats in each folder. False returns IDs only and is cheaper.")] = True,
) -> list[dict]:
    """List Telegram chat folders. Includes the built-in 'All chats' view as id 0, and folders joined from an invite link with kind 'shared' (those cannot be edited here)."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        return [dataclasses.asdict(f) for f in await client.get_folders(resolve_names=resolve_names)]
    except Exception as e:
        return [{"error": _folder_error(e)}]


async def tg_folder_create(
    ctx: Context,
    title: Annotated[str, Field(description="Folder name as shown in the chat list.")],
    emoji: Annotated[Optional[str], Field(description="Single emoji shown on the folder tab."), WithJsonSchema({"type": "string", "description": "Single emoji shown on the folder tab."})] = None,
    chats: Annotated[Optional[list[str]], Field(description="Chats to put in the folder. Each entry is a numeric dialog_id, an @handle, or a display name."), WithJsonSchema({"type": "array", "items": {"type": "string"}, "description": "Chats to put in the folder. Each entry is a numeric dialog_id, an @handle, or a display name."})] = None,
    contacts: Annotated[bool, Field(description="Automatically include all contacts.")] = False,
    non_contacts: Annotated[bool, Field(description="Automatically include all non-contacts.")] = False,
    groups: Annotated[bool, Field(description="Automatically include all groups.")] = False,
    broadcasts: Annotated[bool, Field(description="Automatically include all channels.")] = False,
    bots: Annotated[bool, Field(description="Automatically include all bots.")] = False,
    exclude_muted: Annotated[bool, Field(description="Hide muted chats from the folder.")] = False,
    exclude_read: Annotated[bool, Field(description="Hide fully-read chats from the folder.")] = False,
    exclude_archived: Annotated[bool, Field(description="Hide archived chats from the folder.")] = False,
) -> dict:
    """Create a Telegram chat folder. A folder needs at least one chat or one of contacts/non_contacts/groups/broadcasts/bots — the exclude_* flags only narrow it."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        folder, skipped = await client.create_folder(
            title=title, emoji=emoji, chats=chats or [],
            contacts=contacts, non_contacts=non_contacts, groups=groups,
            broadcasts=broadcasts, bots=bots, exclude_muted=exclude_muted,
            exclude_read=exclude_read, exclude_archived=exclude_archived,
        )
        return {"ok": True, "folder_id": folder.id, "folder": dataclasses.asdict(folder), "skipped": skipped}
    except Exception as e:
        return {"error": _folder_error(e)}


async def tg_folder_update(
    ctx: Context,
    folder_id: Annotated[Optional[int], Field(description="Folder ID from tg_folders. Preferred over title."), WithJsonSchema({"type": "integer", "description": "Folder ID from tg_folders. Preferred over title."})] = None,
    title: Annotated[Optional[str], Field(description="Current folder name, when the ID is unknown."), WithJsonSchema({"type": "string", "description": "Current folder name, when the ID is unknown."})] = None,
    new_title: Annotated[Optional[str], Field(description="Rename the folder to this."), WithJsonSchema({"type": "string", "description": "Rename the folder to this."})] = None,
    emoji: Annotated[Optional[str], Field(description="New tab emoji. Pass an empty string to clear it."), WithJsonSchema({"type": "string", "description": "New tab emoji. Pass an empty string to clear it."})] = None,
    contacts: Annotated[Optional[bool], Field(description="Include all contacts. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Include all contacts. Omit to leave unchanged."})] = None,
    non_contacts: Annotated[Optional[bool], Field(description="Include all non-contacts. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Include all non-contacts. Omit to leave unchanged."})] = None,
    groups: Annotated[Optional[bool], Field(description="Include all groups. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Include all groups. Omit to leave unchanged."})] = None,
    broadcasts: Annotated[Optional[bool], Field(description="Include all channels. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Include all channels. Omit to leave unchanged."})] = None,
    bots: Annotated[Optional[bool], Field(description="Include all bots. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Include all bots. Omit to leave unchanged."})] = None,
    exclude_muted: Annotated[Optional[bool], Field(description="Hide muted chats. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Hide muted chats. Omit to leave unchanged."})] = None,
    exclude_read: Annotated[Optional[bool], Field(description="Hide fully-read chats. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Hide fully-read chats. Omit to leave unchanged."})] = None,
    exclude_archived: Annotated[Optional[bool], Field(description="Hide archived chats. Omit to leave unchanged."), WithJsonSchema({"type": "boolean", "description": "Hide archived chats. Omit to leave unchanged."})] = None,
) -> dict:
    """Rename a folder or change its auto-filters. Anything omitted is left as it was; folder membership is never touched — use tg_folder_add_chats / tg_folder_remove_chats for that. Supply exactly one of folder_id or title."""
    if folder_id is None and title is None:
        raise ValueError("One of folder_id or title must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        folder = await client.update_folder(
            folder_id=folder_id, title=title, new_title=new_title, emoji=emoji,
            contacts=contacts, non_contacts=non_contacts, groups=groups,
            broadcasts=broadcasts, bots=bots, exclude_muted=exclude_muted,
            exclude_read=exclude_read, exclude_archived=exclude_archived,
        )
        return {"ok": True, "folder": dataclasses.asdict(folder)}
    except Exception as e:
        return {"error": _folder_error(e)}


async def tg_folder_delete(
    ctx: Context,
    folder_id: Annotated[Optional[int], Field(description="Folder ID from tg_folders. Preferred over title."), WithJsonSchema({"type": "integer", "description": "Folder ID from tg_folders. Preferred over title."})] = None,
    title: Annotated[Optional[str], Field(description="Folder name, when the ID is unknown."), WithJsonSchema({"type": "string", "description": "Folder name, when the ID is unknown."})] = None,
) -> dict:
    """Delete a Telegram chat folder. The chats themselves are untouched — a folder is only a view. Supply exactly one of folder_id or title."""
    if folder_id is None and title is None:
        raise ValueError("One of folder_id or title must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        deleted_id, deleted_title = await client.delete_folder(folder_id=folder_id, title=title)
        return {"ok": True, "deleted_id": deleted_id, "deleted_title": deleted_title}
    except Exception as e:
        return {"error": _folder_error(e)}


async def tg_folder_add_chats(
    ctx: Context,
    chats: Annotated[list[str], Field(description="Chats to add. Each entry is a numeric dialog_id, an @handle, or a display name.", min_length=1)],
    folder_id: Annotated[Optional[int], Field(description="Folder ID from tg_folders. Preferred over title."), WithJsonSchema({"type": "integer", "description": "Folder ID from tg_folders. Preferred over title."})] = None,
    title: Annotated[Optional[str], Field(description="Folder name, when the ID is unknown."), WithJsonSchema({"type": "string", "description": "Folder name, when the ID is unknown."})] = None,
) -> dict:
    """Add chats to a folder. Chats already in it are left alone; ones that cannot be resolved are listed in 'skipped' and the rest still go through. Supply exactly one of folder_id or title."""
    if folder_id is None and title is None:
        raise ValueError("One of folder_id or title must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        folder, added, skipped = await client.add_folder_chats(chats, folder_id=folder_id, title=title)
        return {"ok": True, "added": added, "skipped": skipped, "folder": dataclasses.asdict(folder)}
    except Exception as e:
        return {"error": _folder_error(e)}


async def tg_folder_remove_chats(
    ctx: Context,
    chats: Annotated[list[str], Field(description="Chats to remove. Each entry is a numeric dialog_id, an @handle, or a display name.", min_length=1)],
    folder_id: Annotated[Optional[int], Field(description="Folder ID from tg_folders. Preferred over title."), WithJsonSchema({"type": "integer", "description": "Folder ID from tg_folders. Preferred over title."})] = None,
    title: Annotated[Optional[str], Field(description="Folder name, when the ID is unknown."), WithJsonSchema({"type": "string", "description": "Folder name, when the ID is unknown."})] = None,
) -> dict:
    """Remove chats from a folder, including from its pinned list. Removing the last chat from a folder with no auto-filters is refused — delete the folder instead. Supply exactly one of folder_id or title."""
    if folder_id is None and title is None:
        raise ValueError("One of folder_id or title must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        folder, removed, skipped = await client.remove_folder_chats(chats, folder_id=folder_id, title=title)
        return {"ok": True, "removed": removed, "skipped": skipped, "folder": dataclasses.asdict(folder)}
    except Exception as e:
        return {"error": _folder_error(e)}
```

Update the two registration lists:

```python
READ_TOOLS = [tg_me, tg_dialogs, tg_dialog, tg_search, tg_download, tg_folders]

WRITE_TOOLS = [
    tg_send, tg_send_file, tg_forward, tg_read, tg_click,
    tg_react, tg_delete, tg_edit,
    tg_folder_create, tg_folder_update, tg_folder_delete,
    tg_folder_add_chats, tg_folder_remove_chats,
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_folders.py tests/test_readonly.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. `tests/test_server.py` and `tests/test_server_startup.py` also count tools; fix any hard-coded totals they carry.

- [ ] **Step 6: Commit**

```bash
git add src/tg_mcp/server.py tests/test_folders.py tests/test_readonly.py
git commit -m "feat: expose six chat-folder tools over MCP"
```

---

### Task 9: Documentation and release

**Files:**
- Modify: `README.md:19-33` (tool table), `CHANGELOG.md`, `pyproject.toml:3`
- Modify: `CLAUDE.md` (project structure block — `folders.py` is new)

**Interfaces:**
- Consumes: the finished tool set.
- Produces: nothing code depends on.

- [ ] **Step 1: Bump the version**

In `pyproject.toml`, line 3: `version = "0.11.0"`.

- [ ] **Step 2: Add the tools to the README table**

After the `tg_download` row, add:

```markdown
| `tg_folders` | `resolve_names` (bool, default `true`) | List of folders — each has `id`, `title`, `kind`, `emoji`, `include`/`pinned`/`exclude` (lists of `{dialog_id, name}`), and the eight filter flags | List chat folders, including `All chats` as id `0` |
| `tg_folder_create` ✎ | `title` (str); `emoji` (str, optional); `chats` (list[str], optional); `contacts`/`non_contacts`/`groups`/`broadcasts`/`bots`/`exclude_muted`/`exclude_read`/`exclude_archived` (bool, default `false`) | `{"ok": true, "folder_id": n, "folder": {…}, "skipped": [...]}` | Create a folder ([chat references](#referring-to-chats-in-a-folder)) |
| `tg_folder_update` ✎ | folder selector — `folder_id` (int) or `title` (str); `new_title` (str, optional); `emoji` (str, optional); the eight flags (bool, optional) | `{"ok": true, "folder": {…}}` | Rename a folder or change its auto-filters; anything omitted is left as it was |
| `tg_folder_delete` ✎ | folder selector | `{"ok": true, "deleted_id": n, "deleted_title": "…"}` | Delete a folder; the chats in it are untouched |
| `tg_folder_add_chats` ✎ | `chats` (list[str]); folder selector | `{"ok": true, "added": [...], "skipped": [...], "folder": {…}}` | Add chats to a folder |
| `tg_folder_remove_chats` ✎ | `chats` (list[str]); folder selector | `{"ok": true, "removed": [...], "skipped": [...], "folder": {…}}` | Remove chats from a folder, pinned list included |
```

- [ ] **Step 3: Add a README section explaining chat references and folder rules**

Add after the reactions/buttons sections:

```markdown
### Referring to chats in a folder

`tg_folder_create`, `tg_folder_add_chats`, and `tg_folder_remove_chats` take chats as a
list of strings rather than the usual three selectors. Each entry is read as:

| Form | Example | Meaning |
|------|---------|---------|
| Digits, optionally negative | `-1001234567890` | Numeric `dialog_id` from `tg_dialogs` — most reliable |
| Leading `@` | `@durov` | Telegram handle |
| Anything else | `Team standup` | Display name; may be ambiguous |

Entries that do not resolve are returned in `skipped` with a reason and the rest are
still applied.

### Folder rules Telegram enforces

- A folder must have **at least one chat or one of** `contacts`, `non_contacts`,
  `groups`, `broadcasts`, `bots`. The `exclude_*` flags only narrow a folder.
  Removing the last chat from a folder with no such flag is refused — delete the
  folder instead.
- Without Premium: 10 folders, 100 chats each.
- `All chats` (`id: 0`) is a view, not a folder: it cannot be edited or deleted.
- Folders joined from an invite link come back with `kind: "shared"`; this server
  lists them but does not edit them.
```

- [ ] **Step 4: Add the changelog entry**

At the top of `CHANGELOG.md`, under the title paragraph:

```markdown
## 0.11.0

### Added

- **Chat folders.** Six new tools: `tg_folders` (read), plus `tg_folder_create`,
  `tg_folder_update`, `tg_folder_delete`, `tg_folder_add_chats`, and
  `tg_folder_remove_chats` (write). `TG_READONLY` keeps the listing and drops the rest.
- Folder contents carry the same marked dialog IDs `tg_dialogs` returns, so the two
  tools compose without translation.
- Telegram's folder errors are translated into sentences: `FILTERS_TOO_MUCH` becomes
  the limit and what to do about it.

### Notes

- `tg_folder_update` never touches membership, and copies the stored filter rather than
  rebuilding it, so folder settings this server does not model (colour, title animation)
  survive an edit made from here.
- Shared folders (joined from an invite link) are listed with `kind: "shared"` and
  refuse writes. Invite links, folder reordering, and folder tags are not supported.
```

- [ ] **Step 5: Update the project structure block in `CLAUDE.md`**

Add `│       ├── folders.py         ← dialog-filter arithmetic, pure` to the `src/tg_mcp/` listing and `│   ├── test_folders.py` to the `tests/` listing.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md CLAUDE.md pyproject.toml
git commit -m "docs: document chat folder tools (v0.11.0)"
```

- [ ] **Step 8: Spawn a DOCUMENTER agent**

CLAUDE.md requires it after any code change: dispatch DOCUMENTER with `agents/DOCUMENTER.md` as its role and this plan as context, to verify `README.md` and `docs/` match the shipped behaviour.

---

## Self-Review Notes

Spec coverage checked section by section:

- Six tools, READ/WRITE split → Task 8.
- Folder addressing by `folder_id`/`title` with ambiguity error → Task 5 (`_find_filter`).
- `chats: list[str]` and its three forms → Task 2 (`parse_chat_ref`), Task 5 (`_resolve_peers`).
- Patch semantics and unmodelled-field preservation → Task 4 (`patched_filter`), pinned by `test_unmodelled_fields_are_carried_through`.
- `Folder`/`FolderChat` → Task 1; `to_folder` → Task 3.
- Marked IDs matching `tg_dialogs` → Task 2 (`peer_key`), asserted against `telethon.utils.get_peer_id`.
- Error translation table → Task 4.
- `DialogFilterDefault` / `DialogFilterChatlist` handling → Tasks 1, 3, 6, 7.
- Best-effort name resolution and `resolve_names=False` → Task 5.
- README, CHANGELOG, DOCUMENTER → Task 9.

**Addendum to the spec, discovered while planning:** `telethon.utils.get_peer_id` raises
`TypeError` on `InputPeerSelf`, which is how Saved Messages appears inside a folder. Every
peer helper therefore takes a `self_id` and the client passes the account's own ID. The spec
does not mention this; the plan is the authority on it.
