"""Dashboard runtime support (Phase 11 — §11.2).

Backing stores for the read-heavy ``/dashboard/*`` API surface. Currently this
package holds :class:`DashboardPreferencesStore`, the SQLite-backed persistence
for per-actor dashboard preferences and saved filter/sort views.
"""
from __future__ import annotations

from elephantbroker.runtime.dashboard.preferences_store import DashboardPreferencesStore

__all__ = ["DashboardPreferencesStore"]
