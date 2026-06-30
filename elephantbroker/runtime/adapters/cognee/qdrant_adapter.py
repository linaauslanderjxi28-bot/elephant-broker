"""Qdrant vector adapter registered into Cognee 1.2's community adapter hook."""
from __future__ import annotations

import asyncio
from uuid import UUID

from cognee.infrastructure.databases.exceptions import MissingQueryParameterError
from cognee.infrastructure.databases.vector import VectorDBInterface, use_vector_adapter
from cognee.infrastructure.databases.vector.exceptions import CollectionNotFoundError
from cognee.infrastructure.databases.vector.models.ScoredResult import ScoredResult
from cognee.infrastructure.engine import DataPoint
from cognee.infrastructure.engine.models.DataPoint import MetaData
from cognee.infrastructure.engine.utils import parse_id
from cognee.shared.logging_utils import get_logger
from pydantic import Field
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

logger = get_logger("EBQdrantAdapter")


class IndexSchema(DataPoint):
    text: str
    document_id: str | None = None
    document_name: str | None = None
    chunk_index: int | None = None
    source_chunk_id: str | None = None
    importance_weight: float | None = None
    belongs_to_set: list[DataPoint] | list[str] | None = Field(default_factory=list)
    metadata: MetaData = {"index_fields": ["text"]}


class QdrantAdapter(VectorDBInterface):
    name = "Qdrant"

    def __init__(
        self,
        url: str,
        api_key: str,
        embedding_engine,
        database_name: str = "",
        qdrant_path: str | None = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.embedding_engine = embedding_engine
        self.database_name = database_name
        self.qdrant_path = qdrant_path
        self._lock = asyncio.Lock()

    def get_qdrant_client(self) -> AsyncQdrantClient:
        if self.qdrant_path is not None:
            return AsyncQdrantClient(path=self.qdrant_path)
        if self.url:
            return AsyncQdrantClient(url=self.url, api_key=self.api_key or None)
        return AsyncQdrantClient(location=":memory:")

    async def embed_data(self, data: list[str]) -> list[list[float]]:
        return await self.embedding_engine.embed_text(data)

    async def has_collection(self, collection_name: str) -> bool:
        client = self.get_qdrant_client()
        try:
            return await client.collection_exists(collection_name)
        finally:
            await client.close()

    async def create_collection(self, collection_name: str, payload_schema=None) -> None:
        async with self._lock:
            client = self.get_qdrant_client()
            try:
                if await client.collection_exists(collection_name):
                    return
                await client.create_collection(
                    collection_name=collection_name,
                    vectors_config={
                        "text": models.VectorParams(
                            size=self.embedding_engine.get_vector_size(),
                            distance=models.Distance.COSINE,
                        ),
                    },
                    hnsw_config=models.HnswConfigDiff(payload_m=16, m=0),
                )
                await client.create_payload_index(
                    collection_name=collection_name,
                    field_name="database_name",
                    field_schema=models.KeywordIndexParams(
                        type=models.KeywordIndexType.KEYWORD,
                        is_tenant=True,
                    ),
                )
            finally:
                await client.close()

    async def create_data_points(self, collection_name: str, data_points: list[DataPoint]) -> None:
        client = self.get_qdrant_client()
        try:
            data_vectors = await self.embed_data([
                str(DataPoint.get_embeddable_data(data_point) or "")
                for data_point in data_points
            ])
            points = [
                models.PointStruct(
                    id=str(data_point.id),
                    payload={**data_point.model_dump(), "database_name": self.database_name},
                    vector={"text": data_vectors[index]},
                )
                for index, data_point in enumerate(data_points)
            ]
            await client.upsert(collection_name=collection_name, points=points)
        except UnexpectedResponse as exc:
            if "Collection not found" in str(exc):
                raise CollectionNotFoundError(message=f"Collection {collection_name} not found!") from exc
            raise
        finally:
            await client.close()

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        await self.create_collection(f"{index_name}_{index_property_name}")

    async def index_data_points(
        self,
        index_name: str,
        index_property_name: str,
        data_points: list[DataPoint],
    ) -> None:
        await self.create_data_points(
            f"{index_name}_{index_property_name}",
            [
                IndexSchema(
                    id=data_point.id,
                    text=str(getattr(data_point, data_point.metadata["index_fields"][0])),
                    document_id=getattr(data_point, "document_id", None),
                    document_name=getattr(data_point, "document_name", None),
                    chunk_index=getattr(data_point, "chunk_index", None),
                    source_chunk_id=getattr(data_point, "source_chunk_id", None),
                    importance_weight=getattr(data_point, "importance_weight", None),
                    belongs_to_set=(data_point.belongs_to_set or []),
                )
                for data_point in data_points
            ],
        )

    async def retrieve(self, collection_name: str, data_point_ids: list[str]):
        client = self.get_qdrant_client()
        try:
            return await client.retrieve(collection_name, data_point_ids, with_payload=True)
        finally:
            await client.close()

    def _tenant_filters(
        self,
        node_name: list[str] | None,
        node_name_filter_operator: str,
    ) -> list[models.Condition]:
        filters: list[models.Condition] = [
            models.FieldCondition(
                key="database_name",
                match=models.MatchValue(value=self.database_name),
            ),
        ]
        if node_name:
            if node_name_filter_operator == "AND":
                filters.extend(
                    models.FieldCondition(
                        key="belongs_to_set",
                        match=models.MatchAny(any=[name]),
                    )
                    for name in node_name
                )
            else:
                filters.append(
                    models.FieldCondition(
                        key="belongs_to_set",
                        match=models.MatchAny(any=node_name),
                    ),
                )
        return filters

    async def search(
        self,
        collection_name: str,
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        limit: int | None = 15,
        with_vector: bool = False,
        include_payload: bool = False,
        node_name: list[str] | None = None,
        node_name_filter_operator: str = "OR",
    ) -> list[ScoredResult]:
        if query_text is None and query_vector is None:
            raise MissingQueryParameterError()
        if not await self.has_collection(collection_name):
            return []

        resolved_vector = query_vector
        if resolved_vector is None:
            resolved_vector = (await self.embed_data([query_text or ""]))[0]

        client = self.get_qdrant_client()
        try:
            resolved_limit = limit
            if resolved_limit is None:
                resolved_limit = (await client.count(collection_name=collection_name)).count
            if resolved_limit == 0:
                return []
            result = await client.query_points(
                collection_name=collection_name,
                query=resolved_vector,
                query_filter=models.Filter(
                    must=self._tenant_filters(node_name, node_name_filter_operator),
                ),
                using="text",
                limit=resolved_limit,
                with_vectors=with_vector,
                with_payload=include_payload,
            )
            return [
                ScoredResult(
                    id=parse_id(str(point.id)),
                    payload=None if not point.payload else {**point.payload, "id": parse_id(str(point.id))},
                    score=point.score if point.score is not None else 0.0,
                )
                for point in result.points
            ]
        finally:
            await client.close()

    async def batch_search(
        self,
        collection_name: str,
        query_texts: list[str],
        limit: int | None = None,
        with_vectors: bool = False,
        include_payload: bool = False,
        node_name: list[str] | None = None,
        node_name_filter_operator: str = "OR",
    ) -> list[list[ScoredResult]]:
        return [
            await self.search(
                collection_name,
                query_text=query_text,
                limit=limit,
                with_vector=with_vectors,
                include_payload=include_payload,
                node_name=node_name,
                node_name_filter_operator=node_name_filter_operator,
            )
            for query_text in query_texts
        ]

    async def delete_data_points(self, collection_name: str, data_point_ids: list[UUID]) -> None:
        client = self.get_qdrant_client()
        try:
            point_ids = [str(point_id) for point_id in data_point_ids]
            points_selector = models.PointIdsList(points=point_ids)
            if self.database_name:
                points_selector = models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.HasIdCondition(has_id=point_ids),
                            models.FieldCondition(
                                key="database_name",
                                match=models.MatchValue(value=self.database_name),
                            ),
                        ],
                    ),
                )
            await client.delete(
                collection_name=collection_name,
                points_selector=points_selector,
            )
        finally:
            await client.close()

    async def upsert_raw_vectors(
        self,
        collection_name: str,
        points: list[dict[str, object]],
        payload_schema: object | None = None,
    ) -> None:
        raise NotImplementedError("Qdrant raw vector upserts are not used by EB's Cognee adapter")

    async def prune(self) -> None:
        client = self.get_qdrant_client()
        try:
            response = await client.get_collections()
            for collection in response.collections:
                await client.delete(
                    collection.name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(must=self._tenant_filters(None, "OR")),
                    ),
                )
                remaining = await client.count(collection_name=collection.name)
                if remaining.count == 0:
                    await client.delete_collection(collection_name=collection.name)
        finally:
            await client.close()

    async def get_collection_names(self) -> list[str]:
        client = self.get_qdrant_client()
        try:
            response = await client.get_collections()
            names: list[str] = []
            for collection in response.collections:
                count = await client.count(
                    collection_name=collection.name,
                    count_filter=models.Filter(must=self._tenant_filters(None, "OR")),
                    exact=True,
                )
                if count.count > 0:
                    names.append(collection.name)
            return names
        finally:
            await client.close()


def register_qdrant_adapter() -> None:
    use_vector_adapter("qdrant", QdrantAdapter)
