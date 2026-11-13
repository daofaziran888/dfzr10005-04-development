"""冷数据成本试算的业务规则测试：候选分类、冲突裁决、并发确认与口径版本隔离。

直接运行：backend/.venv/bin/python -I tests/test_cost_trial.py
（也兼容 pytest：每个 test_* 都是独立用例。）
"""
from __future__ import annotations

import copy
import threading
from datetime import date

from app.seed import SEED_ROWS
from app.services.cost_trial import CostTrialService
from app.services.heatmap import HeatmapService
from app.store import store

TODAY = date(2026, 11, 13)


def reset_store() -> None:
    """每个用例前把内存仓库重置回种子数据（深拷贝，避免嵌套列表被串改）。"""
    store._tables = {name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()}


def _trial(scope: str = "全域") -> dict:
    service = CostTrialService()
    trial, errors = service.create_trial(scope, "测试员", today=TODAY)
    assert not errors, f"试算生成失败：{errors}"
    return trial


def _bucket(trial: dict, name: str) -> dict[str, dict]:
    return {item["表名称"]: item for item in trial["候选"][name]}


def test_trial_buckets_and_sorting() -> None:
    reset_store()
    trial = _trial()
    assert set(_bucket(trial, "迁移候选")) == {
        "dwd_user_login_log_hi", "dm_user_profile_monthly", "ods_crm_customer_archive", "ods_iot_device_metrics",
    }
    assert set(_bucket(trial, "冲突裁决")) == {"dwd_refund_detail_di", "dwd_log_app_event_di"}
    assert set(_bucket(trial, "热点保留")) == {"ads_risk_feature_wide"}
    assert trial["汇总"]["无建议表数"] == 2
    savings = [item["预计节省元每月"] for item in trial["候选"]["迁移候选"]]
    assert savings == sorted(savings, reverse=True), "迁移候选必须按预计节省降序"
    for item in trial["候选"]["迁移候选"]:
        assert item["建议目标层"] != item["当前层"]
        assert item["预计释放容量GB"] > 0
        assert item["风险明细"], "候选必须列出风险明细"


def test_hot_partition_guardrail() -> None:
    reset_store()
    trial = _trial()
    kept = _bucket(trial, "热点保留")["ads_risk_feature_wide"]
    assert kept["建议目标层"] == "热层", "热点分区不得仅因总量大被判为冷数据"
    assert kept["热点保护命中"] is True
    assert "热点分区" in kept["裁决依据"]


def test_conflict_resolved_by_priority() -> None:
    reset_store()
    trial = _trial()
    judged = _bucket(trial, "冲突裁决")["dwd_refund_detail_di"]
    assert judged["建议目标层"] == "温层", "热度条件 P1 应优先于容量条件 P2"
    assert "管理优先级" in judged["裁决依据"]
    assert judged["冲突条件"] == ["热度条件→温层", "容量条件→冷层"]


def test_guardrail_holds_when_size_priority_first() -> None:
    reset_store()
    service = CostTrialService()
    caliber, errors = service.update_caliber(
        {"分层条件": [{"条件编号": "RULE-HEAT", "管理优先级": 2}, {"条件编号": "RULE-SIZE", "管理优先级": 1}]},
        "测试员",
    )
    assert not errors
    trial, errors = service.create_trial("交易域", "测试员", today=TODAY)
    assert not errors
    judged = {item["表名称"]: item for item in trial["候选"]["冲突裁决"]}
    refund = judged["dwd_refund_detail_di"]
    assert refund["建议目标层"] != "冷层", "容量条件优先级再高也不得把热点分区表判冷"
    assert refund["热点保护命中"] is True


def test_estimation_math() -> None:
    reset_store()
    trial = _trial()
    item = _bucket(trial, "迁移候选")["dwd_user_login_log_hi"]
    assert item["预计释放容量GB"] == 2400
    assert item["预计节省元每月"] == 2400 * (1.2 - 0.12)
    assert item["迁移成本元"] == 2400 * 0.8
    assert item["试算周期净节省元"] == round(2400 * 1.08 * 12 - 2400 * 0.8, 1)
    assert any("维护窗口" in risk for risk in item["风险明细"]), "2400GB 迁移应触发窗口风险"
    assert any("水位" in risk for risk in item["风险明细"]), "冷层水位 84% 应触发水位风险"


def test_concurrent_confirm_single_success() -> None:
    reset_store()
    service = CostTrialService()
    trial = _trial()
    results: list[tuple[bool, str]] = []

    def confirm(operator: str) -> None:
        snapshot, message = service.confirm_trial(trial["id"], operator)
        results.append((snapshot is not None, message))

    threads = [threading.Thread(target=confirm, args=(f"操作员{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for ok, _ in results if ok) == 1, "并行确认同一试算版本只允许一个成功"
    assert len(store.rows("cost_snapshot")) == 1, "重复确认不能产生两份有效方案"
    reports = [row for row in store.rows("cost") if row.get("演算快照")]
    assert len(reports) == 1, "只能生成一份引用快照的成本报告"


def test_duplicate_confirm_rejected() -> None:
    reset_store()
    service = CostTrialService()
    trial = _trial()
    snapshot, message = service.confirm_trial(trial["id"], "张三")
    assert snapshot is not None
    again, message = service.confirm_trial(trial["id"], "李四")
    assert again is None and "已由 张三" in message and "未生效" in message
    assert len(store.rows("cost_snapshot")) == 1


def test_caliber_change_keeps_history() -> None:
    reset_store()
    service = CostTrialService()
    trial_a = _trial()
    snapshot, _ = service.confirm_trial(trial_a["id"], "张三")
    report = [row for row in store.rows("cost") if row.get("演算快照")][0]
    assert report["口径版本"] == "CLB-2026-11-A"

    caliber, errors = service.update_caliber({"成本口径": {"容量条件冷层阈值GB": 2000}, "操作人": "李四"}, "李四")
    assert not errors
    assert caliber["口径版本"] == "CLB-2026-11-B"

    trial_b, errors = service.create_trial("全域", "李四", today=TODAY)
    assert not errors
    assert trial_b["口径版本"] == "CLB-2026-11-B", "新报告按更新后的口径计算"
    assert trial_a["口径版本"] == "CLB-2026-11-A", "既有试算保留原口径"
    assert report["口径版本"] == "CLB-2026-11-A", "既有成本报告按原口径保留"
    moved = _bucket(trial_b, "迁移候选")
    assert "dwd_refund_detail_di" in moved, "冷层阈值调到 2000GB 后该表应转为迁移候选"
    assert "dwd_refund_detail_di" in _bucket(trial_a, "冲突裁决"), "旧试算的分类不随新口径改变"
    assert snapshot["口径内容"]["成本口径"]["容量条件冷层阈值GB"] == 1000, "快照内保留原采样口径"


def test_snapshot_references_sync() -> None:
    reset_store()
    service = CostTrialService()
    trial = _trial()
    snapshot, _ = service.confirm_trial(trial["id"], "张三")
    code = snapshot["快照编号"]
    report = [row for row in store.rows("cost") if row.get("演算快照") == code][0]
    assert report["报告编号"] == snapshot["报告编号"]
    tiers = {row["分层名称"]: row for row in store.rows("tier")}
    for name in ("热层", "温层", "冷层"):
        assert any(code in ref for ref in tiers[name]["成本报告引用"]), f"{name}应同步成本报告引用"
    heat = {row["表名称"]: row for row in store.rows("heatmap")}
    assert code in heat["dwd_user_login_log_hi"]["试算引用"], "热度记录必须引用演算快照"
    batches = {item["采样批次"]: item for item in HeatmapService().list_batches()}
    assert code in batches["HB-20261110-A"]["引用快照"], "采样批次应能追溯到演算快照"


def main() -> None:
    cases = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for case in cases:
        case()
        print(f"PASS {case.__name__}")
    print(f"\n{len(cases)} 个用例全部通过")


if __name__ == "__main__":
    main()
