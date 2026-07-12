#!/usr/bin/env python3
"""Append-only standardization backfill for the cross-border trade graph.

Creates canonical shadow nodes and CANONICALIZES_TO edges. It never updates or
deletes existing business nodes/relations. Run without --apply for a dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from neo4j import AsyncGraphDatabase

from elephantbroker.schemas.config import ElephantBrokerConfig

RUN_NAMESPACE = "trade_standard_v1"
COUNTRY_ALIASES = {
    "cn": "China",
    "china": "China",
    "pr china": "China",
    "us": "United States",
    "usa": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "korea, republic of": "Korea, Republic of",
    "south korea": "Korea, Republic of",
    "turkiye": "Türkiye",
    "turkey": "Türkiye",
    "hong kong": "Hong Kong",
    "taiwan": "Taiwan, Province of China",
}
TARGETS = {
    "Country": ("CanonicalCountry", "ISO_3166_1"),
    "Market": ("CanonicalMarket", "MARKET_SCOPE"),
    "Port": ("CanonicalPort", "UN_LOCODE"),
    "HSCode": ("CanonicalCommodity", "HS"),
    "TariffRule": ("CanonicalTariffRule", "TARIFF_ROUTE"),
    "Supplier": ("CanonicalOrganization", "ORG_NAME_PROVISIONAL"),
    "ExporterCompany": ("CanonicalOrganization", "ORG_NAME_PROVISIONAL"),
    "ExhibitorCompany": ("CanonicalOrganization", "ORG_NAME_PROVISIONAL"),
    "TradeProduct": ("CanonicalProductConcept", "PRODUCT_NAME_PROVISIONAL"),
    "Certification": ("CanonicalCertification", "CERTIFICATION_SCHEME"),
    "ExpoEdition": ("CanonicalExpoEdition", "EXPO_EDITION"),
}


def normalize_key(value: str) -> str:
    ascii_value = value.lower().encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if key:
        return key[:180]
    return f"u-{uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:24]}"


def _iso3166_by_name() -> dict[str, dict[str, str]]:
    path = "/usr/share/iso-codes/json/iso_3166-1.json"
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as stream:
        rows = json.load(stream).get("3166-1", [])
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        record = {
            "canonical_name": row["name"],
            "iso2": row["alpha_2"],
            "iso3": row["alpha_3"],
            "un_m49": row.get("numeric", ""),
            "identity_status": "standardized",
        }
        result[row["name"].lower()] = record
        result[row.get("official_name", row["name"]).lower()] = record
    return result


ISO3166_BY_NAME = _iso3166_by_name()


def country_properties(name: str) -> tuple[str, dict[str, Any]] | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    normalized = COUNTRY_ALIASES.get(raw.lower(), raw)
    record = ISO3166_BY_NAME.get(normalized.lower())
    if record is None and len(normalized) == 2:
        record = next((value for value in ISO3166_BY_NAME.values() if value["iso2"] == normalized.upper()), None)
    if record is None:
        return None
    return record["iso2"], record


def canonicalize(label: str, props: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    name = str(props.get("name") or props.get("code") or "").strip()
    if label == "Country":
        return country_properties(name)
    if not name:
        return None
    if label == "Port":
        code = str(props.get("code") or "").upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code):
            return None
        return code, {"canonical_name": name, "un_locode": code, "identity_status": "standardized"}
    if label == "HSCode":
        code = re.sub(r"\D", "", str(props.get("code") or name))
        if not 4 <= len(code) <= 10:
            return None
        return code, {"canonical_name": code, "hs_code": code, "hs_digits": len(code), "hs_version": "unspecified"}
    if label == "TariffRule":
        key = str(props.get("trade_id") or name)
        return normalize_key(key), {"canonical_name": name, "route_key": key, "identity_status": "source_defined"}
    if label == "Market":
        code = str(props.get("code") or name).upper()
        return normalize_key(code), {"canonical_name": code, "market_code": code, "identity_status": "source_defined"}
    if label == "ExpoEdition":
        expo_id, edition = str(props.get("expo_id") or ""), str(props.get("edition") or "")
        key = normalize_key(f"{expo_id}:{edition}" if expo_id and edition else name)
        return key, {
            "canonical_name": name,
            "expo_id": expo_id,
            "edition": edition,
            "identity_status": "official_key" if expo_id and edition else "provisional_name_based",
        }
    return normalize_key(name), {"canonical_name": name, "identity_status": "provisional_name_based"}


ISO4217_CODES = {"CNY", "EUR", "JPY", "MXN", "USD"}


async def add_currency_master_data(session, *, apply: bool, now: str, run_id: str) -> int:
    if not apply:
        return len(ISO4217_CODES)
    rows = json.load(open("/usr/share/iso-codes/json/iso_4217.json", encoding="utf-8"))["4217"]
    by_code = {row["alpha_3"]: row for row in rows}
    for code in ISO4217_CODES:
        row = by_code[code]
        await session.run(
            """
            MERGE (n:CanonicalEntity:CanonicalCurrency {namespace:$namespace,canonical_key:$code})
            ON CREATE SET n.created_at=$now
            SET n.canonical_name=$name,n.iso4217=$code,n.numeric_code=$numeric,
                n.scheme='ISO_4217',n.identity_status='standardized',n.updated_at=$now,
                n.standardization_run_id=$run_id
            """,
            namespace=RUN_NAMESPACE,
            code=code,
            name=row["name"],
            numeric=row["numeric"],
            now=now,
            run_id=run_id,
        )
    return len(ISO4217_CODES)


async def run(config: ElephantBrokerConfig, apply: bool) -> dict[str, Any]:
    driver = AsyncGraphDatabase.driver(
        config.cognee.neo4j_uri, auth=(config.cognee.neo4j_user, config.cognee.neo4j_password)
    )
    run_id = f"std-{uuid.uuid4()}"
    stats: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    now = datetime.now(UTC).isoformat()
    try:
        async with driver.session() as session:
            if apply:
                await session.run(
                    """
                    CREATE CONSTRAINT canonical_entity_key IF NOT EXISTS
                    FOR (n:CanonicalEntity) REQUIRE (n.namespace,n.canonical_key) IS UNIQUE
                    """
                )
            currency_count = await add_currency_master_data(session, apply=apply, now=now, run_id=run_id)
            stats["Currency"] = currency_count
            for label, (canonical_label, scheme) in TARGETS.items():
                result = await session.run(
                    f"MATCH (n:{label}) RETURN elementId(n) AS source_id, properties(n) AS props"
                )
                for row in await result.data():
                    canonical = canonicalize(label, row["props"])
                    if canonical is None:
                        skipped[label] += 1
                        continue
                    canonical_key, fields = canonical
                    stats[label] += 1
                    if not apply:
                        continue
                    await session.run(
                        f"""
                        MATCH (source) WHERE elementId(source)=$source_id
                        MERGE (target:CanonicalEntity:{canonical_label} {{
                            namespace:$namespace, canonical_key:$canonical_key
                        }})
                        ON CREATE SET target.created_at=$now
                        SET target += $fields, target.scheme=$scheme, target.updated_at=$now
                        MERGE (source)-[r:CANONICALIZES_TO {{
                            namespace:$namespace, scheme:$scheme
                        }}]->(target)
                        ON CREATE SET r.created_at=$now, r.standardization_run_id=$run_id
                        SET r.updated_at=$now
                        """,
                        source_id=row["source_id"],
                        namespace=RUN_NAMESPACE,
                        canonical_key=canonical_key,
                        fields=fields,
                        scheme=scheme,
                        now=now,
                        run_id=run_id,
                    )
    finally:
        await driver.close()
    return {
        "mode": "apply" if apply else "dry_run",
        "run_id": run_id,
        "eligible": dict(stats),
        "skipped": dict(skipped),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", default="/etc/elephantbroker/default.yaml")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(run(ElephantBrokerConfig.load(args.config_path), args.apply)))


if __name__ == "__main__":
    main()
