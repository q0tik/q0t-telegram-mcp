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
