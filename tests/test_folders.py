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
