"""ElephantBroker Ontology — entity types, relations, provenance.

This package provides the typed ontology layer: which entity types
exist, what fields they require, which relations are allowed between
them, and what constitutes a valid provenance reference.
"""
from elephantbroker.ontology.provenance import ProvenanceRef, typed_provenance_from_legacy
from elephantbroker.ontology.registry import (
    ALLOWED_RELATIONS,
    ENTITY_TYPES,
    get_required_fields,
    validate_entity_type,
    validate_relation,
)

__all__ = [
    "ALLOWED_RELATIONS",
    "ENTITY_TYPES",
    "ProvenanceRef",
    "get_required_fields",
    "typed_provenance_from_legacy",
    "validate_entity_type",
    "validate_relation",
]
