from uuid import uuid4

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from platform_core.events import EventEnvelope, Producer


async def test_platform_database_check(platform_database):
    await platform_database.check()


async def test_event_probe_roundtrip(platform_kafka_brokers, event_probe):
    topic = f"platform.test.{uuid4().hex}.v1"

    admin = AIOKafkaAdminClient(bootstrap_servers=platform_kafka_brokers)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
    except TopicAlreadyExistsError:
        pass
    finally:
        await admin.close()

    producer = Producer(platform_kafka_brokers)
    await producer.start()
    await producer.publish(topic, EventEnvelope(type="probe.test", source="smoke", data={"k": 1}))
    await producer.stop()

    events = await event_probe.collect(topic, count=1, timeout=30)
    assert events[0].data == {"k": 1}
