"""
插件库 HTTP：列表/详情/从商店拷到 data/plugins。不是 VS Code 市场。
"""

import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_token
from app.db.session import get_db
from app.models.plugin import InstalledPluginRow
from app.services.plugins import (
    PLUGIN_ROOT,
    STORE_ROOT,
    catalog,
    catalog_by_id,
    install_from_store,
    is_installed_on_disk,
    load_store,
    package_readme,
)

router = APIRouter(prefix="/plugins", tags=["plugins"], dependencies=[Depends(require_token)])


def _public(item: dict, installed: dict[str, bool]) -> dict:
    builtin = bool(item.get("builtin"))
    pid = item["id"]
    on_disk = builtin or is_installed_on_disk(pid)
    enabled = builtin or (installed.get(pid, False) and on_disk)
    return {
        "id": pid,
        "name": item.get("name"),
        "version": item.get("version", "1.0.0"),
        "publisher": item.get("publisher", "Forge"),
        "summary": item.get("summary", ""),
        "exts": item.get("exts", []),
        "kind": item.get("kind", "editor"),
        "builtin": builtin,
        "recommended": bool(item.get("recommended")),
        "license": item.get("license", ""),
        "installed": enabled,
        "enabled": enabled,
    }


@router.get("")
async def list_plugins(q: str = Query(""), db: AsyncSession = Depends(get_db)) -> dict:
    needle = q.strip().lower()
    installed_rows = (await db.execute(select(InstalledPluginRow))).scalars()
    installed = {r.plugin_id: r.enabled for r in installed_rows}
    items = []
    for item in catalog():
        hay = f"{item.get('name', '')} {item.get('summary', '')} {' '.join(item.get('exts', []))}".lower()
        if needle and needle not in hay:
            continue
        items.append(_public(item, installed))
    return {
        "plugins": items,
        "library": str(STORE_ROOT),
        "install_root": str(PLUGIN_ROOT),
    }


@router.get("/{plugin_id}")
async def plugin_detail(plugin_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    meta = catalog_by_id().get(plugin_id)
    if not meta:
        raise HTTPException(404, "未知插件")
    installed_rows = (await db.execute(select(InstalledPluginRow))).scalars()
    installed = {r.plugin_id: r.enabled for r in installed_rows}
    body = _public(meta, installed)
    body["readme"] = package_readme(plugin_id)
    return body


@router.post("/install-all")
async def install_all_store(db: AsyncSession = Depends(get_db)) -> dict:
    installed: list[str] = []
    for item in load_store():
        pid = item["id"]
        try:
            install_from_store(pid)
        except FileNotFoundError:
            continue
        row = await db.get(InstalledPluginRow, pid)
        if row is None:
            db.add(InstalledPluginRow(plugin_id=pid, enabled=True))
        else:
            row.enabled = True
        installed.append(pid)
    await db.commit()
    return {"ok": True, "installed": installed}


@router.post("/{plugin_id}/install")
async def install_plugin(plugin_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    meta = catalog_by_id().get(plugin_id)
    if not meta:
        raise HTTPException(404, "未知插件")
    if meta.get("builtin"):
        return {"ok": True, "plugin_id": plugin_id, "builtin": True, "path": None}
    try:
        target = install_from_store(plugin_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "插件库中没有该包") from exc
    row = await db.get(InstalledPluginRow, plugin_id)
    if row is None:
        db.add(InstalledPluginRow(plugin_id=plugin_id, enabled=True))
    else:
        row.enabled = True
    await db.commit()
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "path": str(target),
        "files": sorted(p.name for p in target.iterdir()),
    }


@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    meta = catalog_by_id().get(plugin_id)
    if not meta:
        raise HTTPException(404, "未知插件")
    if meta.get("builtin"):
        raise HTTPException(400, "内置插件不能卸载")
    row = await db.get(InstalledPluginRow, plugin_id)
    if row:
        await db.delete(row)
        await db.commit()
    target = PLUGIN_ROOT / plugin_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}
