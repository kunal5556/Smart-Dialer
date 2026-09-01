import argparse
import asyncio
import sys

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import connect, disconnect, get_db
from app.db_indexes import ensure_indexes
from app.models import Agent, Borrower, Campaign
from app.repositories.base import (
    COLLECTION_AGENTS,
    COLLECTION_BORROWERS,
    COLLECTION_CAMPAIGNS,
)


class SeedError(RuntimeError):
    pass


def build_phone_number(index: int) -> str:
    return f"+1555{index:07d}"


async def seed_campaign(
    database: AsyncIOMotorDatabase,
    campaign_name: str,
    agent_count: int,
    borrower_count: int,
    reset: bool,
) -> Campaign:
    if agent_count < 0 or borrower_count < 0:
        raise SeedError("Agent and borrower counts must not be negative.")

    existing = await database[COLLECTION_CAMPAIGNS].find_one({"name": campaign_name})
    if existing is not None:
        if not reset:
            raise SeedError(
                f"Campaign '{campaign_name}' already exists. "
                "Re-run with --reset to replace it, or choose a different --campaign-name."
            )
        campaign_id = existing["_id"]
        await database[COLLECTION_AGENTS].delete_many({"campaign_id": campaign_id})
        await database[COLLECTION_BORROWERS].delete_many({"campaign_id": campaign_id})
        await database[COLLECTION_CAMPAIGNS].delete_one({"_id": campaign_id})

    campaign = Campaign(name=campaign_name)
    await database[COLLECTION_CAMPAIGNS].insert_one(campaign.to_mongo())

    if agent_count:
        agents = [
            Agent(campaign_id=campaign.id, name=f"Agent {number:03d}")
            for number in range(1, agent_count + 1)
        ]
        await database[COLLECTION_AGENTS].insert_many(agent.to_mongo() for agent in agents)

    if borrower_count:
        borrowers = [
            Borrower(
                campaign_id=campaign.id,
                name=f"Borrower {number:04d}",
                phone_number=build_phone_number(number),
            )
            for number in range(1, borrower_count + 1)
        ]
        await database[COLLECTION_BORROWERS].insert_many(
            borrower.to_mongo() for borrower in borrowers
        )

    return campaign


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a SmartDialer campaign with test data.")
    parser.add_argument("--agents", type=int, default=10)
    parser.add_argument("--borrowers", type=int, default=100)
    parser.add_argument("--campaign-name", type=str, default="Demo Collections Campaign")
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    await connect()
    try:
        database = get_db()
        await ensure_indexes(database)
        campaign = await seed_campaign(
            database=database,
            campaign_name=args.campaign_name,
            agent_count=args.agents,
            borrower_count=args.borrowers,
            reset=args.reset,
        )
        print(
            f"Seeded campaign '{campaign.name}' ({campaign.id}) "
            f"with {args.agents} agents and {args.borrowers} borrowers."
        )
    finally:
        await disconnect()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except SeedError as error:
        print(f"Seeding aborted: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
