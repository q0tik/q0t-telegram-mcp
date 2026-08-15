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

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import telethon.utils
from telethon.tl import types

from .models import Folder, FolderChat

FOLDER_ID_MIN = 2
FOLDER_ID_MAX = 255

#: Longest folder title Telegram accepts, in characters. Verified against the
#: live API: 12 emoji are accepted and 13 ASCII are not, so the unit is code
#: points rather than bytes or UTF-16 units. Over the limit the server answers
#: MESSAGE_TOO_LONG — a code whose stock text is about messages, not folders.
FOLDER_TITLE_MAX_LEN = 12

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


def check_title(title: str) -> None:
    """Raise when *title* is longer than Telegram will store.

    Checked here rather than left to the server: the answer to an over-long
    title is ``MESSAGE_TOO_LONG``, which reads as though a message was sent.
    """
    if len(title) > FOLDER_TITLE_MAX_LEN:
        raise ValueError(
            f'Folder title "{title}" is {len(title)} characters; '
            f"Telegram allows at most {FOLDER_TITLE_MAX_LEN}."
        )


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
    # Saved Messages. Telethon resolves "me"/"self" itself; without this the
    # word falls into the display-name path, matches no dialog, and the chat is
    # skipped — which is what a live run of this server actually did.
    if ref.casefold() in ("me", "self"):
        return {"username": "me"}
    if _NUMERIC_REF.match(ref):
        return {"dialog_id": int(ref)}
    if ref.startswith("@"):
        return {"username": ref.lstrip("@")}
    return {"name": ref}


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
    # Telegram stores a cleared icon — and one it rejected, since folder icons
    # are a fixed set — as an empty string, while a folder that never had one
    # omits the field. Both mean "no icon"; report them the same way.
    emoticon = getattr(f, "emoticon", None) or None
    return Folder(
        id=getattr(f, "id", DEFAULT_FOLDER_ID),
        title=filter_title(f),
        kind=kind,
        emoji=emoticon,
        include=chats("include_peers"),
        pinned=chats("pinned_peers"),
        exclude=chats("exclude_peers"),
        **flags,
    )


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
    # Telegram's answer to an over-long folder title. Its stock text talks about
    # messages; these tools never send one, so the folder reading is the only one.
    "MESSAGE_TOO_LONG": (
        f"Folder title is too long — Telegram allows at most "
        f"{FOLDER_TITLE_MAX_LEN} characters."
    ),
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
