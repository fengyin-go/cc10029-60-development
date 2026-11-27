"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class ReceiptImportPayload(BaseModel):
    """到货回单导入请求：既支持一次粘贴多行，也支持引用已上传文件。"""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    filename: str = ""
    order_no: str = ""
    receipt_no: str = ""
    course: str = ""
    textbook: str = ""
    import_batch: str = ""


class ReceiptImportResult(BaseModel):
    ok: bool
    message: str
    receipt: dict[str, Any] = Field(default_factory=dict)
