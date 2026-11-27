"""大纲修订领域服务：修订稿登记、审批、否决，含教材采用资格与替换办理。

审批人提交审批时，先根据到货回执核对本次采用是否仍有资格：

* 已核对教材数量满足需求——即使有缺货批次，也可以凭已核对材料完成采用；
* 已核对数量不足——必须先从审批入口办理“确认替换教材”；
* 已确认替换——以替换教材及其回单继续办理。

每次审批通过都会把当时使用的教材和回单编号固化到审批快照里，后续到货
更正不会再改写已批准版本。
"""
from __future__ import annotations

from typing import Any

from app.services import textbook_rules as rules
from app.services.base import BaseModuleService
from app.store import store


class SyllabusRevService(BaseModuleService):
    MODULE = "syllabus_rev"
    ENTITY = "修订稿"
    OPERATION_TERM = "审批修订"
    LIST_FIELDS = [
        "课程名称", "修订章节", "原内容", "修订内容", "修订理由", "提出教师",
        "提交日期", "采用教材", "教材订单编号", "教材需求数量", "教材回单编号",
        "教材采用状态", "替换教材", "替换回单编号", "采用资格", "教材同步说明",
        "审批时教材", "审批时回单编号", "备注说明",
    ]
    REQUIRED_FIELDS = ["课程名称", "修订章节", "原内容"]
    STATUS_ORDER = ["草稿", "待审批", "已通过", "已否决"]
    ACTION_RULES = {
        "审批修订": "已通过",
        "提交审批": "待审批",
        "否决修订": "已否决",
        # 办理采用 / 确认替换只改变教材采用状态，不改变审批状态
        "办理采用": "待审批",
        "确认替换教材": "待审批",
    }
    NEGATIVE_ACTIONS = []
    ACTION_VALUES_SUPPORTED = True

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        entry["教材需求数量"] = rules.to_int(values.get("教材需求数量"))
        entry["教材采用状态"] = rules.text(values.get("教材采用状态")) or "未办理"
        self._refresh_adoption(entry)
        return entry, missing

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含教材采用资格校验与审批快照。"""
        values = values or {}
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        status = rules.text(entry.get("status"))
        if action in ("审批修订", "否决修订"):
            if status not in ("待审批", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{status}'，不可{action[:2]}"

        if action == "提交审批":
            if status == "已通过":
                return None, f"{self.ENTITY} {entry_id} 已通过审批，无需重复提交"
            # 到货更正可能与审批提交相遇：先按最新回执重算资格，
            # 再通过明确提示告知审批人本次是继续采用还是须先替换，
            # 不直接拒绝提交（否决权仍在审批环节）。
            self._apply_inputs(entry, values)
            info = self._refresh_adoption(entry)
            result, message = super().run_action(entry_id, action)
            if result is not None:
                message = f"{message}；{info['message']}"
            return result, message

        if action == "办理采用":
            return self._handle_adoption(entry, values)

        if action == "确认替换教材":
            return self._handle_replacement(entry, values)

        if action == "审批修订":
            self._apply_inputs(entry, values)
            info = self._refresh_adoption(entry)
            if not info["eligible"]:
                return None, (
                    f"{self.ENTITY} {entry_id} 暂不能审批：{info['message']}；"
                    f"请先在审批入口办理采用或确认替换教材"
                )
            result, message = super().run_action(entry_id, action)
            if result is None:
                return None, message
            self._snapshot_approval(result, info)
            self._flag_related_lesson_prep(
                rules.text(result.get("课程名称")),
                rules.text(result.get("修订章节")),
                entry_id,
            )
            return result, message

        return super().run_action(entry_id, action)

    # ------------------------------------------------------------------
    # 采用 / 替换
    # ------------------------------------------------------------------
    def _handle_adoption(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        status = rules.text(entry.get("status"))
        if status not in ("待审批", "草稿"):
            return None, f"{self.ENTITY} {entry.get('id')} 当前状态为'{status}'，不可办理采用"
        self._apply_inputs(entry, values)
        self._refresh_adoption(entry)
        info = rules.evaluate_adoption(entry)
        if not info["linked"]:
            return None, f"{self.ENTITY} {entry.get('id')} 未关联教材订单，无需办理采用"
        if not info["eligible"]:
            entry["教材采用状态"] = "未办理"
            entry["采用资格"] = "须先确认替换教材"
            return None, (
                f"{self.ENTITY} {entry.get('id')} 不能办理采用：{info['message']}；"
                f"请先确认替换教材"
            )
        entry["教材采用状态"] = "已采用"
        entry["采用资格"] = info["verdict"]
        entry["教材同步说明"] = info["message"]
        self._write_back_order_snapshot(entry, info)
        return entry, f"{self.ENTITY} {entry.get('id')} 已办理采用：{info['message']}"

    def _handle_replacement(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        status = rules.text(entry.get("status"))
        if status not in ("待审批", "草稿"):
            return None, f"{self.ENTITY} {entry.get('id')} 当前状态为'{status}'，不可确认替换"
        self._apply_inputs(entry, values)
        replacement = rules.text(entry.get("替换教材"))
        if not replacement:
            return None, "请填写替换教材名称后再确认替换"
        if replacement == rules.text(entry.get("采用教材")):
            return None, "替换教材不能与原采用教材相同"

        course = rules.text(entry.get("课程名称"))
        selection = rules.selection_for_course(course, replacement)
        rep_order = rules.text(selection.get("订单编号")) if selection else ""
        rep_course = "" if rep_order else course
        rep_receipt = rules.latest_receipt(rep_order, rep_course, replacement)
        rep_confirmed = rules.verified_quantity(rep_order, rep_course, replacement)
        if rep_confirmed <= 0 and not rep_receipt:
            # 允许无独立到货记录的替换，但需要留下替换依据
            note = rules.text(values.get("替换依据")) or rules.text(entry.get("替换依据"))
            if not note:
                return None, (
                    f"替换教材《{replacement}》没有可核对的到货回执，"
                    f"请填写替换依据或先导入该教材的到货回单"
                )
            entry["替换依据"] = note

        if rules.text(entry.get("教材采用状态")) != "已替换":
            entry["教材采用状态"] = "已替换"
        if rep_receipt:
            entry["替换回单编号"] = rep_receipt
        info = self._refresh_adoption(entry)
        return entry, f"{self.ENTITY} {entry.get('id')} 已确认替换教材《{replacement}》"

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _apply_inputs(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        mapping = {
            "采用教材": "采用教材",
            "教材名称": "采用教材",
            "教材订单编号": "教材订单编号",
            "订单编号": "教材订单编号",
            "教材需求数量": "教材需求数量",
            "需求数量": "教材需求数量",
            "替换教材": "替换教材",
            "替换回单编号": "替换回单编号",
            "教材回单编号": "教材回单编号",
        }
        for source, target in mapping.items():
            value = values.get(source)
            if value is None or value == "":
                continue
            if target == "教材需求数量":
                entry[target] = rules.to_int(value)
            else:
                entry[target] = rules.text(value)

    def _refresh_adoption(self, entry: dict[str, Any]) -> dict[str, Any]:
        info = rules.evaluate_adoption(entry)
        entry["采用资格"] = info["verdict"]
        entry["教材同步说明"] = info["message"]
        if info["linked"]:
            if not rules.text(entry.get("教材订单编号")) and info["order_no"]:
                entry["教材订单编号"] = info["order_no"]
            if not rules.text(entry.get("采用教材")) and info["textbook"]:
                entry["采用教材"] = info["textbook"]
            if not rules.text(entry.get("教材需求数量")) and info["required_quantity"]:
                entry["教材需求数量"] = info["required_quantity"]
            if not rules.text(entry.get("教材回单编号")) and info["receipt_no"]:
                entry["教材回单编号"] = info["receipt_no"]
        entry["pending"] = True
        entry["abnormal"] = bool(info["linked"]) and not bool(info["eligible"])
        return info

    def _write_back_order_snapshot(
        self, entry: dict[str, Any], info: dict[str, Any]
    ) -> None:
        """办理采用时，把核对到的回单号写回大纲当前版本（仅待审批）。"""
        if info["receipt_no"]:
            entry["教材回单编号"] = info["receipt_no"]
        if info["order_no"]:
            entry["教材订单编号"] = info["order_no"]

    def _snapshot_approval(
        self, entry: dict[str, Any], info: dict[str, Any]
    ) -> None:
        """固化审批当时使用的教材与回单编号，后续到货更正不得改写。"""
        entry["审批时教材"] = info["adoption_textbook"]
        entry["审批时回单编号"] = info["adoption_receipt_no"]
        entry["审批时采用资格"] = info["verdict"]
        entry["审批时已核对数量"] = info["confirmed_quantity"]
        entry["审批时缺货批次"] = "、".join(info["shortage_batches"])
        entry["审批快照"] = {
            "教材": info["adoption_textbook"],
            "回单编号": info["adoption_receipt_no"],
            "已核对数量": info["confirmed_quantity"],
            "需求数量": info["required_quantity"],
            "缺货批次": list(info["shortage_batches"]),
            "是否替换": info["replacement_confirmed"],
            "审批时采用资格": info["verdict"],
        }
        entry["采用资格"] = info["verdict"]
        entry["教材同步说明"] = info["message"]

    def adoption_status(self, entry_id: int) -> dict[str, Any] | None:
        """查询一份大纲修订当前的采用资格明细。

        审批人据此回答“这次采用还能凭已核对的教材材料完成，还是须先确认
        替换教材”，并同时看到本次审批可沿用的教材与回单编号。
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        info = rules.evaluate_adoption(entry)
        status = rules.text(entry.get("status"))
        return {
            "修订稿": entry_id,
            "课程名称": rules.text(entry.get("课程名称")),
            "审批状态": status,
            "教材采用状态": rules.text(entry.get("教材采用状态")),
            **info,
            "可凭已核对教材材料完成": bool(info["linked"] and info["eligible"]),
            "须先确认替换教材": bool(info["linked"]) and not info["eligible"],
            # 已批准版本固定回放审批快照；待审批版本给出当前将要沿用的材料
            "本次采用教材": (
                rules.text(entry.get("审批时教材"))
                if status == "已通过"
                else info["adoption_textbook"]
            ),
            "本次回单编号": (
                rules.text(entry.get("审批时回单编号"))
                if status == "已通过"
                else info["adoption_receipt_no"]
            ),
            "审批时教材": rules.text(entry.get("审批时教材")),
            "审批时回单编号": rules.text(entry.get("审批时回单编号")),
        }

    def _flag_related_lesson_prep(
        self, course_name: str, chapter: str, syllabus_id: int
    ) -> None:
        """大纲修订通过后，标记相关章节的备课记录通知备课教师签阅。"""
        prep_rows = store.rows("lesson_prep")
        for row in prep_rows:
            existing_chapter = str(row.get("备课章节", ""))
            if (course_name.casefold() in existing_chapter.casefold()
                    or chapter.casefold() in existing_chapter.casefold()):
                existing_note = str(row.get("备注说明", ""))
                new_note = f"{existing_note} [大纲修订{syllabus_id}已通过，需复核备课内容]".strip()
                row["备注说明"] = new_note
                if row.get("status") == "已存档":
                    row["status"] = "待签阅"
                    row["pending"] = True
