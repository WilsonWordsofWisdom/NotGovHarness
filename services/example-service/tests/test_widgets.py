import pytest
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from asgi_lifespan import LifespanManager
from example_service.config import WIDGET_TOPIC
from example_service.main import app, build_app
from example_service.models import Widget
from httpx import ASGITransport, AsyncClient

from platform_core.db import Base, Database


@pytest.fixture
async def _schema(platform_pg_url: str):
    """Ensure the widgets table exists (Alembic is verified separately by `task migrate`)."""
    db = Database(platform_pg_url)
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await db.dispose()


async def _ensure_topic(brokers: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=brokers)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(WIDGET_TOPIC, num_partitions=1, replication_factor=1)])
    except TopicAlreadyExistsError:
        pass
    finally:
        await admin.close()


async def test_create_widget_persists_and_emits(_schema, platform_kafka_brokers, event_probe):
    await _ensure_topic(platform_kafka_brokers)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://example") as client:
            created = await client.post("/widgets", json={"name": "gizmo"})
            assert created.status_code == 201
            widget_id = created.json()["id"]

            fetched = await client.get(f"/widgets/{widget_id}")
            assert fetched.status_code == 200
            assert fetched.json()["name"] == "gizmo"

            missing = await client.get("/widgets/999999999")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "not_found"

    events = await event_probe.collect(WIDGET_TOPIC, count=10_000, timeout=10)
    assert any(e.type == "widget.created" and e.data.get("id") == widget_id for e in events)


async def test_build_app_is_isolated():
    # A second app instance can be built without touching global state.
    other = build_app()
    assert any(r.path == "/widgets" for r in other.routes)  # type: ignore[attr-defined]


def test_widget_model_table():
    assert Widget.__tablename__ == "widgets"
