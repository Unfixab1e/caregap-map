"""Reviewer-note store: SQLite roundtrip and input validation."""

import pytest

from caregap_map.persistence import ReviewNote, SqliteReviewStore


@pytest.fixture
def store(tmp_path):
    return SqliteReviewStore(tmp_path / "reviews.db")


class TestSqliteReviewStore:
    def test_add_and_list_roundtrip(self, store):
        note = ReviewNote(
            scope_type="district",
            scope_id="Kerala/Ernakulam",
            note="Verify these facilities before classifying this district as an ICU desert.",
            author="planner-1",
        )
        saved = store.add_note(note)
        assert saved.id is not None
        assert saved.created_at

        notes = store.list_notes(scope_type="district", scope_id="Kerala/Ernakulam")
        assert len(notes) == 1
        assert notes[0].note.startswith("Verify these facilities")

    def test_scope_filtering(self, store):
        store.add_note(ReviewNote(scope_type="facility", scope_id="fac-1", note="check A"))
        store.add_note(ReviewNote(scope_type="facility", scope_id="fac-2", note="check B"))
        store.add_note(ReviewNote(scope_type="state", scope_id="Kerala", note="state note"))

        assert len(store.list_notes()) == 3
        assert len(store.list_notes(scope_type="facility")) == 2
        assert len(store.list_notes(scope_type="facility", scope_id="fac-1")) == 1

    def test_empty_note_rejected(self, store):
        with pytest.raises(ValueError):
            store.add_note(ReviewNote(scope_type="facility", scope_id="fac-1", note="   "))

    def test_invalid_scope_rejected(self, store):
        with pytest.raises(ValueError):
            store.add_note(ReviewNote(scope_type="country", scope_id="India", note="nope"))

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "reviews.db"
        SqliteReviewStore(path).add_note(ReviewNote(scope_type="state", scope_id="Bihar", note="revisit"))
        assert len(SqliteReviewStore(path).list_notes()) == 1
