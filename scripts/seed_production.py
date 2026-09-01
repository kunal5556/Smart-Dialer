import argparse
import asyncio
import sys

from app.db import connect, disconnect, get_db
from app.db_indexes import ensure_indexes
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign, PacingConfig
from app.models.enums import CampaignStatus, DialingMode
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerRepository
from app.repositories.campaign_repo import CampaignRepository


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a demo campaign into the deployed SmartDialer database."
    )
    parser.add_argument("--campaign-name", default="Demo Collections Campaign")
    parser.add_argument("--agents", type=int, default=10)
    parser.add_argument("--borrowers", type=int, default=300)
    parser.add_argument("--answer-rate", type=float, default=0.3)
    parser.add_argument("--start", action="store_true", help="Set the campaign to RUNNING")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in DialingMode],
        default=DialingMode.PROGRESSIVE.value,
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> str:
    await connect()
    try:
        database = get_db()
        await ensure_indexes(database)

        campaigns = CampaignRepository(database)
        existing = [
            campaign
            for campaign in await campaigns.find_all()
            if campaign.name == args.campaign_name
        ]
        if existing:
            print(
                f"Campaign '{args.campaign_name}' already exists ({existing[0].id}); "
                "nothing was changed."
            )
            return existing[0].id

        campaign = Campaign(
            name=args.campaign_name,
            status=CampaignStatus.RUNNING if args.start else CampaignStatus.DRAFT,
            dialing_mode=DialingMode(args.mode),
            max_concurrent_calls=max(args.agents * 3, 30),
            pacing_config=PacingConfig(baseline_answer_rate=args.answer_rate),
        )
        await campaigns.insert(campaign)

        await AgentRepository(database).insert_many(
            [
                Agent(campaign_id=campaign.id, name=f"Agent {number:04d}")
                for number in range(1, args.agents + 1)
            ]
        )
        await BorrowerRepository(database).insert_many(
            [
                Borrower(
                    campaign_id=campaign.id,
                    name=f"Borrower {number:05d}",
                    phone_number=f"+1555{number:07d}",
                )
                for number in range(1, args.borrowers + 1)
            ]
        )

        print(
            f"Seeded '{campaign.name}' ({campaign.id}) with {args.agents} agents "
            f"and {args.borrowers} borrowers, status {campaign.status.value}."
        )
        print("Agents start OFFLINE; log them in from the dashboard or the agents API.")
        return campaign.id
    finally:
        await disconnect()


def main() -> int:
    asyncio.run(run(parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
