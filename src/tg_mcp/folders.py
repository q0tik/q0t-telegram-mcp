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
from collections.abc import Iterable, Sequence
from typing import Any

import telethon.utils
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
