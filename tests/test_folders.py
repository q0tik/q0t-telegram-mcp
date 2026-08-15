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
