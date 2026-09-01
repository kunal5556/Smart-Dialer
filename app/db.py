from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.config import get_settings

_client: AsyncIOMotorClient | None = None
_database: AsyncIOMotorDatabase | None = None


class DatabaseNotConnectedError(RuntimeError):
    pass


async def connect() -> None:
    global _client, _database
    if _client is not None:
        return
    settings = get_settings()
    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        uuidRepresentation="standard",
        tz_aware=True,
    )
    _database = _client[settings.MONGODB_DB_NAME]


async def disconnect() -> None:
    global _client, _database
    if _client is None:
        return
    _client.close()
    _client = None
    _database = None


def get_db() -> AsyncIOMotorDatabase:
    if _database is None:
        raise DatabaseNotConnectedError("Database is not connected. Call connect() first.")
    return _database


async def ping() -> bool:
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
    except PyMongoError:
        return False
    return True
