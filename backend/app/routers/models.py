"""
模型菜单 API：列表、设默认、加/删自定义模型。写入 preferred_model.txt。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_engineer, require_token
from app.db.session import get_db
from app.models.llm import CustomModelRow
from app.schemas import CustomModelIn, DefaultModelIn
from app.services.llm_catalog import catalog_with_status, provider_of
from app.services.llm_prefs import preferred_model, save_preferred_model

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_token)])


@router.get("")
async def list_models(db: AsyncSession = Depends(get_db)) -> dict:
    """【功能】内置厂商 + 自定义 + 当前 default。"""
    rows = (await db.execute(select(CustomModelRow).order_by(CustomModelRow.created_at.asc()))).scalars()
    custom = [
        {"id": r.id, "provider": r.provider, "label": r.label, "model_id": r.model_id}
        for r in rows
    ]
    grouped = {p["id"]: {**p, "custom": []} for p in catalog_with_status()}
    extras = []
    for item in custom:
        bucket = grouped.get(item["provider"])
        if bucket:
            bucket["variants"] = [
                *bucket["variants"],
                {"id": item["model_id"], "label": item["label"], "custom_id": item["id"]},
            ]
        else:
            extras.append(item)
    return {"providers": list(grouped.values()), "custom": extras, "default": preferred_model()}


@router.put("/default")
async def set_default_model(
    body: DefaultModelIn,     _user=Depends(require_engineer)
) -> dict:
    """【功能】PUT 默认模型，写入 data/preferred_model.txt。"""
    mid = save_preferred_model(body.model)
    if not mid:
        raise HTTPException(400, "模型 id 不能为空")
    return {"ok": True, "model": mid}


@router.post("")
async def add_model(body: CustomModelIn, db: AsyncSession = Depends(get_db)) -> dict:
    provider = body.provider.strip() or provider_of(body.model_id)
    row = CustomModelRow(
        provider=provider,
        label=body.label.strip() or body.model_id,
        model_id=body.model_id.strip(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "provider": row.provider, "label": row.label, "model_id": row.model_id}


@router.delete("/{model_row_id}")
async def delete_model(model_row_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(CustomModelRow, model_row_id)
    if not row:
        raise HTTPException(404, "Model not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
