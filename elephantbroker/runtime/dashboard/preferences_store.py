"""SQLite-backed persistence for dashboard preferences + saved views (Phase 11 — §11.2).

Backs the ``/dashboard/preferences`` and ``/dashboard/saved-views`` endpoints in
``api/routes/dashboard.py`` (resolved there via ``_get_prefs_store``). Follows the
same pattern as :class:`~elephantbroker.runtime.guards.custom_rule_store.CustomRuleStore`
and the Phase 5 audit stores: a synchronous ``sqlite3.Connection`` held on
``self._conn``, ``async def`` wrappers around the sync sqlite calls, ``init_db()``
for table creation, and ``ON CONFLICT DO UPDATE`` for upsert.

The store is single-tenant per instance: ``gateway_id`` is bound at construction
and every query is scoped by it, so a store built for one gateway can never read
or write another gateway's rows. Callers pass ``actor_id`` for per-actor scoping.

Tables::

    user_preferences (
        gateway_id TEXT,
        actor_id   TEXT,
        key        TEXT,
        value_json TEXT,   -- json.dumps(value)
        updated_at TEXT,
        PRIMARY KEY (gateway_id, actor_id, key)
    )

    saved_views (
        id          TEXT,
        gateway_id  TEXT,
        actor_id    TEXT,
        name        TEXT,
        resource    TEXT,
        config_json TEXT,  -- json.dumps({"filters": {...}, "sort": {...}})
        created_at  TEXT,
        PRIMARY KEY (gateway_id, id)
    )
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime

from elephantbroker.schemas.dashboard import SavedView, UserPreferences

logger = logging.getLogger(__name__)

# Top-level ``UserPreferences`` fields persisted as individual key rows.
# ``actor_id`` is the scoping column, never a stored preference value.
_PREF_KEYS = tuple(k for k in UserPreferences.model_fields if k != "actor_id")


class DashboardPreferencesStore:
    """SQLite persistence for per-actor dashboard preferences and saved views.

    Instantiate with a db path and the process gateway id, then ``await init_db()``
    before use::

        store = DashboardPreferencesStore("data/dashboard_prefs.db", gateway_id="gw1")
        await store.init_db()
    """

    def __init__(
        self, db_path: str = "data/dashboard_preferences.db", *, gateway_id: str = ""
    ) -> None:
        self._db_path = db_path
        self._gateway_id = gateway_id
        self._conn: sqlite3.Connection | None = None

    async def init_db(self) -> None:
        """Create the tables if they don't exist."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS user_preferences (
                gateway_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (gateway_id, actor_id, key)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS saved_views (
                id TEXT NOT NULL,
                gateway_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                name TEXT NOT NULL,
                resource TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (gateway_id, id)
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_views_actor "
            "ON saved_views (gateway_id, actor_id, resource)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def get_preferences(self, actor_id: str) -> UserPreferences:
        """Return an actor's dashboard preferences (schema defaults where unset)."""
        data: dict = {"actor_id": actor_id}
        if self._conn is not None:
            cursor = self._conn.execute(
                "SELECT key, value_json FROM user_preferences "
                "WHERE gateway_id = ? AND actor_id = ?",
                (self._gateway_id, actor_id),
            )
            for key, value_json in cursor.fetchall():
                if key not in UserPreferences.model_fields:
                    continue
                try:
                    data[key] = json.loads(value_json)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "dashboard prefs: corrupt row actor=%s key=%s: %s",
                        actor_id,
                        key,
                        exc,
                    )
        try:
            return UserPreferences(**data)
        except Exception as exc:  # noqa: BLE001 - never let a bad row break reads
            logger.warning("dashboard prefs: rebuild failed actor=%s: %s", actor_id, exc)
            return UserPreferences(actor_id=actor_id)

    async def set_preferences(
        self, actor_id: str, prefs: UserPreferences | dict
    ) -> UserPreferences:
        """Persist an actor's full preferences object (upsert per field)."""
        if self._conn is None:
            raise RuntimeError(
                "DashboardPreferencesStore not initialized — call init_db() first"
            )
        if isinstance(prefs, UserPreferences):
            model = prefs.model_copy(update={"actor_id": actor_id})
        else:
            clean = {
                k: v
                for k, v in dict(prefs or {}).items()
                if k in UserPreferences.model_fields
            }
            clean["actor_id"] = actor_id
            model = UserPreferences(**clean)

        dumped = model.model_dump(mode="json")
        now = datetime.now(UTC).isoformat()
        for key in _PREF_KEYS:
            self._conn.execute(
                """INSERT INTO user_preferences
                       (gateway_id, actor_id, key, value_json, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (gateway_id, actor_id, key) DO UPDATE SET
                       value_json = excluded.value_json,
                       updated_at = excluded.updated_at""",
                (self._gateway_id, actor_id, key, json.dumps(dumped.get(key)), now),
            )
        self._conn.commit()
        return model

    async def set_preference(self, actor_id: str, key: str, value) -> None:
        """Upsert a single preference key for an actor."""
        if self._conn is None:
            raise RuntimeError(
                "DashboardPreferencesStore not initialized — call init_db() first"
            )
        if key not in UserPreferences.model_fields or key == "actor_id":
            raise ValueError(f"Unknown preference key: {key}")
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO user_preferences
                   (gateway_id, actor_id, key, value_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (gateway_id, actor_id, key) DO UPDATE SET
                   value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
            (self._gateway_id, actor_id, key, json.dumps(value), now),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Saved views
    # ------------------------------------------------------------------

    async def list_saved_views(
        self, actor_id: str, resource: str | None = None
    ) -> list[SavedView]:
        """List an actor's saved views, optionally filtered by resource."""
        if self._conn is None:
            return []
        query = (
            "SELECT id, actor_id, name, resource, config_json, created_at "
            "FROM saved_views WHERE gateway_id = ? AND actor_id = ?"
        )
        params: list = [self._gateway_id, actor_id]
        if resource is not None:
            query += " AND resource = ?"
            params.append(resource)
        query += " ORDER BY created_at DESC, id"
        cursor = self._conn.execute(query, tuple(params))
        views: list[SavedView] = []
        for row in cursor.fetchall():
            view = self._row_to_view(row)
            if view is not None:
                views.append(view)
        return views

    async def create_saved_view(
        self, actor_id: str, view: SavedView | dict
    ) -> SavedView:
        """Create a saved view for an actor. Returns the persisted view."""
        if self._conn is None:
            raise RuntimeError(
                "DashboardPreferencesStore not initialized — call init_db() first"
            )
        if isinstance(view, SavedView):
            payload = view.model_dump(mode="json")
        else:
            payload = dict(view or {})

        view_id = str(payload.get("id") or uuid.uuid4())
        created_at = payload.get("created_at") or datetime.now(UTC).isoformat()
        model = SavedView(
            id=view_id,
            actor_id=actor_id,  # scope to caller; never trust supplied actor_id
            name=str(payload.get("name", "")),
            resource=str(payload.get("resource", "")),
            filters=dict(payload.get("filters") or {}),
            sort=dict(payload.get("sort") or {}),
            created_at=created_at,
        )
        config_json = json.dumps({"filters": model.filters, "sort": model.sort})
        self._conn.execute(
            """INSERT INTO saved_views
                   (id, gateway_id, actor_id, name, resource, config_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (gateway_id, id) DO UPDATE SET
                   actor_id = excluded.actor_id,
                   name = excluded.name,
                   resource = excluded.resource,
                   config_json = excluded.config_json""",
            (
                model.id,
                self._gateway_id,
                actor_id,
                model.name,
                model.resource,
                config_json,
                model.created_at.isoformat()
                if isinstance(model.created_at, datetime)
                else str(model.created_at),
            ),
        )
        self._conn.commit()
        logger.info(
            "dashboard: created saved view id=%s actor=%s gateway=%s",
            model.id,
            actor_id,
            self._gateway_id,
        )
        return model

    async def delete_saved_view(self, view_id: str, actor_id: str) -> bool:
        """Delete a saved view owned by an actor. Returns ``True`` if removed."""
        if self._conn is None:
            return False
        cursor = self._conn.execute(
            "DELETE FROM saved_views "
            "WHERE gateway_id = ? AND actor_id = ? AND id = ?",
            (self._gateway_id, actor_id, view_id),
        )
        self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(
                "dashboard: deleted saved view id=%s actor=%s gateway=%s",
                view_id,
                actor_id,
                self._gateway_id,
            )
        return deleted

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_view(row) -> SavedView | None:
        """Reconstruct a :class:`SavedView` from a stored row.

        Returns ``None`` (and logs) on a corrupt row so one bad record cannot
        break a whole listing.
        """
        view_id, actor_id, name, resource, config_json, created_at = row
        try:
            config = json.loads(config_json) if config_json else {}
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("dashboard: skipping corrupt saved view %s: %s", view_id, exc)
            return None
        if not isinstance(config, dict):
            config = {}
        try:
            return SavedView(
                id=str(view_id),
                actor_id=actor_id,
                name=str(name),
                resource=str(resource),
                filters=dict(config.get("filters") or {}),
                sort=dict(config.get("sort") or {}),
                created_at=created_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: skipping invalid saved view %s: %s", view_id, exc)
            return None
