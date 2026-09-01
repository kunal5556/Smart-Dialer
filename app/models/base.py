from datetime import datetime, timezone
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MongoModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(default_factory=new_id, alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, document: dict[str, Any]) -> Self:
        return cls.model_validate(document)
