import asyncio
from uuid import uuid4

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from platform_core.events import Consumer, EventEnvelope, Producer


async def _ensure_topics(brokers: str, names: list[str]) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=brokers)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(n, num_partitions=1, replication_factor=1) for n in names]
        )
    except TopicAlreadyExistsError:
        pass
    finally:
        await admin.close()


async def test_publish_consume_roundtrip(kafka_brokers):
    topic = f"platform.test.{uuid4().hex}.v1"
    await _ensure_topics(kafka_brokers, [topic])

    received: list[EventEnvelope] = []
    done = asyncio.Event()

    async def handler(event: EventEnvelope) -> None:
        received.append(event)
        done.set()

    producer = Producer(kafka_brokers)
    await producer.start()
    sent = EventEnvelope(type="test.happened", source="test-suite", data={"n": 1})
    await producer.publish(topic, sent)

    consumer = Consumer(kafka_brokers, f"g-{uuid4().hex}", [topic], handler)
    await consumer.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=30)
    finally:
        await consumer.stop()
        await producer.stop()

    assert len(received) == 1
    assert received[0].event_id == sent.event_id
    assert received[0].type == "test.happened"
    assert received[0].data == {"n": 1}


async def test_failing_handler_dead_letters(kafka_brokers):
    topic = f"platform.test.{uuid4().hex}.v1"
    dlq = topic + ".dlq"
    await _ensure_topics(kafka_brokers, [topic, dlq])

    async def always_fails(_event: EventEnvelope) -> None:
        raise RuntimeError("boom")

    dlq_received: list[EventEnvelope] = []
    dlq_done = asyncio.Event()

    async def collect(event: EventEnvelope) -> None:
        dlq_received.append(event)
        dlq_done.set()

    producer = Producer(kafka_brokers)
    await producer.start()
    sent = EventEnvelope(type="will.fail", source="test-suite", data={"x": True})
    await producer.publish(topic, sent)

    worker = Consumer(kafka_brokers, f"g-{uuid4().hex}", [topic], always_fails, retries=1)
    dlq_watch = Consumer(kafka_brokers, f"g-{uuid4().hex}", [dlq], collect)
    await worker.start()
    await dlq_watch.start()
    try:
        await asyncio.wait_for(dlq_done.wait(), timeout=30)
    finally:
        await worker.stop()
        await dlq_watch.stop()
        await producer.stop()

    assert dlq_received[0].event_id == sent.event_id
