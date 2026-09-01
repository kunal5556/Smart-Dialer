import pytest
from pymongo.errors import DuplicateKeyError

from app.db_indexes import INDEXES_BY_COLLECTION, ensure_indexes
from app.models import Call, ProviderEvent, build_idempotency_key
from app.repositories.base import COLLECTION_CALLS, COLLECTION_PROVIDER_EVENTS
from scripts.seed_data import SeedError, seed_campaign


@pytest.fixture
async def indexed_database(test_database):
    await ensure_indexes(test_database)
    yield test_database
    for collection_name in INDEXES_BY_COLLECTION:
        await test_database[collection_name].delete_many({})


def build_call(**overrides) -> Call:
    fields = {
        "campaign_id": "campaign-1",
        "agent_id": "agent-1",
        "borrower_id": "borrower-1",
        "provider_name": "mock_a",
        "idempotency_key": build_idempotency_key("campaign-1", "agent-1", "borrower-1", 1),
        "created_by_worker": "worker-1",
    }
    fields.update(overrides)
    return Call(**fields)


async def test_ensure_indexes_is_idempotent(indexed_database):
    await ensure_indexes(indexed_database)
    await ensure_indexes(indexed_database)

    for collection_name, indexes in INDEXES_BY_COLLECTION.items():
        existing = await indexed_database[collection_name].index_information()
        for index in indexes:
            assert index.document["name"] in existing


async def test_duplicate_idempotency_key_is_rejected(indexed_database):
    await indexed_database[COLLECTION_CALLS].insert_one(build_call().to_mongo())

    with pytest.raises(DuplicateKeyError):
        await indexed_database[COLLECTION_CALLS].insert_one(build_call().to_mongo())


async def test_calls_for_different_attempts_are_allowed(indexed_database):
    first = build_call()
    second = build_call(
        attempt=2,
        idempotency_key=build_idempotency_key("campaign-1", "agent-1", "borrower-1", 2),
    )

    await indexed_database[COLLECTION_CALLS].insert_one(first.to_mongo())
    await indexed_database[COLLECTION_CALLS].insert_one(second.to_mongo())

    assert await indexed_database[COLLECTION_CALLS].count_documents({}) == 2


async def test_duplicate_provider_call_id_is_rejected(indexed_database):
    first = build_call(provider_call_id="provider-call-1")
    second = build_call(
        idempotency_key=build_idempotency_key("campaign-1", "agent-1", "borrower-2", 1),
        borrower_id="borrower-2",
        provider_call_id="provider-call-1",
    )

    await indexed_database[COLLECTION_CALLS].insert_one(first.to_mongo())

    with pytest.raises(DuplicateKeyError):
        await indexed_database[COLLECTION_CALLS].insert_one(second.to_mongo())


async def test_calls_without_a_provider_call_id_are_not_treated_as_duplicates(indexed_database):
    first = build_call()
    second = build_call(
        borrower_id="borrower-2",
        idempotency_key=build_idempotency_key("campaign-1", "agent-1", "borrower-2", 1),
    )

    await indexed_database[COLLECTION_CALLS].insert_one(first.to_mongo())
    await indexed_database[COLLECTION_CALLS].insert_one(second.to_mongo())

    assert await indexed_database[COLLECTION_CALLS].count_documents({}) == 2


def build_provider_event(**overrides) -> ProviderEvent:
    fields = {
        "provider_name": "mock_a",
        "provider_event_id": "event-1",
        "provider_call_id": "provider-call-1",
        "event_type": "ANSWERED",
    }
    fields.update(overrides)
    return ProviderEvent(**fields)


async def test_duplicate_provider_event_id_is_rejected(indexed_database):
    await indexed_database[COLLECTION_PROVIDER_EVENTS].insert_one(build_provider_event().to_mongo())

    with pytest.raises(DuplicateKeyError):
        await indexed_database[COLLECTION_PROVIDER_EVENTS].insert_one(
            build_provider_event().to_mongo()
        )


async def test_same_event_id_from_a_different_provider_is_allowed(indexed_database):
    await indexed_database[COLLECTION_PROVIDER_EVENTS].insert_one(build_provider_event().to_mongo())
    await indexed_database[COLLECTION_PROVIDER_EVENTS].insert_one(
        build_provider_event(provider_name="mock_b").to_mongo()
    )

    assert await indexed_database[COLLECTION_PROVIDER_EVENTS].count_documents({}) == 2


async def test_seeder_creates_the_requested_counts(indexed_database):
    campaign = await seed_campaign(
        database=indexed_database,
        campaign_name="Seed Test Campaign",
        agent_count=10,
        borrower_count=50,
        reset=False,
    )

    agents = indexed_database["agents"]
    borrowers = indexed_database["borrowers"]

    assert await agents.count_documents({"campaign_id": campaign.id}) == 10
    assert await agents.count_documents({"campaign_id": campaign.id, "state": "OFFLINE"}) == 10
    assert await borrowers.count_documents({"campaign_id": campaign.id}) == 50
    assert await borrowers.count_documents({"campaign_id": campaign.id, "status": "PENDING"}) == 50

    await indexed_database["campaigns"].delete_many({})
    await agents.delete_many({})
    await borrowers.delete_many({})


async def test_seeder_refuses_to_duplicate_an_existing_campaign(indexed_database):
    await seed_campaign(
        database=indexed_database,
        campaign_name="Duplicate Guard Campaign",
        agent_count=2,
        borrower_count=2,
        reset=False,
    )

    with pytest.raises(SeedError):
        await seed_campaign(
            database=indexed_database,
            campaign_name="Duplicate Guard Campaign",
            agent_count=2,
            borrower_count=2,
            reset=False,
        )

    await indexed_database["campaigns"].delete_many({})
    await indexed_database["agents"].delete_many({})
    await indexed_database["borrowers"].delete_many({})


async def test_seeder_reset_replaces_the_existing_campaign(indexed_database):
    first = await seed_campaign(
        database=indexed_database,
        campaign_name="Reset Campaign",
        agent_count=3,
        borrower_count=3,
        reset=False,
    )
    second = await seed_campaign(
        database=indexed_database,
        campaign_name="Reset Campaign",
        agent_count=5,
        borrower_count=7,
        reset=True,
    )

    assert first.id != second.id
    assert await indexed_database["campaigns"].count_documents({"name": "Reset Campaign"}) == 1
    assert await indexed_database["agents"].count_documents({}) == 5
    assert await indexed_database["borrowers"].count_documents({}) == 7

    await indexed_database["campaigns"].delete_many({})
    await indexed_database["agents"].delete_many({})
    await indexed_database["borrowers"].delete_many({})
