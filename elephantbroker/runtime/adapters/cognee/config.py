"""Map ElephantBroker config to Cognee SDK settings."""
from __future__ import annotations

import logging
import os
from importlib import metadata as _importlib_metadata

from elephantbroker.schemas.config import CogneeConfig, LLMConfig

_SUPPORTED_COGNEE_VERSION = "1.2.2"

_log = logging.getLogger("elephantbroker.adapters.cognee.config")


def _verify_cognee_pin() -> None:
    """Warn if installed Cognee version differs from the verified pin."""
    try:
        installed = _importlib_metadata.version("cognee")
    except _importlib_metadata.PackageNotFoundError:
        _log.warning("Cognee package metadata not found — cannot verify version pin")
        return
    if installed != _SUPPORTED_COGNEE_VERSION:
        _log.warning(
            "Cognee version %s differs from the verified pin %s — "
            "TD-50 cascade paths (MemoryStoreFacade._cascade_cognee_data) use "
            "Cognee internal APIs and MUST be re-verified before running on an "
            "unpinned version. See local/TECHNICAL-DEBT.md §Load-bearing "
            "dependency pins.",
            installed,
            _SUPPORTED_COGNEE_VERSION,
        )


async def configure_cognee(
    config: CogneeConfig,
    llm_config: LLMConfig | None = None,
    gateway_id: str = "",
) -> None:
    _verify_cognee_pin()
    import cognee
    from cognee.infrastructure.databases.vector.embeddings.config import get_embedding_config

    from elephantbroker.runtime.adapters.cognee.qdrant_adapter import register_qdrant_adapter

    register_qdrant_adapter()

    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")
    os.environ.setdefault("COGNEE_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("TELEMETRY_DISABLED", "true")

    cognee.config.set_graph_database_provider("neo4j")
    cognee.config.set_graph_db_config({
        "graph_database_url": config.neo4j_uri,
        "graph_database_username": config.neo4j_user,
        "graph_database_password": config.neo4j_password,
        "graph_dataset_database_handler": "neo4j",
    })

    cognee.config.set_vector_db_provider("qdrant")
    vector_db_cfg: dict[str, object] = {
        "vector_db_url": config.qdrant_url,
        "vector_dataset_database_handler": "qdrant",
    }
    if gateway_id:
        vector_db_cfg["vector_db_name"] = gateway_id
    cognee.config.set_vector_db_config(vector_db_cfg)

    if llm_config:
        cognee.config.set_llm_config({
            "llm_provider": "openai",
            "llm_model": llm_config.model,
            "llm_endpoint": llm_config.endpoint,
            "llm_api_key": llm_config.api_key,
        })
    else:
        _log.warning("LLM config not provided, falling back to embedding config")
        cognee.config.set_llm_config({
            "llm_provider": config.embedding_provider,
            "llm_model": config.embedding_model,
            "llm_endpoint": config.embedding_endpoint,
            "llm_api_key": config.embedding_api_key or "unused",
        })

    embedding_cfg = get_embedding_config()
    embedding_cfg.embedding_provider = config.embedding_provider
    embedding_cfg.embedding_model = config.embedding_model
    embedding_cfg.embedding_dimensions = config.embedding_dimensions
    if config.embedding_endpoint:
        embedding_cfg.embedding_endpoint = config.embedding_endpoint
    if config.embedding_api_key:
        embedding_cfg.embedding_api_key = config.embedding_api_key
