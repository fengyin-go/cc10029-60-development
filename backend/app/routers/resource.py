"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    EntryPayload,
    PageResult,
    ReceiptImportPayload,
    ReceiptImportResult,
)
from app.services import service_for
from app.services.textbook_arrival import parse_arrival_csv

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


# ---------------------------------------------------------------------------
# 教材到货：上传到货文件 + 导入回执核对
# 这些固定子路径必须注册在 /{module}/{entry_id} 之前。
# ---------------------------------------------------------------------------
@router.get("/textbook_arrival/receipts", response_model=list[dict])
def list_arrival_receipts() -> list[dict]:
    service = require_service("textbook_arrival")
    return service.list_receipts()


@router.post("/textbook_arrival/import", response_model=ReceiptImportResult)
def import_arrival_receipt(payload: ReceiptImportPayload) -> ReceiptImportResult:
    service = require_service("textbook_arrival")
    receipt = service.import_receipt(
        rows=payload.rows,
        filename=payload.filename,
        receipt_no=payload.receipt_no,
        order_no=payload.order_no,
        course=payload.course,
        textbook=payload.textbook,
        import_batch=payload.import_batch,
    )
    return ReceiptImportResult(ok=True, message=receipt["message"], receipt=receipt)


@router.post("/textbook_arrival/upload", response_model=ReceiptImportResult)
async def upload_arrival_file(
    file: UploadFile = File(...),
    order_no: str = Form(default=""),
    receipt_no: str = Form(default=""),
    course: str = Form(default=""),
    textbook: str = Form(default=""),
    import_batch: str = Form(default=""),
) -> ReceiptImportResult:
    service = require_service("textbook_arrival")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        rows = parse_arrival_csv(content)
    except Exception as error:  # noqa: BLE001 - 回执需要可读的错误信息
        raise HTTPException(status_code=400, detail=f"文件解析失败：{error}") from error
    if not rows:
        raise HTTPException(status_code=400, detail="文件中没有可导入的到货记录")
    receipt = service.import_receipt(
        rows=rows,
        filename=file.filename or "",
        receipt_no=receipt_no,
        order_no=order_no,
        course=course,
        textbook=textbook,
        import_batch=import_batch,
    )
    return ReceiptImportResult(ok=True, message=receipt["message"], receipt=receipt)


@router.get("/textbook_select/{order_no}/arrivals", response_model=dict)
def order_arrivals(order_no: str) -> dict[str, Any]:
    service = require_service("textbook_select")
    return service.order_status(order_no)


@router.get("/syllabus_rev/{entry_id}/adoption", response_model=dict)
def syllabus_adoption(entry_id: int) -> dict[str, Any]:
    service = require_service("syllabus_rev")
    status = service.adoption_status(entry_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"修订稿 {entry_id} 不存在或已归档")
    return status


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    try:
        entry, missing = service.create_entry(payload.values)
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    try:
        if getattr(service, "ACTION_VALUES_SUPPORTED", False):
            entry, message = service.run_action(
                entry_id, payload.action.strip(), payload.values)
        else:
            entry, message = service.run_action(entry_id, payload.action.strip())
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    return ActionResult(ok=entry is not None, message=message, entry=entry)
