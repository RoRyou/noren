"""店主の画面の API（メニュー取り込み・業態の切り替え）。"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.context import Ctx, http_ctx
from app.services.owner import PRESETS, OwnerService

router = APIRouter(prefix="/api/owner")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ImportIn(BaseModel):
    image_base64: str
    mime: str = "image/jpeg"


class IngredientsIn(BaseModel):
    ingredients: list[str]
    cross_contact_items: list[str] = []


class PublishIn(BaseModel):
    name_ja: str
    price: int
    published: bool = True


class StoreIn(BaseModel):
    business_type: str


class PolicyIn(BaseModel):
    key: str
    value: str


def owner(ctx: Ctx = Depends(http_ctx)) -> OwnerService:
    return OwnerService(ctx.repo)


@router.post("/menu/import")
async def import_menu(body: ImportIn, svc: OwnerService = Depends(owner)) -> dict:
    image = base64.b64decode(body.image_base64)
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image_too_large")
    return await svc.import_photo(image, body.mime)


@router.get("/menu")
async def all_items(ctx: Ctx = Depends(http_ctx)) -> list[dict]:
    items = await ctx.repo.menu()
    return [i.model_dump(by_alias=True, mode="json") for i in items.values()]


@router.post("/menu/{item_id}/questions")
async def questions(item_id: str, svc: OwnerService = Depends(owner)) -> dict:
    return await svc.questions(item_id)


@router.post("/menu/{item_id}/ingredients")
async def ingredients(item_id: str, body: IngredientsIn, svc: OwnerService = Depends(owner)) -> dict:
    return await svc.answer_ingredients(item_id, body.ingredients, body.cross_contact_items)


@router.post("/menu/{item_id}/translate")
async def translate(item_id: str, svc: OwnerService = Depends(owner)) -> dict:
    return await svc.translate(item_id)


@router.post("/menu/{item_id}/publish")
async def publish(item_id: str, body: PublishIn, svc: OwnerService = Depends(owner)) -> dict:
    return await svc.publish(item_id, body.name_ja, body.price, body.published)


@router.get("/policy")
async def get_policy(ctx: Ctx = Depends(http_ctx)) -> dict:
    return (await ctx.repo.store()).service_policy


@router.patch("/policy")
async def set_policy(body: PolicyIn, svc: OwnerService = Depends(owner)) -> dict:
    try:
        return await svc.set_policy(body.key, body.value)
    except ValueError as e:
        raise HTTPException(400, "unknown_policy") from e


@router.patch("/store")
async def store(body: StoreIn, svc: OwnerService = Depends(owner)) -> dict:
    if body.business_type not in PRESETS:
        raise HTTPException(400, "unknown_business_type")
    return await svc.set_business_type(body.business_type)
