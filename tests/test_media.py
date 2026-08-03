"""Tests for tg_mcp.media — album grouping and path checks.

Pure functions: no Telethon, no network, no fixtures beyond tmp_path.
"""

from __future__ import annotations

from tg_mcp.media import (
    AUDIO,
    DOCUMENT,
    VISUAL,
    album_kind,
    expand_paths,
    group_for_albums,
    missing_files,
)


class TestAlbumKind:
    """Tests for album_kind()."""

    def test_photo_extensions_are_visual(self) -> None:
        """Images classify as visual."""
        for path in ("/a/b.png", "/a/b.jpg", "/a/b.jpeg", "/a/b.webp", "/a/b.heic"):
            assert album_kind(path) == VISUAL

    def test_video_extensions_are_visual(self) -> None:
        """Videos share the visual album class with photos, as Telegram allows."""
        for path in ("/a/b.mp4", "/a/b.mov", "/a/b.mkv", "/a/b.webm"):
            assert album_kind(path) == VISUAL

    def test_audio_extensions_are_audio(self) -> None:
        """Audio tracks are their own album class."""
        for path in ("/a/b.mp3", "/a/b.m4a", "/a/b.ogg", "/a/b.flac"):
            assert album_kind(path) == AUDIO

    def test_unknown_extension_is_document(self) -> None:
        """Anything unrecognised — including no extension — is a document."""
        for path in ("/a/b.pdf", "/a/b.zip", "/a/b.xyz", "/a/README"):
            assert album_kind(path) == DOCUMENT

    def test_extension_matching_is_case_insensitive(self) -> None:
        """Screenshots often arrive as .PNG; that must not become a document."""
        assert album_kind("/a/IMG_0001.PNG") == VISUAL
        assert album_kind("/a/Track.MP3") == AUDIO


class TestGroupForAlbums:
    """Tests for group_for_albums()."""

    def test_single_file_is_one_group(self) -> None:
        """One path yields one group holding it."""
        assert group_for_albums(["/a/1.png"]) == [["/a/1.png"]]

    def test_uniform_files_stay_in_one_group(self) -> None:
        """Photos and videos travel as a single album, order preserved."""
        paths = ["/a/1.png", "/a/2.mp4", "/a/3.jpg"]
        assert group_for_albums(paths) == [paths]

    def test_mixed_kinds_split_into_one_group_each(self) -> None:
        """A photo and a PDF cannot share an album, so they are split."""
        assert group_for_albums(["/a/1.png", "/a/2.pdf", "/a/3.jpg"]) == [
            ["/a/1.png", "/a/3.jpg"],
            ["/a/2.pdf"],
        ]

    def test_groups_are_ordered_visual_audio_document(self) -> None:
        """Group order is fixed regardless of input order: visual first."""
        assert group_for_albums(["/a/1.pdf", "/a/2.mp3", "/a/3.png"]) == [
            ["/a/3.png"],
            ["/a/2.mp3"],
            ["/a/1.pdf"],
        ]

    def test_empty_input_yields_no_groups(self) -> None:
        """An empty list produces no albums rather than an empty album."""
        assert group_for_albums([]) == []

    def test_more_than_ten_files_stay_one_group(self) -> None:
        """Chunking by ten is Telethon's job; we must not pre-split."""
        paths = [f"/a/{i}.png" for i in range(25)]
        assert group_for_albums(paths) == [paths]


class TestExpandPaths:
    """Tests for expand_paths()."""

    def test_tilde_is_expanded(self, monkeypatch) -> None:
        """~ becomes the home directory; Telethon would open("~/…") literally."""
        monkeypatch.setenv("HOME", "/home/tester")
        assert expand_paths(["~/pic.png"]) == ["/home/tester/pic.png"]

    def test_absolute_paths_pass_through(self) -> None:
        """Paths without ~ are returned unchanged."""
        assert expand_paths(["/a/1.png", "/b/2.pdf"]) == ["/a/1.png", "/b/2.pdf"]


class TestMissingFiles:
    """Tests for missing_files()."""

    def test_existing_files_are_not_reported(self, tmp_path) -> None:
        """A real file is not missing."""
        f = tmp_path / "real.txt"
        f.write_text("hi")
        assert missing_files([str(f)]) == []

    def test_missing_files_are_reported_in_input_order(self, tmp_path) -> None:
        """Every bad path is reported, keeping the caller's order."""
        f = tmp_path / "real.txt"
        f.write_text("hi")
        result = missing_files(["/nope/a.png", str(f), "/nope/b.png"])
        assert result == ["/nope/a.png", "/nope/b.png"]

    def test_directory_counts_as_missing(self, tmp_path) -> None:
        """A directory is not a sendable file."""
        assert missing_files([str(tmp_path)]) == [str(tmp_path)]
