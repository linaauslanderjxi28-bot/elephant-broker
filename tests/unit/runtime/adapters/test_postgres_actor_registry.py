from __future__ import annotations

import uuid

from elephantbroker.runtime.adapters.postgres.actor_registry import _row_to_actor
from elephantbroker.schemas.actor import ActorType


def test_row_to_actor_maps_actor_type_to_schema_type() -> None:
    actor_id = uuid.uuid4()
    org_id = uuid.uuid4()
    team_id = uuid.uuid4()

    actor = _row_to_actor({
        "id": actor_id,
        "display_name": "Smoke Worker",
        "actor_type": "worker_agent",
        "authority_level": 3,
        "handles": ["smoke"],
        "org_id": org_id,
        "team_ids": [str(team_id)],
        "trust_level": 0.8,
        "tags": ["runtime-smoke"],
        "gateway_id": "gw-enterprise-prod",
    })

    assert actor.id == actor_id
    assert actor.type is ActorType.WORKER_AGENT
    assert actor.display_name == "Smoke Worker"
    assert actor.team_ids == [team_id]
    assert actor.gateway_id == "gw-enterprise-prod"
