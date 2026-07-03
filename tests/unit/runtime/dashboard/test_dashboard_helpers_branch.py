"""Unit tests for the branch-new DashboardPreferencesStore (Phase 11 — §11.2).

Covers the SQLite persistence/scoping/validation logic that the API-route tests
(`tests/unit/api/test_routes_dashboard.py`) stub out with ``AsyncMock``. Mirrors
the temp-file sqlite store pattern used by the sibling stores
(``test_scoring_ledger_store.py``, ``test_tuning_delta_store.py``): a real
temp-file ``sqlite3`` connection via ``tmp_path`` — no network / live infra.
"""
import json

import pytest

from elephantbroker.runtime.dashboard.preferences_store import (
    DashboardPreferencesStore,
)
from elephantbroker.schemas.dashboard import SavedView, UserPreferences


@pytest.fixture
async def store(tmp_path):
    s = DashboardPreferencesStore(
        db_path=str(tmp_path / "prefs.db"), gateway_id="gw1"
    )
    await s.init_db()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class TestPreferences:
    async def test_get_preferences_defaults_when_empty(self, store):
        prefs = await store.get_preferences("actor-a")
        assert isinstance(prefs, UserPreferences)
        assert prefs.actor_id == "actor-a"
        assert prefs.theme == "light"
        assert prefs.items_per_page == 50
        assert prefs.default_page == "/"

    async def test_set_then_get_full_round_trip(self, store):
        model = UserPreferences(
            theme="dark",
            items_per_page=25,
            default_page="/memory",
            selected_gateway="gw1",
            preferences={"sidebar": "collapsed"},
        )
        returned = await store.set_preferences("actor-a", model)
        # actor_id is forced onto the returned model
        assert returned.actor_id == "actor-a"

        prefs = await store.get_preferences("actor-a")
        assert prefs.theme == "dark"
        assert prefs.items_per_page == 25
        assert prefs.default_page == "/memory"
        assert prefs.selected_gateway == "gw1"
        assert prefs.preferences == {"sidebar": "collapsed"}

    async def test_set_preferences_forces_caller_actor_id(self, store):
        # A model carrying someone else's actor_id must be re-scoped to caller.
        model = UserPreferences(actor_id="attacker", theme="dark")
        returned = await store.set_preferences("actor-a", model)
        assert returned.actor_id == "actor-a"

        # Nothing was written under the spoofed actor.
        other = await store.get_preferences("attacker")
        assert other.theme == "light"

    async def test_set_preferences_from_dict_drops_unknown_keys(self, store):
        returned = await store.set_preferences(
            "actor-a",
            {"theme": "dark", "bogus_key": "ignored", "items_per_page": 10},
        )
        assert returned.theme == "dark"
        assert returned.items_per_page == 10
        assert not hasattr(returned, "bogus_key")

        prefs = await store.get_preferences("actor-a")
        assert prefs.theme == "dark"
        assert prefs.items_per_page == 10

    async def test_set_single_preference_upserts_one_key(self, store):
        await store.set_preference("actor-a", "theme", "dark")
        prefs = await store.get_preferences("actor-a")
        assert prefs.theme == "dark"
        # Untouched keys keep their schema defaults.
        assert prefs.items_per_page == 50

    async def test_set_single_preference_unknown_key_raises(self, store):
        with pytest.raises(ValueError):
            await store.set_preference("actor-a", "not_a_field", "x")

    async def test_set_single_preference_actor_id_key_raises(self, store):
        # actor_id is the scoping column, never a stored preference value.
        with pytest.raises(ValueError):
            await store.set_preference("actor-a", "actor_id", "someone")

    async def test_set_preferences_requires_init(self, tmp_path):
        s = DashboardPreferencesStore(
            db_path=str(tmp_path / "noinit.db"), gateway_id="gw1"
        )
        with pytest.raises(RuntimeError):
            await s.set_preferences("actor-a", {"theme": "dark"})

    async def test_set_single_preference_requires_init(self, tmp_path):
        s = DashboardPreferencesStore(
            db_path=str(tmp_path / "noinit.db"), gateway_id="gw1"
        )
        with pytest.raises(RuntimeError):
            await s.set_preference("actor-a", "theme", "dark")

    async def test_preferences_scoped_per_actor(self, store):
        await store.set_preference("actor-a", "theme", "dark")
        prefs_b = await store.get_preferences("actor-b")
        assert prefs_b.theme == "light"

    async def test_get_preferences_skips_corrupt_row(self, store):
        # Directly plant an un-parseable value_json row; read must fall back to
        # the schema default for that key rather than raising.
        store._conn.execute(
            "INSERT INTO user_preferences "
            "(gateway_id, actor_id, key, value_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("gw1", "actor-a", "theme", "not-json{", "2026-01-01T00:00:00+00:00"),
        )
        store._conn.commit()
        prefs = await store.get_preferences("actor-a")
        assert prefs.theme == "light"

    async def test_get_preferences_ignores_unknown_stored_key(self, store):
        store._conn.execute(
            "INSERT INTO user_preferences "
            "(gateway_id, actor_id, key, value_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("gw1", "actor-a", "ghost", json.dumps("x"), "2026-01-01T00:00:00+00:00"),
        )
        store._conn.commit()
        prefs = await store.get_preferences("actor-a")
        assert isinstance(prefs, UserPreferences)
        assert prefs.actor_id == "actor-a"

    async def test_get_preferences_rebuild_falls_back_on_invalid_value(self, store):
        # A stored value that violates the schema (items_per_page le=200) must
        # not blow up the whole read — it returns pure defaults.
        store._conn.execute(
            "INSERT INTO user_preferences "
            "(gateway_id, actor_id, key, value_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("gw1", "actor-a", "items_per_page", json.dumps(9999),
             "2026-01-01T00:00:00+00:00"),
        )
        store._conn.commit()
        prefs = await store.get_preferences("actor-a")
        assert prefs.items_per_page == 50


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


class TestSavedViews:
    async def test_create_and_list_round_trip(self, store):
        created = await store.create_saved_view(
            "actor-a",
            {
                "name": "My facts",
                "resource": "memory",
                "filters": {"scope": "team"},
                "sort": {"by": "created_at", "order": "desc"},
            },
        )
        assert isinstance(created, SavedView)
        assert created.id  # auto-generated uuid
        assert created.actor_id == "actor-a"
        assert created.filters == {"scope": "team"}
        assert created.sort == {"by": "created_at", "order": "desc"}

        views = await store.list_saved_views("actor-a")
        assert len(views) == 1
        assert views[0].id == created.id
        assert views[0].name == "My facts"
        assert views[0].resource == "memory"
        assert views[0].filters == {"scope": "team"}
        assert views[0].sort == {"by": "created_at", "order": "desc"}

    async def test_create_saved_view_forces_caller_actor_id(self, store):
        created = await store.create_saved_view(
            "actor-a",
            {"name": "v", "resource": "memory", "actor_id": "attacker"},
        )
        assert created.actor_id == "actor-a"
        # Not visible to the spoofed actor.
        assert await store.list_saved_views("attacker") == []

    async def test_create_saved_view_honours_supplied_id_and_upserts(self, store):
        await store.create_saved_view(
            "actor-a", {"id": "v1", "name": "first", "resource": "memory"}
        )
        await store.create_saved_view(
            "actor-a", {"id": "v1", "name": "updated", "resource": "memory"}
        )
        views = await store.list_saved_views("actor-a")
        assert len(views) == 1
        assert views[0].id == "v1"
        assert views[0].name == "updated"

    async def test_list_filtered_by_resource(self, store):
        await store.create_saved_view(
            "actor-a", {"name": "m", "resource": "memory"}
        )
        await store.create_saved_view(
            "actor-a", {"name": "t", "resource": "traces"}
        )
        mem = await store.list_saved_views("actor-a", resource="memory")
        assert [v.resource for v in mem] == ["memory"]
        all_views = await store.list_saved_views("actor-a")
        assert len(all_views) == 2

    async def test_list_ordered_newest_first(self, store):
        await store.create_saved_view(
            "actor-a",
            {"id": "old", "name": "old", "resource": "memory",
             "created_at": "2026-01-01T00:00:00+00:00"},
        )
        await store.create_saved_view(
            "actor-a",
            {"id": "new", "name": "new", "resource": "memory",
             "created_at": "2026-02-01T00:00:00+00:00"},
        )
        views = await store.list_saved_views("actor-a")
        assert [v.id for v in views] == ["new", "old"]

    async def test_list_scoped_per_actor(self, store):
        await store.create_saved_view(
            "actor-a", {"name": "a", "resource": "memory"}
        )
        assert await store.list_saved_views("actor-b") == []

    async def test_delete_saved_view_success(self, store):
        created = await store.create_saved_view(
            "actor-a", {"name": "v", "resource": "memory"}
        )
        assert await store.delete_saved_view(created.id, "actor-a") is True
        assert await store.list_saved_views("actor-a") == []

    async def test_delete_saved_view_not_found_returns_false(self, store):
        assert await store.delete_saved_view("nope", "actor-a") is False

    async def test_delete_saved_view_wrong_actor_is_noop(self, store):
        created = await store.create_saved_view(
            "actor-a", {"name": "v", "resource": "memory"}
        )
        # Different actor cannot delete another actor's view.
        assert await store.delete_saved_view(created.id, "actor-b") is False
        assert len(await store.list_saved_views("actor-a")) == 1

    async def test_list_saved_views_without_init_returns_empty(self, tmp_path):
        s = DashboardPreferencesStore(
            db_path=str(tmp_path / "noinit.db"), gateway_id="gw1"
        )
        assert await s.list_saved_views("actor-a") == []

    async def test_create_saved_view_without_init_raises(self, tmp_path):
        s = DashboardPreferencesStore(
            db_path=str(tmp_path / "noinit.db"), gateway_id="gw1"
        )
        with pytest.raises(RuntimeError):
            await s.create_saved_view("actor-a", {"name": "v", "resource": "m"})

    async def test_delete_saved_view_without_init_returns_false(self, tmp_path):
        s = DashboardPreferencesStore(
            db_path=str(tmp_path / "noinit.db"), gateway_id="gw1"
        )
        assert await s.delete_saved_view("id", "actor-a") is False


# ---------------------------------------------------------------------------
# _row_to_view (corrupt-row resilience)
# ---------------------------------------------------------------------------


class TestRowToView:
    def test_valid_row_reconstructs_view(self):
        row = (
            "v1",
            "actor-a",
            "name",
            "memory",
            json.dumps({"filters": {"scope": "team"}, "sort": {"by": "created_at"}}),
            "2026-01-01T00:00:00+00:00",
        )
        view = DashboardPreferencesStore._row_to_view(row)
        assert isinstance(view, SavedView)
        assert view.id == "v1"
        assert view.filters == {"scope": "team"}
        assert view.sort == {"by": "created_at"}

    def test_corrupt_config_json_returns_none(self):
        row = ("v1", "actor-a", "name", "memory", "{not valid",
               "2026-01-01T00:00:00+00:00")
        assert DashboardPreferencesStore._row_to_view(row) is None

    def test_non_dict_config_falls_back_to_empty(self):
        row = ("v1", "actor-a", "name", "memory", json.dumps([1, 2, 3]),
               "2026-01-01T00:00:00+00:00")
        view = DashboardPreferencesStore._row_to_view(row)
        assert isinstance(view, SavedView)
        assert view.filters == {}
        assert view.sort == {}


# ---------------------------------------------------------------------------
# Gateway scoping (single-tenant-per-instance isolation)
# ---------------------------------------------------------------------------


class TestGatewayScoping:
    async def test_saved_views_isolated_across_gateways(self, tmp_path):
        db = str(tmp_path / "shared.db")
        gw1 = DashboardPreferencesStore(db_path=db, gateway_id="gw1")
        gw2 = DashboardPreferencesStore(db_path=db, gateway_id="gw2")
        await gw1.init_db()
        await gw2.init_db()
        try:
            created = await gw1.create_saved_view(
                "actor-a", {"name": "v", "resource": "memory"}
            )
            # gw2 sees nothing and cannot delete gw1's view.
            assert await gw2.list_saved_views("actor-a") == []
            assert await gw2.delete_saved_view(created.id, "actor-a") is False
            assert len(await gw1.list_saved_views("actor-a")) == 1
        finally:
            await gw1.close()
            await gw2.close()

    async def test_preferences_isolated_across_gateways(self, tmp_path):
        db = str(tmp_path / "shared.db")
        gw1 = DashboardPreferencesStore(db_path=db, gateway_id="gw1")
        gw2 = DashboardPreferencesStore(db_path=db, gateway_id="gw2")
        await gw1.init_db()
        await gw2.init_db()
        try:
            await gw1.set_preference("actor-a", "theme", "dark")
            prefs = await gw2.get_preferences("actor-a")
            assert prefs.theme == "light"
        finally:
            await gw1.close()
            await gw2.close()
