"""Check resource ownership and cleanup without real connections."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application import create_app


@pytest.mark.parametrize("startup_fails", [False, True])
def test_resources_close_after_shutdown_or_partial_startup(startup_fails: bool) -> None:
    pool = AsyncMock()
    saver = MagicMock()
    saver.setup = AsyncMock()
    init = AsyncMock(side_effect=RuntimeError("startup failed") if startup_fails else None)
    graph = object()

    with (
        patch("app.lifespan.AsyncConnectionPool", return_value=pool),
        patch("app.lifespan.AsyncPostgresSaver", return_value=saver),
        patch("app.lifespan.build_graph", return_value=graph),
        patch("app.lifespan.init_pool", new=init),
        patch("app.lifespan.close_pool", new_callable=AsyncMock) as close,
    ):
        application = create_app()
        if startup_fails:
            with pytest.raises(RuntimeError, match="startup failed"), TestClient(application):
                pass
        else:
            with TestClient(application) as client:
                assert application.state.runtime.graph is graph
                assert client.get("/health").json() == {"status": "healthy"}
        pool.open.assert_awaited_once_with(wait=True)
        saver.setup.assert_awaited_once()
        close.assert_awaited_once()
        pool.close.assert_awaited_once()
        assert application.state.runtime.graph is None


def test_metrics_belong_to_each_application() -> None:
    first, second = create_app(), create_app()
    with patch("app.api.routes.process_message", new=AsyncMock(return_value="Hello")) as process:
        first.state.runtime.graph = object()
        response = TestClient(first).post("/invoke", json={"sessionId": "abc", "message": "Hi"})
        assert response.json() == {"response": "Hello"}
        process.assert_awaited_once_with(
            graph=first.state.runtime.graph, session_id="abc", text="Hi"
        )
    assert TestClient(first).get("/metrics").json()["messages_processed"] == 1
    assert TestClient(second).get("/metrics").json()["messages_processed"] == 0
