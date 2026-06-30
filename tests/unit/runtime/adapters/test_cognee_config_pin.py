"""TODO-5-006: verify _verify_cognee_pin warns on version drift."""
from __future__ import annotations

import logging
from importlib import metadata as _importlib_metadata
from unittest.mock import patch

from elephantbroker.runtime.adapters.cognee.config import (
    _SUPPORTED_COGNEE_VERSION,
    _cognee_relational_env_from_dsn,
    _verify_cognee_pin,
)


class TestCogneePinVerify:
    """The load-bearing ``cognee==1.2.2`` pin (TODO-5-006) protects the TD-50
    cascade's internal-API call sites. ``_verify_cognee_pin`` emits a WARNING
    when the installed version drifts so operators testing a bump see the
    signal on boot."""

    def test_supported_pin_tracks_phase_a_cognee_upgrade(self):
        assert _SUPPORTED_COGNEE_VERSION == "1.2.2"

    def test_matching_version_emits_no_warning(self, caplog):
        with patch.object(
            _importlib_metadata, "version", return_value=_SUPPORTED_COGNEE_VERSION,
        ):
            with caplog.at_level(
                logging.WARNING, logger="elephantbroker.adapters.cognee.config",
            ):
                _verify_cognee_pin()
        assert caplog.text == ""

    def test_mismatched_version_emits_warning_with_cascade_pointer(self, caplog):
        with patch.object(_importlib_metadata, "version", return_value="0.6.0"):
            with caplog.at_level(
                logging.WARNING, logger="elephantbroker.adapters.cognee.config",
            ):
                _verify_cognee_pin()
        assert "0.6.0" in caplog.text
        assert _SUPPORTED_COGNEE_VERSION in caplog.text
        assert "TD-50" in caplog.text
        assert "TECHNICAL-DEBT.md" in caplog.text

    def test_missing_package_metadata_emits_warning(self, caplog):
        def _raise_not_found(name):
            raise _importlib_metadata.PackageNotFoundError(name)

        with patch.object(_importlib_metadata, "version", side_effect=_raise_not_found):
            with caplog.at_level(
                logging.WARNING, logger="elephantbroker.adapters.cognee.config",
            ):
                _verify_cognee_pin()
        assert "metadata not found" in caplog.text


def test_cognee_relational_env_from_postgres_dsn():
    env = _cognee_relational_env_from_dsn(
        "postgresql://eb_user:secret@localhost:15432/elephantbroker",
    )

    assert env == {
        "DB_PROVIDER": "postgres",
        "DB_HOST": "localhost",
        "DB_PORT": "15432",
        "DB_USERNAME": "eb_user",
        "DB_PASSWORD": "secret",
        "DB_NAME": "elephantbroker",
        "MIGRATION_DB_PROVIDER": "postgres",
        "MIGRATION_DB_HOST": "localhost",
        "MIGRATION_DB_PORT": "15432",
        "MIGRATION_DB_USERNAME": "eb_user",
        "MIGRATION_DB_PASSWORD": "secret",
        "MIGRATION_DB_NAME": "elephantbroker",
    }
