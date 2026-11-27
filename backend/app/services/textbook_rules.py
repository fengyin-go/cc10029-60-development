"""教材到货与大纲采用资格的共享规则。

这些函数只读取内存仓库，不依赖任何模块服务，避免教材到货、教材选用和
大纲修订之间出现循环导入。金额/数量一律按整数处理。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

ARRIVAL_MODULE = "textbook_arrival"
SELECT_MODULE = "textbook_select"
SYLLABUS_MODULE = "syllabus_rev"

# 只有这些状态代表“回执已核对、数量可以计入订单”
VERIFIED_STATUSES = ("已核对", "已确认")
SHORTAGE_STATUS = "缺货"
VOID_STATUS = "已冲销"


def text(value: Any) -> str:
    """统一去空白的字符串转换。"""
    return str(value if value is not None else "").strip()


def today() -> str:
    """当天日期（ISO 格式），用于导入时间等展示字段。"""
    return date.today().isoformat()


def to_int(value: Any, default: int = 0) -> int:
    """尽力把数量字段转成非负整数，无法识别时返回默认值。"""
    if value is None or value == "":
        return default
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(number, 0)


def _rows(module: str) -> list[dict[str, Any]]:
    return store.rows(module)


# ---------------------------------------------------------------------------
# 订单 / 选用单
# ---------------------------------------------------------------------------
def find_order(order_no: str) -> dict[str, Any] | None:
    """按订单编号查找教材选用单；同号时取最新登记的一条。"""
    order_no = text(order_no)
    if not order_no:
        return None
    matches = [
        row for row in _rows(SELECT_MODULE)
        if text(row.get("订单编号")) == order_no
    ]
    return matches[-1] if matches else None


def selection_for_course(
    course: str, textbook: str = ""
) -> dict[str, Any] | None:
    """找出某门课程当前最相关的教材选用单。

    优先同一教材名称，其次已订购，最后取最新登记。
    """
    course = text(course)
    textbook = text(textbook)
    if not course:
        return None
    candidates = [
        row for row in _rows(SELECT_MODULE)
        if text(row.get("适用课程")) == course
    ]
    if textbook:
        exact = [row for row in candidates if text(row.get("教材名称")) == textbook]
        if not exact:
            # 指定了教材名称却找不到对应选用单，不再退化到同课程任意教材，
            # 否则会把其他教材的订单/回单误认成这本教材的。
            return None
        candidates = exact
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            text(row.get("status")) == "已订购",
            to_int(row.get("id")),
        )
    )
    return candidates[-1]


def resolve_order(syllabus: dict[str, Any]) -> dict[str, Any] | None:
    """找大纲修订所采用的教材订单。"""
    order_no = text(syllabus.get("教材订单编号"))
    if order_no:
        order = find_order(order_no)
        if order is not None:
            return order
    return selection_for_course(
        text(syllabus.get("课程名称")), text(syllabus.get("采用教材"))
    )


# ---------------------------------------------------------------------------
# 到货记录
# ---------------------------------------------------------------------------
def arrival_rows(
    order_no: str = "", course: str = "", textbook: str = ""
) -> list[dict[str, Any]]:
    """到货记录筛选。

    ``order_no`` 与 ``course`` 同时存在时会一起参与过滤——这是保证
    “其他课程的同名书不计入 H-31”的关键。
    """
    order_no = text(order_no)
    course = text(course)
    textbook = text(textbook)
    result: list[dict[str, Any]] = []
    for row in _rows(ARRIVAL_MODULE):
        if order_no and text(row.get("订单编号")) != order_no:
            continue
        if course and text(row.get("适用课程")) != course:
            continue
        if textbook and text(row.get("教材名称")) != textbook:
            continue
        result.append(row)
    return result


def verified_quantity(
    order_no: str = "", course: str = "", textbook: str = ""
) -> int:
    return sum(
        to_int(row.get("到货数量"))
        for row in arrival_rows(order_no, course, textbook)
        if text(row.get("status")) in VERIFIED_STATUSES
    )


def pending_quantity(
    order_no: str = "", course: str = "", textbook: str = ""
) -> int:
    return sum(
        to_int(row.get("到货数量"))
        for row in arrival_rows(order_no, course, textbook)
        if text(row.get("status")) == "待核对"
    )


def shortage_rows(
    order_no: str = "", course: str = "", textbook: str = ""
) -> list[dict[str, Any]]:
    return [
        row for row in arrival_rows(order_no, course, textbook)
        if text(row.get("status")) == SHORTAGE_STATUS
    ]


def shortage_batches(
    order_no: str = "", course: str = "", textbook: str = ""
) -> list[str]:
    batches = []
    for row in shortage_rows(order_no, course, textbook):
        label = text(row.get("到货批次")) or f"#{row.get('id')}"
        batches.append(label)
    return batches


def latest_receipt(
    order_no: str = "", course: str = "", textbook: str = ""
) -> str:
    candidates = [
        row for row in arrival_rows(order_no, course, textbook)
        if text(row.get("status")) in VERIFIED_STATUSES
        and text(row.get("回单编号"))
    ]
    return text(candidates[-1].get("回单编号")) if candidates else ""


def receipt_rows(order_no: str, receipt_no: str) -> list[dict[str, Any]]:
    return [
        row for row in _rows(ARRIVAL_MODULE)
        if text(row.get("订单编号")) == text(order_no)
        and text(row.get("回单编号")) == text(receipt_no)
    ]


# ---------------------------------------------------------------------------
# 采用资格
# ---------------------------------------------------------------------------
def evaluate_adoption(syllabus: dict[str, Any]) -> dict[str, Any]:
    """计算一份大纲修订当前的教材采用资格。

    返回结构化结果，既是审批门槛，也直接展示给审批人：
    - ``eligible``：能否继续办理采用/审批；
    - ``verdict``：面向审批人的一句话结论；
    - 数量、缺货批次、回单号等明细用于解释结论。
    """
    course = text(syllabus.get("课程名称"))
    order = resolve_order(syllabus)
    order_no = text(syllabus.get("教材订单编号")) or (
        text(order.get("订单编号")) if order else ""
    )
    textbook = text(syllabus.get("采用教材")) or (
        text(order.get("教材名称")) if order else ""
    )
    replacement = text(syllabus.get("替换教材"))
    replacement_confirmed = (
        text(syllabus.get("教材采用状态")) == "已替换" and bool(replacement)
    )

    # 订单号缺失时退化为按课程 + 教材名称匹配，避免把其他课程同名书算进来
    scope_order = order_no if order_no else ""
    scope_course = course if not order_no else ""
    arrivals = arrival_rows(scope_order, scope_course, textbook) if textbook else []
    has_arrival_context = bool(arrivals)

    linked = bool(order) and bool(
        order_no
        or text(order.get("status")) == "已订购"
        or has_arrival_context
    )
    if not linked and has_arrival_context:
        # 没有选用单，但已经登记了同课程到货，也视为关联教材材料
        linked = True

    required = to_int(syllabus.get("教材需求数量"))
    if required <= 0 and order is not None:
        required = to_int(order.get("订购数量"))

    confirmed = verified_quantity(scope_order, scope_course, textbook) if textbook else 0
    pending = pending_quantity(scope_order, scope_course, textbook) if textbook else 0
    shortages = shortage_rows(scope_order, scope_course, textbook) if textbook else []
    batches = shortage_batches(scope_order, scope_course, textbook) if textbook else []
    shortage_quantity = sum(to_int(row.get("到货数量")) for row in shortages)
    receipt_no = latest_receipt(scope_order, scope_course, textbook) if textbook else ""
    if not receipt_no:
        receipt_no = text(syllabus.get("教材回单编号"))

    replacement_receipt = text(syllabus.get("替换回单编号"))
    if replacement_confirmed and not replacement_receipt:
        replacement_receipt = latest_receipt(*_replacement_scope(syllabus, replacement))

    adoption_textbook = replacement if replacement_confirmed else textbook
    adoption_receipt = replacement_receipt if replacement_confirmed else receipt_no

    # ---- 判定 ----
    if not linked:
        eligible = True
        verdict = "未关联教材订单"
        message = "未关联教材选用订单，按原有大纲审批流程办理"
    elif replacement_confirmed:
        eligible = True
        verdict = "已确认替换教材"
        receipt_part = f"，回单 {adoption_receipt}" if adoption_receipt else ""
        message = f"已确认替换教材《{replacement}》{receipt_part}，可按替换教材完成采用"
    elif confirmed <= 0:
        eligible = False
        verdict = "教材材料尚未核对"
        message = (
            f"教材《{textbook or '未指定'}》尚无已核对到货材料，"
            f"不能办理采用；请先核对导入回执"
        )
    elif shortages:
        if required <= 0 or confirmed >= required:
            eligible = True
            verdict = "可凭已核对教材材料完成采用"
            need_part = f"，满足需求 {required} 册" if required > 0 else "，需求数量未登记"
            message = (
                f"可凭已核对教材材料完成采用：{textbook} 已核对 {confirmed} 册"
                f"{need_part}；{'、'.join(batches)}缺货不影响本次采用"
            )
        else:
            eligible = False
            verdict = "须先确认替换教材"
            message = (
                f"须先确认替换教材：{textbook} 的{'、'.join(batches)}已被更正为缺货，"
                f"已核对 {confirmed} 册，不足需求 {required} 册（缺货 {shortage_quantity} 册）"
            )
    else:
        if required > 0 and confirmed < required:
            eligible = False
            verdict = "教材到货数量不足"
            message = (
                f"教材《{textbook}》已核对 {confirmed} 册，不足需求 {required} 册，"
                f"须先补齐到货或确认替换教材"
            )
        else:
            eligible = True
            verdict = "可凭已核对教材材料完成采用"
            receipt_part = f"，回单 {receipt_no}" if receipt_no else ""
            message = (
                f"可凭已核对教材材料完成采用：{textbook} 已核对 {confirmed} 册"
                f"{('，需求 ' + str(required) + ' 册') if required else ''}{receipt_part}"
            )

    return {
        "linked": linked,
        "eligible": eligible,
        "verdict": verdict,
        "message": message,
        "course": course,
        "textbook": textbook,
        "order_no": order_no,
        "receipt_no": receipt_no,
        "required_quantity": required,
        "confirmed_quantity": confirmed,
        "pending_quantity": pending,
        "shortage": bool(shortages),
        "shortage_batches": batches,
        "shortage_quantity": shortage_quantity,
        "replacement": replacement,
        "replacement_confirmed": replacement_confirmed,
        "replacement_receipt_no": replacement_receipt,
        "adoption_textbook": adoption_textbook,
        "adoption_receipt_no": adoption_receipt,
        "arrival_count": len(arrivals),
        "has_verified_material": confirmed > 0,
    }


def _replacement_scope(
    syllabus: dict[str, Any], replacement: str
) -> tuple[str, str, str]:
    """返回替换教材查询到货记录时的 (订单号, 课程, 教材名称)。"""
    course = text(syllabus.get("课程名称"))
    selection = selection_for_course(course, replacement)
    if selection is not None and text(selection.get("订单编号")):
        return text(selection.get("订单编号")), "", replacement
    return "", course, replacement


# ---------------------------------------------------------------------------
# 大纲待办同步
# ---------------------------------------------------------------------------
def sync_syllabus_todos(
    course: str,
    event: str,
    *,
    order_no: str = "",
    receipt_no: str = "",
    textbook: str = "",
) -> list[int]:
    """把教材确认/到货结果同步到同课程处于“待审批”的大纲修订。

    只更新待审批记录，已经通过/否决的版本保留当时的教材快照，不被后续
    缺货更正覆盖。
    """
    course = text(course)
    if not course:
        return []
    updated: list[int] = []
    for row in _rows(SYLLABUS_MODULE):
        if text(row.get("课程名称")) != course:
            continue
        if text(row.get("status")) == "待审批":
            self_tag = "待审批"
        elif text(row.get("status")) in ("已通过", "已否决"):
            # 已批准/否决的版本只记录同步事件，不改写审批快照
            self_tag = "已归档版本"
        else:
            continue
        if self_tag == "待审批":
            if order_no and not text(row.get("教材订单编号")):
                row["教材订单编号"] = order_no
            if textbook and not text(row.get("采用教材")):
                row["采用教材"] = textbook
            if receipt_no:
                row["教材回单编号"] = receipt_no

        info = evaluate_adoption(row)
        if self_tag == "待审批":
            row["采用资格"] = info["verdict"]
            row["教材同步说明"] = f"{event}；{info['message']}"
            row["pending"] = True
            row["abnormal"] = bool(info["linked"]) and not bool(info["eligible"])
            if info["linked"] and not text(row.get("采用教材")) and info["textbook"]:
                row["采用教材"] = info["textbook"]
        else:
            row["教材同步记录"] = f"{event}；已批准版本保留审批时教材快照"
        updated.append(to_int(row.get("id")))
    return updated


def order_summary(
    order_no: str, course: str = "", textbook: str = ""
) -> dict[str, Any]:
    """汇总一个订单的到货情况，严格按订单号 + 课程 + 教材过滤。"""
    order_no = text(order_no)
    order = find_order(order_no)
    if not course and order is not None:
        course = text(order.get("适用课程"))
    if not textbook and order is not None:
        textbook = text(order.get("教材名称"))
    rows = arrival_rows(order_no, course, textbook)
    verified = verified_quantity(order_no, course, textbook)
    pending = pending_quantity(order_no, course, textbook)
    shortages = shortage_rows(order_no, course, textbook)
    receipts: dict[str, dict[str, Any]] = {}
    for row in rows:
        receipt_no = text(row.get("回单编号")) or "（无回单）"
        item = receipts.setdefault(receipt_no, {
            "回单编号": receipt_no,
            "记录数": 0,
            "已核对数量": 0,
            "缺货数量": 0,
        })
        item["记录数"] += 1
        status = text(row.get("status"))
        if status in VERIFIED_STATUSES:
            item["已核对数量"] += to_int(row.get("到货数量"))
        elif status == SHORTAGE_STATUS:
            item["缺货数量"] += to_int(row.get("到货数量"))
    return {
        "订单编号": order_no,
        "适用课程": text(course) or (text(order.get("适用课程")) if order else ""),
        "教材名称": text(textbook) or (text(order.get("教材名称")) if order else ""),
        "记录数": len(rows),
        "已核对数量": verified,
        "待核对数量": pending,
        "缺货数量": sum(to_int(row.get("到货数量")) for row in shortages),
        "缺货批次": [text(row.get("到货批次")) for row in shortages],
        "回单": list(receipts.values()),
        "已确认十册": verified,
    }
