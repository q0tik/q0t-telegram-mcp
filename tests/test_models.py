"""
Tests for data models defined in src/tg_mcp/models.py.

Contracts verified:
  - UserInfo, Dialog, Message are frozen dataclasses
  - All documented fields exist with the correct types
  - Frozen behaviour: assignment after creation raises FrozenInstanceError
"""

from __future__ import annotations

import dataclasses

import pytest

from tg_mcp.models import Dialog, Message, UserInfo


class TestUserInfo:
    """Tests for the UserInfo model (returned by tg_me)."""

    def test_userinfo_fields_are_correct(self) -> None:
        """
        Instantiating UserInfo with all fields produces an object whose
        attribute values exactly match the constructor arguments.

        Expected: id, first_name, last_name, username, phone are all
        accessible and equal to the values passed in.
        """
        user = UserInfo(
            id=123456789,
            first_name="Alice",
            last_name="Smith",
            username="alicesmith",
            phone="+15550001234",
        )
        assert user.id == 123456789
        assert user.first_name == "Alice"
        assert user.last_name == "Smith"
        assert user.username == "alicesmith"
        assert user.phone == "+15550001234"

    def test_userinfo_is_frozen(self) -> None:
        """
        UserInfo is a frozen dataclass.

        Expected: attempting to mutate any field after construction raises
        dataclasses.FrozenInstanceError (or AttributeError on older Python).
        """
        user = UserInfo(
            id=1,
            first_name="Alice",
            last_name=None,
            username=None,
            phone=None,
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            user.first_name = "Bob"  # type: ignore[misc]

    def test_userinfo_optional_fields_can_be_none(self) -> None:
        """
        last_name, username, and phone are declared as str | None.

        Expected: constructing UserInfo with None for those fields succeeds
        and the attributes hold None.
        """
        user = UserInfo(
            id=1,
            first_name="Alice",
            last_name=None,
            username=None,
            phone=None,
        )
        assert user.last_name is None
        assert user.username is None
        assert user.phone is None


class TestDialog:
    """Tests for the Dialog model (one entry returned by tg_dialogs)."""

    def test_dialog_fields_are_correct(self) -> None:
        """
        Instantiating Dialog with all fields produces an object whose
        attribute values match the constructor arguments.

        Expected: id, name, unread_count, is_user, is_group, is_channel,
        last_message, date are all accessible and equal to passed-in values.
        """
        dialog = Dialog(
            id=987654321,
            name="Alice Smith",
            username="alicesmith",
            unread_count=3,
            is_user=True,
            is_group=False,
            is_channel=False,
            last_message="Hey there!",
            date="2026-03-08T12:00:00+00:00",
        )
        assert dialog.id == 987654321
        assert dialog.name == "Alice Smith"
        assert dialog.username == "alicesmith"
        assert dialog.unread_count == 3
        assert dialog.is_user is True
        assert dialog.is_group is False
        assert dialog.is_channel is False
        assert dialog.last_message == "Hey there!"
        assert dialog.date == "2026-03-08T12:00:00+00:00"

    def test_dialog_is_frozen(self) -> None:
        """
        Dialog is a frozen dataclass.

        Expected: mutating any field after construction raises
        dataclasses.FrozenInstanceError.
        """
        dialog = Dialog(
            id=1,
            name="Test",
            username=None,
            unread_count=0,
            is_user=True,
            is_group=False,
            is_channel=False,
            last_message=None,
            date=None,
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            dialog.name = "Other"  # type: ignore[misc]

    def test_dialog_last_message_can_be_none(self) -> None:
        """
        last_message and date are optional (str | None).

        Expected: constructing Dialog with None for both succeeds.
        """
        dialog = Dialog(
            id=1,
            name="Empty Chat",
            username=None,
            unread_count=0,
            is_user=True,
            is_group=False,
            is_channel=False,
            last_message=None,
            date=None,
        )
        assert dialog.last_message is None
        assert dialog.date is None


class TestMessage:
    """Tests for the Message model (one entry returned by tg_dialog)."""

    def test_message_fields_are_correct(self) -> None:
        """
        Instantiating Message with all fields produces an object whose
        attribute values match the constructor arguments.

        Expected: id, date, text, out, sender are all accessible and match.
        """
        msg = Message(
            id=42,
            date="2026-03-08T11:30:00+00:00",
            text="Hello, world!",
            out=False,
            sender="Alice Smith",
        )
        assert msg.id == 42
        assert msg.date == "2026-03-08T11:30:00+00:00"
        assert msg.text == "Hello, world!"
        assert msg.out is False
        assert msg.sender == "Alice Smith"

    def test_message_is_frozen(self) -> None:
        """
        Message is a frozen dataclass.

        Expected: mutating any field after construction raises
        dataclasses.FrozenInstanceError.
        """
        msg = Message(
            id=1,
            date="2026-03-08T11:30:00+00:00",
            text="Hi",
            out=True,
            sender=None,
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            msg.text = "Changed"  # type: ignore[misc]

    def test_message_text_can_be_none(self) -> None:
        """
        text and sender are optional (str | None) to accommodate media-only
        messages and channel posts.

        Expected: constructing Message with None for both succeeds.
        """
        msg = Message(
            id=1,
            date="2026-03-08T11:30:00+00:00",
            text=None,
            out=False,
            sender=None,
        )
        assert msg.text is None
        assert msg.sender is None
