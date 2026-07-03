"""Tests for the branch-new ConsolidationReportStore.get_suggestion().

Covers the NEW async get_suggestion(suggestion_id, gateway_id):
  - returns the stored row (including draft_procedure_json + tool_sequence_json)
  - is gateway-scoped: a cross-gateway id returns None
  - returns None for a missing id / uninitialised store.

Mirrors the tmp_path SQLite fixture style of test_report_store.py in this dir;
SQLite is a local file (no network/infra), so no external I/O is touched.
"""
import json

import pytest

from elephantbroker.runtime.consolidation.report_store import ConsolidationReportStore


@pytest.fixture
async def store(tmp_path):
    s = ConsolidationReportStore(db_path=str(tmp_path / "reports.db"))
    await s.init_db()
    yield s
    await s.close()


def _suggestion(**overrides):
    base = {
        "id": "s1",
        "report_id": "r1",
        "gateway_id": "gw",
        "pattern_description": "read then write",
        "tool_sequence": ["read_file", "write_file"],
        "sessions_observed": 4,
        "draft_procedure": {"name": "read_write", "steps": ["read", "write"]},
        "confidence": 0.9,
        "approval_status": "pending",
    }
    base.update(overrides)
    return base


class TestGetSuggestion:
    async def test_returns_stored_row_with_json_columns(self, store):
        """Happy path: id + gateway match -> full row dict incl. both JSON cols."""
        await store.save_suggestion(_suggestion())

        row = await store.get_suggestion("s1", "gw")

        assert row is not None
        assert isinstance(row, dict)
        assert row["id"] == "s1"
        assert row["report_id"] == "r1"
        assert row["gateway_id"] == "gw"
        assert row["pattern_description"] == "read then write"
        assert row["sessions_observed"] == 4
        assert row["confidence"] == 0.9
        assert row["approval_status"] == "pending"

    async def test_draft_procedure_and_tool_sequence_json_are_the_stored_payload(self, store):
        """draft_procedure_json + tool_sequence_json come back as the exact stored JSON."""
        await store.save_suggestion(_suggestion())

        row = await store.get_suggestion("s1", "gw")

        assert row is not None
        # Columns are present (this is what promote_suggestion_to_procedure consumes).
        assert "draft_procedure_json" in row
        assert "tool_sequence_json" in row
        assert json.loads(row["tool_sequence_json"]) == ["read_file", "write_file"]
        assert json.loads(row["draft_procedure_json"]) == {
            "name": "read_write",
            "steps": ["read", "write"],
        }

    async def test_cross_gateway_id_returns_none(self, store):
        """Gateway scoping: an id that exists under a DIFFERENT gateway returns None."""
        await store.save_suggestion(_suggestion(id="s1", gateway_id="gw-a"))

        # Same id, wrong gateway -> must not leak across gateways.
        assert await store.get_suggestion("s1", "gw-b") is None
        # Sanity: correct gateway still finds it.
        assert (await store.get_suggestion("s1", "gw-a")) is not None

    async def test_missing_id_returns_none(self, store):
        await store.save_suggestion(_suggestion(id="s1", gateway_id="gw"))
        assert await store.get_suggestion("does-not-exist", "gw") is None

    async def test_returns_none_when_db_not_initialised(self, tmp_path):
        """No open connection (init_db not called) -> None, not an error."""
        s = ConsolidationReportStore(db_path=str(tmp_path / "unused.db"))
        assert await s.get_suggestion("s1", "gw") is None

    async def test_draft_procedure_json_is_none_when_absent(self, store):
        """When no draft_procedure was saved, the column round-trips as None."""
        await store.save_suggestion(_suggestion(id="s2", draft_procedure=None))

        row = await store.get_suggestion("s2", "gw")

        assert row is not None
        assert row["draft_procedure_json"] is None
        # tool_sequence_json is always populated (defaults to []).
        assert json.loads(row["tool_sequence_json"]) == ["read_file", "write_file"]
