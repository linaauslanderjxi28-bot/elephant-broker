def test_store_continues_when_cognee_text_capture_treats_html_as_path(monkeypatch, mock_add_data_points, mock_cognee):
    """A raw HTML fact must persist even if Cognee's optional text capture sees it as a path."""
    from elephantbroker.runtime.memory.facade import MemoryStoreFacade
    from elephantbroker.runtime.trace.ledger import TraceLedger
    from tests.fixtures.factories import make_fact_assertion
    from unittest.mock import AsyncMock

    graph = AsyncMock()
    graph.get_entity = AsyncMock(return_value=None)
    vector = AsyncMock()
    embeddings = AsyncMock()
    embeddings.embed_text = AsyncMock(return_value=[0.1] * 1024)
    facade = MemoryStoreFacade(graph, vector, embeddings, TraceLedger(), dataset_name="test_ds")
    mock_cognee.add = AsyncMock(side_effect=FileNotFoundError("Storage directory does not exist: '/td><td>raw html'"))
    monkeypatch.setattr("elephantbroker.runtime.memory.facade.add_data_points", mock_add_data_points)
    monkeypatch.setattr("elephantbroker.runtime.memory.facade.cognee", mock_cognee)

    fact = make_fact_assertion(text="<table><tr><td>raw html</td></tr></table>")

    result = __import__("asyncio").run(facade.store(fact))

    assert result.id == fact.id
    assert len(mock_add_data_points.calls) == 1
