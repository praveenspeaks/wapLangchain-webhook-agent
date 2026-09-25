"""Protected administration for occasion records and delivery history."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from psycopg.errors import UniqueViolation
from pydantic import BaseModel

from app.config import settings
from app.greetings.repository import GreetingRepository
from app.greetings.schemas import OccasionInput, RecipientTimezone

admin_key = APIKeyHeader(name="X-Greetings-Key", auto_error=False)


def require_admin(key: Annotated[str | None, Security(admin_key)]) -> None:
    expected = settings.greetings_admin_api_key.get_secret_value()
    if not expected:
        raise HTTPException(503, "Set GREETINGS_ADMIN_API_KEY to enable occasion management")
    if key is None or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid greetings API key")


router = APIRouter(prefix="/greetings", tags=["Greetings"], dependencies=[Depends(require_admin)])
repository = GreetingRepository()


@router.post("/occasions", status_code=201)
async def create_occasion(payload: OccasionInput) -> dict[str, Any]:
    try:
        return await repository.create(payload)
    except UniqueViolation as exc:
        raise HTTPException(
            409, "This phone number already has this occasion on this date"
        ) from exc


@router.get("/occasions")
async def list_occasions(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await repository.list_occasions(limit, offset)


class EnabledUpdate(BaseModel):
    enabled: bool


@router.patch("/occasions/{occasion_id}/enabled")
async def set_enabled(occasion_id: int, payload: EnabledUpdate) -> dict[str, bool]:
    if not await repository.set_enabled(occasion_id, payload.enabled):
        raise HTTPException(404, "Occasion not found")
    return {"enabled": payload.enabled}


@router.get("/deliveries")
async def list_deliveries(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await repository.list_deliveries(limit, offset)


@router.patch("/occasions/{occasion_id}/timezone")
async def set_timezone(occasion_id: int, payload: RecipientTimezone) -> dict[str, str]:
    if not await repository.set_timezone(occasion_id, payload.timezone):
        raise HTTPException(404, "Occasion not found")
    return {"timezone": payload.timezone}
