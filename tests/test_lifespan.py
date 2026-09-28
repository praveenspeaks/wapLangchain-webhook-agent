"""Check resource ownership and cleanup without real connections."""

import asyncio
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
        patch("app.lifespan.get_pool"),
        patch("app.lifespan.apply_migrations", new_callable=AsyncMock) as migrate,
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
        # Tables are created at startup; deployments cannot run a migration script.
        assert migrate.await_count == (0 if startup_fails else 1)
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


@pytest.mark.asyncio
async def test_enabled_scheduler_is_cancelled_before_resources_close() -> None:
    from app.lifespan import lifespan

    started, stopped = asyncio.Event(), asyncio.Event()

    async def run_forever() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    pool, sender, repository = AsyncMock(), AsyncMock(), AsyncMock()
    scheduler = MagicMock()
    scheduler.run_forever = run_forever
    saver = MagicMock()
    saver.setup = AsyncMock()
    with (
        patch("app.lifespan.settings") as settings,
        patch("app.lifespan.AsyncConnectionPool", return_value=pool),
        patch("app.lifespan.AsyncPostgresSaver", return_value=saver),
        patch("app.lifespan.build_graph"),
        patch("app.lifespan.init_pool", new_callable=AsyncMock),
        patch("app.lifespan.close_pool", new_callable=AsyncMock),
        patch("app.lifespan.GreetingRepository", return_value=repository),
        patch("app.lifespan.ShivaySender", return_value=sender),
        patch("app.lifespan.GreetingScheduler", return_value=scheduler),
        patch("app.lifespan.get_pool"),
        patch("app.lifespan.apply_migrations", new_callable=AsyncMock),
    ):
        settings.greetings_enabled = True
        settings.whatsapp_enabled = False
        settings.whatsapp_data_entry_enabled = False
        settings.whatsapp_summaries_enabled = False
        async with lifespan(create_app()):
            await asyncio.wait_for(started.wait(), timeout=1)
            sender.aclose.assert_not_awaited()
        assert stopped.is_set()
        repository.check_schema.assert_awaited_once()
        sender.aclose.assert_awaited_once()
        pool.close.assert_awaited_once()
