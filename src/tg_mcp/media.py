"""Grouping of local file paths into Telegram-legal albums.

Telegram refuses an album that mixes media classes: photos and videos may share
one album, audio tracks may share another, and everything else travels as
documents — but a photo and a PDF in the same album is rejected by the server.
Callers hand us an arbitrary list of paths, so we split it into groups that
Telegram will actually accept instead of letting the send fail.

The functions here touch no network and no Telethon types, which is why they
live outside ``client.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

# Extensions Telegram renders inline as photos or videos. These two share an
# album class, which is why the set is not split further.
_VISUAL_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif",
    ".tif", ".tiff",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".mpeg", ".mpg",
})

_AUDIO_SUFFIXES = frozenset({
    ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".wav", ".aac", ".wma",
})

VISUAL = "visual"
AUDIO = "audio"
DOCUMENT = "document"

# Album classes in the order we emit them when a call mixes several. Visual
# first because a caption most often belongs to the screenshots.
_KIND_ORDER = (VISUAL, AUDIO, DOCUMENT)


def album_kind(path: str) -> str:
    """Return the album class of ``path``: visual, audio, or document.

    Classification is by extension only — the file need not exist. Anything
    unrecognised is a document, which is what Telegram does with it anyway.
    """
    suffix = Path(path).suffix.lower()
    if suffix in _VISUAL_SUFFIXES:
        return VISUAL
    if suffix in _AUDIO_SUFFIXES:
        return AUDIO
    return DOCUMENT


def group_for_albums(paths: list[str]) -> list[list[str]]:
    """Split ``paths`` into groups that Telegram accepts as single albums.

    All paths of one album class end up in one group, even when other classes
    sit between them in the input: ``[a.png, b.pdf, c.jpg]`` yields
    ``[[a.png, c.jpg], [b.pdf]]``. Order within a group is the caller's order,
    and groups come out visual → audio → document.

    Groups longer than Telegram's ten-item album limit are not split here —
    Telethon's ``send_file`` already chunks a list by ten.
    """
    buckets: dict[str, list[str]] = {}
    for path in paths:
        buckets.setdefault(album_kind(path), []).append(path)
    return [buckets[kind] for kind in _KIND_ORDER if kind in buckets]


def expand_paths(paths: list[str]) -> list[str]:
    """Expand ``~`` in each path. Telethon passes paths to open() as given."""
    return [os.path.expanduser(p) for p in paths]


def missing_files(paths: list[str]) -> list[str]:
    """Return the paths that are not regular files, in input order.

    Checked before anything is sent: a bad path halfway through a list would
    otherwise leave the first albums delivered and the rest lost.
    """
    return [p for p in paths if not os.path.isfile(p)]
