"""FastAPI application composition and dependency lifecycle."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.routes.checkpoints import router as checkpoint_router
from app.api.routes.health import router as health_router
from app.api.routes.messages import router as message_router
from app.api.routes.tasks import router as task_router
from app.api.routes.tool_results import router as tool_result_router
from app.conversations.repository import ChatRequestStore
from app.conversations.stores.memory import InMemoryChatRequestStore
from app.conversations.stores.postgres import PostgreSQLChatRequestStore
from app.core.config import Settings, load_settings
from app.providers.base import ModelProvider
from app.providers.qwen import QwenProvider
from app.tasks.checkpoints.repository import TaskCheckpointStore
from app.tasks.checkpoints.stores.memory import InMemoryTaskCheckpointStore
from app.tasks.checkpoints.stores.postgres import PostgreSQLTaskCheckpointStore
from app.tasks.store import ServerTaskStore
from app.tasks.stores.memory import InMemoryServerTaskStore
from app.tasks.stores.postgres import PostgreSQLServerTaskStore
from app.tool_results.repository import ToolResultStore
from app.tool_results.stores.postgres import PostgreSQLToolResultStore


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    result_store: ToolResultStore | None = None,
    chat_request_store: ChatRequestStore | None = None,
    task_checkpoint_store: TaskCheckpointStore | None = None,
    server_task_store: ServerTaskStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_settings = settings or load_settings()
        active_provider = provider or QwenProvider(active_settings)
        active_result_store = result_store or PostgreSQLToolResultStore(
            active_settings.database_url
        )
        use_in_memory_defaults = result_store is not None
        active_chat_store = chat_request_store or (
            InMemoryChatRequestStore()
            if use_in_memory_defaults
            else PostgreSQLChatRequestStore(active_settings.database_url)
        )
        active_checkpoint_store = task_checkpoint_store or (
            InMemoryTaskCheckpointStore()
            if use_in_memory_defaults
            else PostgreSQLTaskCheckpointStore(active_settings.database_url)
        )
        active_task_store = server_task_store or (
            InMemoryServerTaskStore()
            if use_in_memory_defaults
            else PostgreSQLServerTaskStore(active_settings.database_url)
        )
        stores = (
            active_result_store,
            active_chat_store,
            active_checkpoint_store,
            active_task_store,
        )
        for store in stores:
            await store.initialize()

        app.state.settings = active_settings
        app.state.provider = active_provider
        app.state.result_store = active_result_store
        app.state.chat_request_store = active_chat_store
        app.state.task_checkpoint_store = active_checkpoint_store
        app.state.server_task_store = active_task_store
        try:
            yield
        finally:
            await active_provider.close()
            for store in reversed(stores):
                await store.close()

    application = FastAPI(
        title="WellPhone AI Backend",
        version="0.10.0",
        lifespan=lifespan,
    )
    for router in (
        health_router,
        checkpoint_router,
        task_router,
        message_router,
        tool_result_router,
    ):
        application.include_router(router)
    return application


app = create_app()
