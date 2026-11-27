"""教材分批到货、回执核对与大纲采用资格的业务约束测试。"""
import pytest

from app.services import service_for
from app.services import textbook_rules as rules
from app.store import store
from app.services.syllabus_rev import SyllabusRevService
from app.services.textbook_arrival import TextbookArrivalService
from app.services.textbook_select import TextbookSelectService


def clear_all():
    for name in store.module_names():
        store.rows(name).clear()


def seed_h31():
    """订单 H-31：数据结构课程，需求 10 册，第一批十册已核对。"""
    select = TextbookSelectService()
    select.create_entry({
        "教材名称": "数据结构（第2版）",
        "主编": "严蔚敏",
        "出版社": "清华大学出版社",
        "适用课程": "数据结构",
        "选用学期": "2026-秋季",
        "订单编号": "H-31",
        "订购数量": 10,
    })
    store.rows("textbook_select")[-1]["status"] = "已订购"
    # 其他课程的同名书，另有 25 册已核对
    select.create_entry({
        "教材名称": "数据结构（第2版）",
        "主编": "严蔚敏",
        "出版社": "清华大学出版社",
        "适用课程": "程序设计基础",
        "选用学期": "2026-秋季",
        "订单编号": "H-77",
        "订购数量": 25,
    })
    store.rows("textbook_select")[-1]["status"] = "已订购"

    arrival = TextbookArrivalService()
    arrival.import_receipt(
        rows=[{
            "订单编号": "H-31", "回单编号": "RCPT-H31-01", "到货批次": "第一批",
            "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "10",
        }],
        filename="H-31-第一批.csv", receipt_no="RCPT-H31-01",
        order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
    )
    store.rows("textbook_arrival")[0]["status"] = "已核对"
    arrival.import_receipt(
        rows=[{
            "订单编号": "H-77", "回单编号": "RCPT-H77-01", "到货批次": "第一批",
            "教材名称": "数据结构（第2版）", "适用课程": "程序设计基础",
            "到货数量": "25",
        }],
        filename="H-77.csv", receipt_no="RCPT-H77-01",
        order_no="H-77", course="程序设计基础", textbook="数据结构（第2版）",
    )
    store.rows("textbook_arrival")[1]["status"] = "已核对"
    return arrival, select


def seed_syllabus(status="待审批"):
    service = SyllabusRevService()
    entry, _ = service.create_entry({
        "课程名称": "数据结构",
        "修订章节": "第5章 图结构",
        "原内容": "图的定义和基本术语",
        "修订内容": "图的定义、存储结构和深度优先搜索",
        "采用教材": "数据结构（第2版）",
        "教材订单编号": "H-31",
        "教材需求数量": 10,
    })
    entry["status"] = status
    return entry


class TestArrivalImportIdempotency:
    """同一回单重复导入不得重复计数。"""

    def setup_method(self):
        clear_all()
        self.arrival, self.select = seed_h31()

    def test_first_batch_ten_confirmed_once(self):
        assert rules.verified_quantity("H-31") == 10

    def test_same_receipt_import_twice_does_not_double_count(self):
        before = rules.verified_quantity("H-31")
        receipt = self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-01", "到货批次": "第一批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "10",
            }],
            filename="dup.csv", receipt_no="RCPT-H31-01",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        assert receipt["重复导入"] is True
        assert receipt["新增行数"] == 0
        assert rules.verified_quantity("H-31") == before == 10
        h31_rows = rules.arrival_rows("H-31", "数据结构", "数据结构（第2版）")
        assert len(h31_rows) == 1

    def test_same_receipt_rows_import_twice_does_not_double_count(self):
        # 不经回单级拦截，逐行 create_entry 也必须识别重复
        entry, _ = self.arrival.create_entry({
            "订单编号": "H-31", "回单编号": "RCPT-H31-01", "到货批次": "第一批",
            "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "10",
        })
        assert entry is not None and entry["重复导入"] is True
        assert rules.verified_quantity("H-31") == 10

    def test_other_course_same_name_book_not_counted_for_h31(self):
        assert rules.verified_quantity("H-77") == 25
        assert rules.verified_quantity("H-31") == 10
        summary = rules.order_summary("H-31")
        assert summary["已核对数量"] == 10
        assert summary["教材名称"] == "数据结构（第2版）"
        assert summary["适用课程"] == "数据结构"

    def test_other_course_arrival_cannot_be_pinned_to_h31(self):
        with pytest.raises(ValueError, match="到货课程不匹配"):
            self.arrival.create_entry({
                "订单编号": "H-31", "回单编号": "RCPT-X", "到货批次": "第三批",
                "教材名称": "数据结构（第2版）", "适用课程": "程序设计基础",
                "到货数量": "5",
            })
        assert rules.verified_quantity("H-31") == 10


class TestArrivalCorrection:
    """到货更正为缺货不得计入已核对数量。"""

    def setup_method(self):
        clear_all()
        self.arrival, self.select = seed_h31()
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )

    def test_correcting_to_shortage_removes_from_confirmed(self):
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        # 未核对前也不计入
        assert rules.verified_quantity("H-31") == 10
        result, message = self.arrival.run_action(second["id"], "更正缺货")
        assert result["status"] == "缺货"
        assert "缺货" in message
        assert rules.verified_quantity("H-31") == 10
        assert rules.shortage_batches("H-31") == ["第二批"]

    def test_correcting_shortage_is_idempotent(self):
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")
        result, message = self.arrival.run_action(second["id"], "更正缺货")
        assert result is None
        assert "无需重复更正" in message

    def test_verify_action_counts_quantity(self):
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        result, _ = self.arrival.run_action(second["id"], "核对回执")
        assert result["status"] == "已核对"
        assert rules.verified_quantity("H-31") == 22

    def test_receipt_status_reflects_correction(self):
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")
        receipts = {item["回单编号"]: item for item in self.arrival.list_receipts()}
        assert receipts["RCPT-H31-01"]["状态"] == "已核对"
        assert receipts["RCPT-H31-02"]["状态"] == "缺货"


class TestSyllabusAdoption:
    """大纲审批与教材采用资格的联动。"""

    def setup_method(self):
        clear_all()
        self.arrival, self.select = seed_h31()
        self.syllabus = SyllabusRevService()
        self.entry = seed_syllabus()

    def test_approval_can_proceed_with_verified_material_despite_shortage(self):
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")

        status = self.syllabus.adoption_status(self.entry["id"])
        assert status["shortage"] is True
        assert status["eligible"] is True
        assert status["verdict"] == "可凭已核对教材材料完成采用"

        result, message = self.syllabus.run_action(self.entry["id"], "审批修订")
        assert result["status"] == "已通过", message

    def test_insufficient_verified_quantity_requires_replacement(self):
        self.entry["教材需求数量"] = 30
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")

        status = self.syllabus.adoption_status(self.entry["id"])
        assert status["eligible"] is False
        assert status["verdict"] == "须先确认替换教材"

        rejected, message = self.syllabus.run_action(self.entry["id"], "审批修订")
        assert rejected is None
        assert "须先确认替换教材" in message
        assert self.entry["status"] == "待审批"

        adopted, message = self.syllabus.run_action(self.entry["id"], "办理采用")
        assert adopted is None
        assert "确认替换" in message

    def test_confirmed_replacement_allows_approval(self):
        self.entry["教材需求数量"] = 30
        result, message = self.syllabus.run_action(
            self.entry["id"], "确认替换教材",
            {"替换教材": "数据结构（第3版）", "替换依据": "教务会议纪要2026-11"},
        )
        assert result is not None, message
        assert result["教材采用状态"] == "已替换"
        assert result["采用资格"] == "已确认替换教材"
        approved, message = self.syllabus.run_action(self.entry["id"], "审批修订")
        assert approved["status"] == "已通过", message
        assert approved["审批时教材"] == "数据结构（第3版）"

    def test_replacement_without_basis_or_receipt_is_rejected(self):
        result, message = self.syllabus.run_action(
            self.entry["id"], "确认替换教材", {"替换教材": "数据结构（第3版）"},
        )
        assert result is None
        assert "替换依据" in message or "到货回单" in message

    def test_approved_version_keeps_textbook_and_receipt_snapshot(self):
        result, _ = self.syllabus.run_action(self.entry["id"], "审批修订")
        assert result["审批时教材"] == "数据结构（第2版）"
        assert result["审批时回单编号"] == "RCPT-H31-01"

        # 审批之后再把另一批更正为缺货，已批准版本仍保留审批当时的快照
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")

        stored = store.find("syllabus_rev", self.entry["id"])
        assert stored["审批时教材"] == "数据结构（第2版）"
        assert stored["审批时回单编号"] == "RCPT-H31-01"
        assert stored["status"] == "已通过"

    def test_arrival_correction_syncs_pending_syllabus_todo(self):
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")

        assert self.entry["采用资格"] == "可凭已核对教材材料完成采用"
        assert "到货已更正为缺货" in self.entry["教材同步说明"]
        assert self.entry["pending"] is True

    def test_duplicate_receipt_import_does_not_change_adoption(self):
        before = self.syllabus.adoption_status(self.entry["id"])
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-01", "到货批次": "第一批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "10",
            }],
            filename="dup.csv", receipt_no="RCPT-H31-01",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        after = self.syllabus.adoption_status(self.entry["id"])
        assert after["confirmed_quantity"] == before["confirmed_quantity"] == 10
        assert after["receipt_no"] == "RCPT-H31-01"

    def test_submit_while_shortage_correction_lands_reports_eligibility(self):
        self.entry["教材需求数量"] = 30
        self.arrival.import_receipt(
            rows=[{
                "订单编号": "H-31", "回单编号": "RCPT-H31-02", "到货批次": "第二批",
                "教材名称": "数据结构（第2版）", "适用课程": "数据结构", "到货数量": "12",
            }],
            filename="H-31-第二批.csv", receipt_no="RCPT-H31-02",
            order_no="H-31", course="数据结构", textbook="数据结构（第2版）",
        )
        second = [r for r in store.rows("textbook_arrival") if r["到货批次"] == "第二批"][0]
        self.arrival.run_action(second["id"], "更正缺货")

        result, message = self.syllabus.run_action(self.entry["id"], "提交审批")
        assert result is not None
        assert result["status"] == "待审批"
        assert "须先确认替换教材" in message

    def test_approved_version_visible_even_after_later_approvals(self):
        first, _ = self.syllabus.run_action(self.entry["id"], "审批修订")
        assert first["审批时回单编号"] == "RCPT-H31-01"

        # 新版本（另一份修订稿）审批通过，不影响旧版本查询到的教材/回单
        second_entry, _ = self.syllabus.create_entry({
            "课程名称": "数据结构", "修订章节": "第6章 查找", "原内容": "顺序查找",
            "采用教材": "数据结构（第2版）", "教材订单编号": "H-31", "教材需求数量": 10,
        })
        self.syllabus.run_action(second_entry["id"], "提交审批")
        second, message = self.syllabus.run_action(second_entry["id"], "审批修订")
        assert second["status"] == "已通过", message

        old = store.find("syllabus_rev", self.entry["id"])
        new = store.find("syllabus_rev", second_entry["id"])
        assert old["审批时教材"] == new["审批时教材"] == "数据结构（第2版）"
        assert old["审批时回单编号"] == "RCPT-H31-01"
        assert new["审批时回单编号"] == "RCPT-H31-01"


class TestOrderConfirmationSync:
    """教材确认订购结果同步到同课程大纲待办。"""

    def setup_method(self):
        clear_all()
        self.syllabus = SyllabusRevService()
        self.entry = seed_syllabus()
        store.rows("textbook_select").clear()

    def test_confirm_order_syncs_pending_syllabus(self):
        select = TextbookSelectService()
        selection, _ = select.create_entry({
            "教材名称": "数据结构（第2版）",
            "主编": "严蔚敏",
            "出版社": "清华大学出版社",
            "适用课程": "数据结构",
            "选用学期": "2026-秋季",
            "订单编号": "H-31",
            "订购数量": 10,
        })
        select.run_action(selection["id"], "审核选用")
        result, message = select.run_action(selection["id"], "确认订购")
        assert result["status"] == "已订购"
        assert "大纲审批待办" in message
        assert self.entry["教材订单编号"] == "H-31"
        assert self.entry["pending"] is True

    def test_confirm_order_requires_approval_first(self):
        select = TextbookSelectService()
        selection, _ = select.create_entry({
            "教材名称": "数据结构（第2版）",
            "主编": "严蔚敏",
            "出版社": "清华大学出版社",
            "适用课程": "数据结构",
            "选用学期": "2026-秋季",
            "订单编号": "H-31",
        })
        result, message = select.run_action(selection["id"], "确认订购")
        assert result is None
        assert "先审核通过" in message
