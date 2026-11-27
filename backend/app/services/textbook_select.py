"""教材选用领域服务：选用单登记、审核、订购，含跨学期冲突与到货同步。"""
from __future__ import annotations

from typing import Any

from app.services import textbook_rules as rules
from app.services.base import BaseModuleService
from app.store import store


class TextbookSelectService(BaseModuleService):
    MODULE = "textbook_select"
    ENTITY = "选用单"
    OPERATION_TERM = "审核选用"
    LIST_FIELDS = [
        "教材名称", "主编", "出版社", "适用课程", "选用理由", "选用学期",
        "审批结果", "订单编号", "订购数量", "已核对数量", "缺货数量",
        "教材回单编号", "备注说明",
    ]
    REQUIRED_FIELDS = ["教材名称", "主编", "出版社"]
    STATUS_ORDER = ["草稿", "待审核", "审核通过", "已订购"]
    ACTION_RULES = {"审核选用": "审核通过", "退回重选": "草稿", "确认订购": "已订购"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """创建选用单：同一课程 + 同一学期不能重复选用。"""
        course = str(values.get("适用课程", "") or "").strip()
        semester = str(values.get("选用学期", "") or "").strip()

        if course and semester:
            for row in store.rows("textbook_select"):
                if (str(row.get("适用课程", "")).strip() == course
                        and str(row.get("选用学期", "")).strip() == semester
                        and row.get("status") in ("审核通过", "待审核")):
                    raise ValueError(
                        f"教材冲突：{course} 在 {semester} 学期已有选用的教材"
                    )

        order_no = rules.text(values.get("订单编号"))
        if order_no:
            for row in store.rows("textbook_select"):
                if (rules.text(row.get("订单编号")) == order_no
                        and row.get("status") in ("待审核", "审核通过", "已订购")):
                    raise ValueError(f"订单冲突：订单编号 {order_no} 已被选用单占用")

        entry, missing = super().create_entry(values)
        if entry is not None:
            entry["订购数量"] = rules.to_int(values.get("订购数量"))
            entry["已核对数量"] = 0
            entry["缺货数量"] = 0
            entry["教材回单编号"] = rules.text(values.get("教材回单编号"))
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含状态约束与大纲待办同步。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 确认订购：必须先审核通过
        if action == "确认订购":
            if entry.get("status") != "审核通过":
                return None, f"{self.ENTITY} {entry_id} 必须先审核通过，才能订购"

        result, message = super().run_action(entry_id, action)
        if result is None:
            return None, message

        course = rules.text(entry.get("适用课程"))
        order_no = rules.text(entry.get("订单编号"))
        textbook = rules.text(entry.get("教材名称"))

        if action == "确认订购":
            # 订单号缺省时用选用单编号兜底，方便后续回单按订单归集
            if not order_no:
                order_no = f"TB-{entry_id:04d}"
                entry["订单编号"] = order_no
            if not rules.to_int(entry.get("订购数量")):
                entry["订购数量"] = 0
            self._refresh_order(entry)
            receipt_no = rules.latest_receipt(order_no, course, textbook)
            if receipt_no:
                entry["教材回单编号"] = receipt_no
            updated = rules.sync_syllabus_todos(
                course, "教材已确认订购",
                order_no=order_no,
                receipt_no=receipt_no,
                textbook=textbook,
            )
            if updated:
                message = (
                    f"{self.ENTITY}已确认订购，订单 {order_no} 已同步 "
                    f"{len(updated)} 条大纲审批待办"
                )
        elif action == "审核选用":
            rules.sync_syllabus_todos(
                course, "教材选用已审核通过",
                order_no=order_no, textbook=textbook,
            )
        return result, message

    def _refresh_order(self, entry: dict[str, Any]) -> None:
        order_no = rules.text(entry.get("订单编号"))
        course = rules.text(entry.get("适用课程"))
        textbook = rules.text(entry.get("教材名称"))
        if not order_no:
            return
        summary = rules.order_summary(order_no, course, textbook)
        entry["已核对数量"] = summary["已核对数量"]
        entry["缺货数量"] = summary["缺货数量"]

    def order_status(self, order_no: str) -> dict[str, Any]:
        """订单到货汇总，严格按订单号 + 课程 + 教材过滤。"""
        return rules.order_summary(order_no)
