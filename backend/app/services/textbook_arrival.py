"""教材到货领域服务：到货文件导入、回执核对、缺货更正与冲销。

导入以「订单编号 + 回单编号 + 到货批次 + 教材名称」作为幂等键：同一回单
重复导入只保留一份，不会把十册到货重复计成二十册。到货记录同时按适用
课程过滤，其他课程的同名教材不会被计入当前订单。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from app.services import textbook_rules as rules
from app.services.base import BaseModuleService
from app.store import store

# 受理上传文件时允许的字段别名，兼容手工填表和导出的 CSV 表头
_FIELD_ALIASES = {
    "订单号": "订单编号",
    "订单": "订单编号",
    "采购单号": "订单编号",
    "回执编号": "回单编号",
    "导入回单": "回单编号",
    "回单号": "回单编号",
    "批次": "到货批次",
    "批次号": "到货批次",
    "书名": "教材名称",
    "教材": "教材名称",
    "课程名称": "适用课程",
    "课程": "适用课程",
    "数量": "到货数量",
    "到货册数": "到货数量",
    "册数": "到货数量",
    "确认数量": "到货数量",
    "结论": "核对结论",
    "导入批次": "导入批次号",
}


# 动作别名统一映射到规范动作，页面/接口用哪种叫法都能被受理
_ACTION_VERBS = {
    "核对导入回执": "核对回执",
    "确认到货": "核对回执",
    "标记缺货": "更正缺货",
}


def normalize_values(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in (values or {}).items():
        normalized[_FIELD_ALIASES.get(str(key), str(key))] = value
    return normalized


class TextbookArrivalService(BaseModuleService):
    MODULE = "textbook_arrival"
    ENTITY = "到货记录"
    OPERATION_TERM = "核对回执"
    LIST_FIELDS = [
        "订单编号", "回单编号", "到货批次", "教材名称", "适用课程",
        "到货数量", "文件名", "导入批次号", "导入时间", "核对结论",
        "缺货证明", "缺货证明说明", "备注说明",
    ]
    REQUIRED_FIELDS = ["订单编号", "教材名称", "到货数量"]
    STATUS_ORDER = ["待核对", "已核对", "缺货", "已冲销"]
    ACTION_RULES = {
        "核对回执": "已核对",
        "核对导入回执": "已核对",
        "确认到货": "已核对",
        "更正缺货": "缺货",
        "标记缺货": "缺货",
        "冲销记录": "已冲销",
    }
    NEGATIVE_ACTIONS = ["更正缺货", "标记缺货"]
    ACTION_VALUES_SUPPORTED = True

    # ------------------------------------------------------------------
    # 登记 / 导入
    # ------------------------------------------------------------------
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        values = normalize_values(values)
        order_no = rules.text(values.get("订单编号"))
        receipt_no = rules.text(values.get("回单编号"))
        textbook = rules.text(values.get("教材名称"))
        course = rules.text(values.get("适用课程"))
        batch = rules.text(values.get("到货批次"))
        quantity = rules.to_int(values.get("到货数量"))

        if not order_no or not textbook or str(values.get("到货数量") or "").strip() == "":
            missing = [
                field for field in self.REQUIRED_FIELDS
                if not str(values.get(field) or "").strip()
            ]
            return None, missing or ["到货数量"]

        order = rules.find_order(order_no)
        if order is not None:
            order_course = rules.text(order.get("适用课程"))
            order_textbook = rules.text(order.get("教材名称"))
            # 其他课程的同名书不允许挂到本订单
            if course and order_course and course != order_course:
                raise ValueError(
                    f"到货课程不匹配：订单 {order_no} 属于课程「{order_course}」，"
                    f"不能登记课程「{course}」的到货"
                )
            if textbook and order_textbook and textbook != order_textbook:
                raise ValueError(
                    f"到货教材不匹配：订单 {order_no} 订购的是《{order_textbook}》，"
                    f"不能登记《{textbook}》"
                )
            if not course:
                course = order_course
            if not textbook:
                textbook = order_textbook

        duplicate = self._find_duplicate(order_no, receipt_no, batch, textbook)
        if duplicate is not None:
            duplicate["重复导入"] = True
            return duplicate, []

        values.update({
            "订单编号": order_no,
            "回单编号": receipt_no,
            "教材名称": textbook,
            "适用课程": course,
            "到货批次": batch,
            "到货数量": quantity,
        })
        entry, missing = super().create_entry(values)
        if entry is not None:
            entry["重复导入"] = False
            entry["核对结论"] = rules.text(values.get("核对结论")) or "待核对"
        return entry, missing

    def _find_duplicate(
        self, order_no: str, receipt_no: str, batch: str, textbook: str
    ) -> dict[str, Any] | None:
        """同一回单的同一批次/教材视为重复导入，直接返回既有记录。

        一张回单可能包含多个批次或多本教材，所以幂等键是
        「订单 + 回单 + 批次 + 教材」，而不是只看回单号。
        """
        for row in store.rows(self.MODULE):
            if rules.text(row.get("订单编号")) != order_no:
                continue
            if rules.text(row.get("回单编号")) != receipt_no:
                continue
            if rules.text(row.get("到货批次")) != batch:
                continue
            if rules.text(row.get("教材名称")) != textbook:
                continue
            return row
        return None

    def import_receipt(
        self,
        *,
        rows: list[dict[str, Any]],
        filename: str = "",
        receipt_no: str = "",
        order_no: str = "",
        course: str = "",
        textbook: str = "",
        import_batch: str = "",
    ) -> dict[str, Any]:
        """导入一张到货回单，返回导入回执。

        回执是给教材员核对用的：记录本次新增多少行、跳过了多少重复行、
        当前订单累计已核对多少册。
        """
        receipt_no = rules.text(receipt_no)
        order_no = rules.text(order_no)
        course = rules.text(course)
        textbook = rules.text(textbook)
        import_batch = rules.text(import_batch) or receipt_no or filename

        payload_rows = [normalize_values(row) for row in rows if isinstance(row, dict)]

        # 上传接口把订单/回单号放在表单字段里，行内缺失时从表单补齐，
        # 否则回单级幂等会被绕过。
        for row in payload_rows:
            if order_no and not rules.text(row.get("订单编号")):
                row["订单编号"] = order_no
            if receipt_no and not rules.text(row.get("回单编号")):
                row["回单编号"] = receipt_no
            if course and not rules.text(row.get("适用课程")):
                row["适用课程"] = course
            if textbook and not rules.text(row.get("教材名称")):
                row["教材名称"] = textbook
        if not order_no and payload_rows:
            order_no = rules.text(payload_rows[0].get("订单编号"))
        if not receipt_no and payload_rows:
            receipt_no = rules.text(payload_rows[0].get("回单编号"))

        # 回单级幂等：同一订单 + 同一回单 + 同一批次/教材再次导入直接回执“重复导入”。
        # 只有整张回单的每一行都已存在才整体短路；回单后续补行时仍逐行导入。
        if receipt_no and order_no and payload_rows:
            existing = {
                (rules.text(row.get("到货批次")), rules.text(row.get("教材名称")))
                for row in store.rows(self.MODULE)
                if rules.text(row.get("订单编号")) == order_no
                and rules.text(row.get("回单编号")) == receipt_no
            }
            incoming = {
                (rules.text(row.get("到货批次")), rules.text(row.get("教材名称")))
                for row in payload_rows
            }
            if existing and incoming <= existing:
                return self._receipt(
                    order_no, receipt_no, filename,
                    imported=0,
                    duplicates=len(incoming),
                    skipped=[],
                    duplicate_receipt=True,
                    message=f"回单 {receipt_no} 已导入过，本次未重复计数",
                )

        imported = 0
        duplicates = 0
        skipped: list[dict[str, Any]] = []
        for index, raw in enumerate(payload_rows, start=1):
            row = dict(raw)
            row.setdefault("订单编号", order_no)
            row.setdefault("回单编号", receipt_no)
            row.setdefault("适用课程", course)
            row.setdefault("教材名称", textbook)
            row.setdefault("文件名", filename)
            row.setdefault("导入批次号", import_batch)
            row.setdefault("核对结论", "待核对")
            if not rules.text(row.get("导入时间")):
                row["导入时间"] = rules.today()
            try:
                entry, missing = self.create_entry(row)
            except ValueError as error:
                skipped.append({"行号": index, "原因": str(error)})
                continue
            if entry is None:
                skipped.append({
                    "行号": index,
                    "原因": "缺少必填字段：" + "、".join(missing),
                })
            elif entry.get("重复导入"):
                duplicates += 1
            else:
                imported += 1

        self._refresh_order(order_no)
        order = rules.find_order(order_no)
        if order is not None:
            rules.sync_syllabus_todos(
                rules.text(order.get("适用课程")),
                "到货回单已导入",
                order_no=order_no,
                receipt_no=rules.latest_receipt(
                    order_no,
                    rules.text(order.get("适用课程")),
                    rules.text(order.get("教材名称")),
                ),
                textbook=rules.text(order.get("教材名称")),
            )

        return self._receipt(
            order_no, receipt_no, filename,
            imported=imported,
            duplicates=duplicates,
            skipped=skipped,
            duplicate_receipt=False,
            message=(
                f"回单 {receipt_no or '（无编号）'} 导入完成：新增 {imported} 行，"
                f"重复/跳过 {duplicates + len(skipped)} 行"
            ),
        )

    def _receipt(
        self,
        order_no: str,
        receipt_no: str,
        filename: str,
        *,
        imported: int,
        duplicates: int,
        skipped: list[dict[str, Any]],
        duplicate_receipt: bool,
        message: str,
    ) -> dict[str, Any]:
        rows = [
            row for row in store.rows(self.MODULE)
            if rules.text(row.get("订单编号")) == order_no
            and (not receipt_no or rules.text(row.get("回单编号")) == receipt_no)
        ]
        confirmed = sum(
            rules.to_int(row.get("到货数量"))
            for row in rows
            if rules.text(row.get("status")) in rules.VERIFIED_STATUSES
        )
        return {
            "ok": True,
            "message": message,
            "订单编号": order_no,
            "回单编号": receipt_no,
            "文件名": filename,
            "新增行数": imported,
            "重复行数": duplicates,
            "跳过行数": len(skipped),
            "跳过明细": skipped,
            "重复导入": duplicate_receipt,
            "回单状态": self.receipt_status(order_no, receipt_no),
            "已核对数量": confirmed,
            "待核对数量": sum(
                rules.to_int(row.get("到货数量"))
                for row in rows
                if rules.text(row.get("status")) == "待核对"
            ),
        }

    def receipt_status(self, order_no: str, receipt_no: str = "") -> str:
        rows = rules.receipt_rows(order_no, receipt_no) if receipt_no else [
            row for row in store.rows(self.MODULE)
            if rules.text(row.get("订单编号")) == rules.text(order_no)
        ]
        if not rows:
            return "未导入"
        statuses = {rules.text(row.get("status")) for row in rows}
        if statuses == {"已冲销"}:
            return "已冲销"
        if rules.SHORTAGE_STATUS in statuses:
            return "缺货"
        if statuses <= set(rules.VERIFIED_STATUSES):
            return "已核对"
        return "待核对"

    def list_receipts(self) -> list[dict[str, Any]]:
        """按订单 + 回单聚合，供页面查看导入回执核对情况。"""
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in store.rows(self.MODULE):
            key = (rules.text(row.get("订单编号")), rules.text(row.get("回单编号")))
            item = groups.setdefault(key, {
                "订单编号": key[0],
                "回单编号": key[1],
                "文件名": rules.text(row.get("文件名")),
                "教材名称": rules.text(row.get("教材名称")),
                "适用课程": rules.text(row.get("适用课程")),
                "批次": [],
                "记录数": 0,
                "到货数量": 0,
                "已核对数量": 0,
                "缺货数量": 0,
                "状态": "待核对",
            })
            item["记录数"] += 1
            item["到货数量"] += rules.to_int(row.get("到货数量"))
            status = rules.text(row.get("status"))
            if status in rules.VERIFIED_STATUSES:
                item["已核对数量"] += rules.to_int(row.get("到货数量"))
            elif status == rules.SHORTAGE_STATUS:
                item["缺货数量"] += rules.to_int(row.get("到货数量"))
            batch = rules.text(row.get("到货批次"))
            if batch and batch not in item["批次"]:
                item["批次"].append(batch)
        result = []
        for item in groups.values():
            item["状态"] = self.receipt_status(item["订单编号"], item["回单编号"])
            result.append(item)
        result.sort(key=lambda item: (item["订单编号"], item["回单编号"]))
        return result

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = normalize_values(values or {})
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        verb = _ACTION_VERBS.get(action, action)
        current = rules.text(entry.get("status"))
        if verb in ("核对回执", "核对导入回执", "确认到货"):
            if current == "已冲销":
                return None, f"{self.ENTITY} {entry_id} 已冲销，不可核对"
            if current == "已核对":
                return None, f"{self.ENTITY} {entry_id} 已是已核对状态，无需重复核对"
        if verb in ("更正缺货", "标记缺货"):
            if current == "已冲销":
                return None, f"{self.ENTITY} {entry_id} 已冲销，不可更正"
            if current == rules.SHORTAGE_STATUS:
                return None, f"{self.ENTITY} {entry_id} 已是缺货状态，无需重复更正"
        if verb == "冲销记录" and current == "已冲销":
            return None, f"{self.ENTITY} {entry_id} 已是已冲销状态，无需重复冲销"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        proof_note = rules.text(values.get("缺货证明说明")) or rules.text(values.get("缺货证明"))
        if verb in ("更正缺货", "标记缺货") and proof_note:
            entry["缺货证明说明"] = proof_note

        result, message = super().run_action(entry_id, action)
        if result is None:
            return None, message

        if verb in ("核对回执", "核对导入回执", "确认到货"):
            entry["核对结论"] = "已核对"
            message = (
                f"回单 {rules.text(entry.get('回单编号')) or '（无编号）'} 已核对，"
                f"{rules.to_int(entry.get('到货数量'))} 册计入订单"
            )
        elif verb in ("更正缺货", "标记缺货"):
            entry["核对结论"] = "缺货"
            message = (
                f"批次 {rules.text(entry.get('到货批次')) or entry_id} 已更正为缺货，"
                f"不计入已核对数量"
            )

        order_no = rules.text(entry.get("订单编号"))
        self._refresh_order(order_no)
        order = rules.find_order(order_no)
        if order is not None:
            if verb in ("核对回执", "核对导入回执", "确认到货"):
                event = "到货回执已核对"
            elif verb in ("更正缺货", "标记缺货"):
                event = "到货已更正为缺货"
            else:
                event = "到货记录已冲销"
            rules.sync_syllabus_todos(
                rules.text(order.get("适用课程")),
                event,
                order_no=order_no,
                receipt_no=rules.latest_receipt(
                    order_no,
                    rules.text(order.get("适用课程")),
                    rules.text(order.get("教材名称")),
                ),
                textbook=rules.text(order.get("教材名称")),
            )
        return result, message

    # ------------------------------------------------------------------
    # 订单汇总
    # ------------------------------------------------------------------
    def _refresh_order(self, order_no: str) -> None:
        order = rules.find_order(order_no)
        if order is None:
            return
        course = rules.text(order.get("适用课程"))
        textbook = rules.text(order.get("教材名称"))
        summary = rules.order_summary(order_no, course, textbook)
        order["已核对数量"] = summary["已核对数量"]
        order["缺货数量"] = summary["缺货数量"]
        order["待核对数量"] = summary["待核对数量"]
        if summary["已核对数量"]:
            order["教材回单编号"] = rules.latest_receipt(order_no, course, textbook)

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["receipts"] = self.list_receipts()
        return snapshot


def parse_arrival_csv(content: bytes) -> list[dict[str, Any]]:
    """解析上传的 CSV 到货文件；首行作为表头，容忍 BOM 与空白列。"""
    text_content = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text_content))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        row = {
            str(key).strip(): (value or "").strip()
            for key, value in raw.items()
            if key is not None and str(key).strip()
        }
        if any(value for value in row.values()):
            rows.append(row)
    return rows
