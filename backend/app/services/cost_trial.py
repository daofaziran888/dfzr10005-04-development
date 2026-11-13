"""冷数据成本试算业务规则：候选演算、冲突裁决、成本估算与版本确认都收在这里。

设计要点：
- 试算按分析范围关联热度记录（含分区访问明细）与分层存储策略，产出三类候选：
  迁移候选（两条件一致）、冲突裁决（按管理优先级判定）、热点保留（热点分区保护）。
- 热点分区不得仅因总量大被判为冷数据：容量条件单独投票冷层且存在热分区时强制回退。
- 确认动作在模块级锁内做检查-置位：同一试算版本只允许一次成功确认，
  重复/并发确认不会生成第二份快照与报告。
- 确认时把当时口径深拷贝进演算快照；口径后续变更不影响已确认报告，
  新试算按新口径重新核算。
"""
from __future__ import annotations

import copy
import threading
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE_TRIAL = "cost_trial"
MODULE_SNAPSHOT = "cost_snapshot"
MODULE_CALIBER = "cost_caliber"
MODULE_REPORT = "cost"
MODULE_HEATMAP = "heatmap"
MODULE_TIER = "tier"

TIER_ORDER = ["热层", "温层", "冷层"]
HEAT_TO_TIER = {"热": "热层", "温": "温层", "冷": "冷层"}
BUCKETS = ["迁移候选", "冲突裁决", "热点保留"]

# 确认动作的互斥锁挂在模块上：即使意外构造了多个 Service 实例，
# 同一试算版本的检查-置位也不会被并发穿透。
CONFIRM_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _today() -> date:
    return date.today()


def _days_between(day: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(day)).days
    except ValueError:
        return 9999


def _next_code(rows: list[dict[str, Any]], field: str, prefix: str) -> str:
    nums = []
    for row in rows:
        code = str(row.get(field) or "")
        if code.startswith(prefix) and code.rsplit("-", 1)[-1].isdigit():
            nums.append(int(code.rsplit("-", 1)[-1]))
    return f"{prefix}-{max(nums, default=0) + 1:04d}"


def _bump_caliber_version(version: str) -> str:
    if version and version[-1].isalpha():
        return version[:-1] + chr(ord(version[-1]) + 1)
    return f"{version}-A"


def _as_number(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


class CostTrialService:
    # ---------- 口径 ----------
    def current_caliber(self) -> dict[str, Any]:
        rows = store.rows(MODULE_CALIBER)
        return rows[-1]

    def list_calibers(self) -> list[dict[str, Any]]:
        return list(reversed(store.rows(MODULE_CALIBER)))

    def update_caliber(self, values: dict[str, Any], operator: str) -> tuple[dict[str, Any] | None, list[str]]:
        """更新热度/成本口径或分层条件优先级，生成新口径版本；历史版本原样保留。"""
        current = copy.deepcopy(self.current_caliber())
        errors: list[str] = []
        for section in ("热度口径", "成本口径"):
            updates = values.get(section) or {}
            if not isinstance(updates, dict):
                errors.append(f"{section} 必须是键值结构")
                continue
            for key, raw in updates.items():
                if key not in current[section]:
                    errors.append(f"{section} 不支持字段「{key}」")
                    continue
                num = _as_number(raw)
                if num is None:
                    errors.append(f"{section}.{key} 必须是正数")
                    continue
                current[section][key] = int(num) if float(num).is_integer() else num
        condition_updates = values.get("分层条件") or []
        if condition_updates:
            if not isinstance(condition_updates, list):
                errors.append("分层条件 必须是列表结构")
            else:
                wanted = {str(item.get("条件编号")): item.get("管理优先级") for item in condition_updates if isinstance(item, dict)}
                priorities: list[int] = []
                for condition in current["分层条件"]:
                    if condition["条件编号"] in wanted:
                        num = _as_number(wanted[condition["条件编号"]])
                        if num is None:
                            errors.append(f"分层条件 {condition['条件编号']} 的管理优先级必须是正整数")
                            continue
                        condition["管理优先级"] = int(num)
                    priorities.append(int(condition["管理优先级"]))
                if len(set(priorities)) != len(priorities):
                    errors.append("分层条件的管理优先级不允许重复")
        if errors:
            return None, errors
        rows = store.rows(MODULE_CALIBER)
        for row in rows:
            row["status"] = "历史口径"
        current["id"] = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        current["口径版本"] = _bump_caliber_version(str(current["口径版本"]))
        current["更新人"] = operator or "未署名"
        current["更新时间"] = _now()
        current["status"] = "生效中"
        rows.append(current)
        return current, []

    # ---------- 试算 ----------
    def list_scopes(self) -> list[str]:
        domains = sorted({str(row.get("业务域")) for row in store.rows(MODULE_HEATMAP) if row.get("业务域")})
        return ["全域", *domains]

    def list_trials(self) -> list[dict[str, Any]]:
        return list(reversed(store.rows(MODULE_TRIAL)))

    def get_trial(self, trial_id: int) -> dict[str, Any] | None:
        return store.find(MODULE_TRIAL, trial_id)

    def create_trial(self, scope: str, operator: str, today: date | None = None) -> tuple[dict[str, Any] | None, list[str]]:
        today = today or _today()
        scope = (scope or "").strip() or "全域"
        if scope not in self.list_scopes():
            return None, [f"分析范围「{scope}」不在可选清单里"]
        records = [row for row in store.rows(MODULE_HEATMAP) if scope == "全域" or row.get("业务域") == scope]
        if not records:
            return None, [f"分析范围「{scope}」内没有可关联的热度记录"]
        caliber = copy.deepcopy(self.current_caliber())
        tier_price = {row["分层名称"]: float(row.get("存储单价") or 0) for row in store.rows(MODULE_TIER)}
        tier_level = {row["分层名称"]: float(row.get("容量水位") or 0) for row in store.rows(MODULE_TIER)}

        buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKETS}
        total_gb = 0.0
        cold_gb = 0.0
        skipped = 0
        for record in records:
            candidate, table_total, table_cold = self._evaluate_table(record, caliber, tier_price, tier_level, today)
            total_gb += table_total
            cold_gb += table_cold
            if candidate is None:
                skipped += 1
                continue
            buckets[candidate["候选类型"]].append(candidate)
        for bucket in buckets.values():
            bucket.sort(key=lambda item: item["预计节省元每月"], reverse=True)

        summary = {
            "评估表数": len(records),
            "候选表数": sum(len(bucket) for bucket in buckets.values()),
            "无建议表数": skipped,
            "迁移候选数": len(buckets["迁移候选"]),
            "冲突裁决数": len(buckets["冲突裁决"]),
            "热点保留数": len(buckets["热点保留"]),
            "评估总容量GB": round(total_gb, 1),
            "冷数据容量GB": round(cold_gb, 1),
            "冷数据占比": f"{(cold_gb / total_gb * 100 if total_gb else 0):.1f}%",
            "预计释放容量GB": round(sum(item["预计释放容量GB"] for bucket in buckets.values() for item in bucket), 1),
            "预计节省元每月": round(sum(item["预计节省元每月"] for bucket in buckets.values() for item in bucket), 1),
            "迁移成本元": round(sum(item["迁移成本元"] for bucket in buckets.values() for item in bucket), 1),
            "试算周期净节省元": round(sum(item["试算周期净节省元"] for bucket in buckets.values() for item in bucket), 1),
        }
        rows = store.rows(MODULE_TRIAL)
        seq = sum(1 for row in rows if str(row.get("试算版本", "")).startswith(f"TRIAL-{today:%Y%m%d}")) + 1
        trial = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "试算版本": f"TRIAL-{today:%Y%m%d}-{seq:02d}",
            "分析范围": scope,
            "口径版本": caliber["口径版本"],
            "status": "待确认",
            "pending": True,
            "abnormal": False,
            "创建人": operator or "未署名",
            "创建时间": _now(),
            "确认人": "",
            "确认时间": "",
            "演算快照": "",
            "候选": buckets,
            "汇总": summary,
            "口径内容": caliber,
        }
        rows.append(trial)
        return trial, []

    def _evaluate_table(
        self,
        record: dict[str, Any],
        caliber: dict[str, Any],
        tier_price: dict[str, float],
        tier_level: dict[str, float],
        today: date,
    ) -> tuple[dict[str, Any] | None, float, float]:
        """对单表执行双条件投票与裁决；返回（候选明细, 表容量, 冷分区容量）。"""
        heat = caliber["热度口径"]
        cost = caliber["成本口径"]
        stats = {"热": 0, "温": 0, "冷": 0}
        hot_gb = 0.0
        cold_gb = 0.0
        for part in record.get("分区明细") or []:
            cls = self._classify_partition(part, heat, today)
            stats[cls] += 1
            if cls == "热":
                hot_gb += float(part.get("容量GB") or 0)
            elif cls == "冷":
                cold_gb += float(part.get("容量GB") or 0)
        total = float(record.get("总容量GB") or 0)
        cold_ratio = cold_gb / total * 100 if total else 0.0

        heat_vote = HEAT_TO_TIER.get(str(record.get("热度等级")), str(record.get("当前层")))
        size_vote: str | None = None
        if total >= float(cost["容量条件冷层阈值GB"]) and cold_ratio >= float(cost["容量条件冷占比阈值"]):
            size_vote = "冷层"
        elif total >= float(cost["容量条件温层阈值GB"]) and cold_ratio >= float(cost["容量条件温占比阈值"]):
            size_vote = "温层"

        conditions = sorted(caliber["分层条件"], key=lambda item: int(item["管理优先级"]))
        votes = {"RULE-HEAT": heat_vote, "RULE-SIZE": size_vote}
        names = {item["条件编号"]: item["条件名称"] for item in conditions}
        priority = {item["条件编号"]: item["管理优先级"] for item in conditions}
        current = str(record.get("当前层"))
        conflict = size_vote is not None and size_vote != heat_vote
        guardrail = False

        if not conflict:
            target = heat_vote
            if size_vote is None:
                basis = f"容量条件未投票（未达容量阈值），按热度条件（P{priority['RULE-HEAT']}）→{target}"
            else:
                basis = f"热度条件与容量条件一致指向{target}，无冲突"
        else:
            winner = conditions[0]
            target = votes[winner["条件编号"]] or heat_vote
            basis = (
                f"{names['RULE-HEAT']}（P{priority['RULE-HEAT']}）建议{heat_vote}，"
                f"{names['RULE-SIZE']}（P{priority['RULE-SIZE']}）建议{size_vote}；"
                f"按管理优先级采纳{winner['条件名称']}→{target}"
            )
            if target == "冷层" and stats["热"] > 0 and heat_vote != "冷层":
                # 热点分区保护：不得仅因总量大被判为冷数据
                target = heat_vote
                guardrail = True
                basis += f"；热点分区保护：存在 {stats['热']} 个热分区，不得仅因总量大判为冷数据，回退采纳热度条件→{target}"

        if not conflict and target == current:
            return None, total, cold_gb

        if conflict and target == current and stats["热"] > 0 and TIER_ORDER.index(size_vote) > TIER_ORDER.index(target):
            bucket = "热点保留"
            basis = (
                f"{names['RULE-SIZE']}（P{priority['RULE-SIZE']}）建议{size_vote}，"
                f"但存在 {stats['热']} 个热点分区（近{heat['热分区近访天数']}天有访问）；"
                f"按管理优先级采纳{names['RULE-HEAT']}（P{priority['RULE-HEAT']}）保持{current}："
                "热点分区不得仅因总量大被判为冷数据"
            )
        elif conflict:
            bucket = "冲突裁决"
        else:
            bucket = "迁移候选"

        migrating = target != current
        release = total if migrating else 0.0
        save = round(release * (tier_price.get(current, 0) - tier_price.get(target, 0)), 1) if migrating else 0.0
        migrate_cost = round(release * float(cost["迁移单价元每GB"]), 1) if migrating else 0.0
        period = float(cost["试算周期月"])
        net = round(save * period - migrate_cost, 1)
        payback: float | None = round(migrate_cost / save, 1) if save > 0 else None

        risks: list[str] = []
        if migrating:
            if stats["热"] > 0:
                risks.append(f"含 {stats['热']} 个热分区（{hot_gb:.0f} GB），建议按分区拆分迁移")
            queries = int(record.get("查询次数") or 0)
            if queries > 0:
                risks.append(f"近30天仍有 {queries} 次查询，迁移后可能回源{current}")
            hours = release / float(cost["迁移带宽GB每小时"]) if release else 0.0
            if hours > float(cost["维护窗口小时"]):
                risks.append(f"预计迁移窗口 {hours:.1f}h 超出 {cost['维护窗口小时']}h 维护窗口")
            level = tier_level.get(target, 0)
            if level >= float(cost["水位阈值百分比"]):
                risks.append(f"目标{target}容量水位 {level:.0f}% 已达 {cost['水位阈值百分比']}% 阈值")
            if payback is not None and payback > period:
                risks.append(f"回本周期 {payback} 个月超过试算周期 {int(period)} 个月")
            if not risks:
                risks.append("未发现明显风险")
        else:
            queries = int(record.get("查询次数") or 0)
            risks.append(f"若误判迁冷，热点查询将回源，影响约 {queries} 次/30天")

        candidate = {
            "表名称": record.get("表名称"),
            "业务域": record.get("业务域"),
            "记录编号": record.get("记录编号"),
            "采样批次": record.get("采样批次"),
            "候选类型": bucket,
            "当前层": current,
            "建议目标层": target,
            "总容量GB": total,
            "预计释放容量GB": round(release, 1),
            "预计节省元每月": save,
            "迁移成本元": migrate_cost,
            "回本周期月": payback,
            "试算周期净节省元": net,
            "分区统计": stats,
            "冷分区占比": f"{cold_ratio:.1f}%",
            "冲突条件": [f"{names['RULE-HEAT']}→{heat_vote}", f"{names['RULE-SIZE']}→{size_vote}"] if conflict else [],
            "裁决依据": basis,
            "热点保护命中": guardrail or bucket == "热点保留",
            "风险明细": risks,
        }
        return candidate, total, cold_gb

    @staticmethod
    def _classify_partition(part: dict[str, Any], heat: dict[str, Any], today: date) -> str:
        queries = float(part.get("近30天查询") or 0)
        idle_days = _days_between(str(part.get("最近访问") or ""), today)
        if queries >= float(heat["热分区查询阈值"]) or idle_days <= int(heat["热分区近访天数"]):
            return "热"
        if queries < float(heat["冷分区查询阈值"]) and idle_days > int(heat["冷分区未访天数"]):
            return "冷"
        return "温"

    # ---------- 确认与快照 ----------
    def confirm_trial(self, trial_id: int, operator: str) -> tuple[dict[str, Any] | None, str]:
        """确认试算版本：锁内检查-置位，同一版本只放行一次，产出唯一演算快照。"""
        operator = (operator or "").strip() or "未署名"
        with CONFIRM_LOCK:
            trial = store.find(MODULE_TRIAL, trial_id)
            if trial is None:
                return None, f"成本试算版本 {trial_id} 不存在"
            if trial.get("status") != "待确认" or trial.get("演算快照"):
                return None, (
                    f"试算版本 {trial['试算版本']} 已由 {trial.get('确认人')} 于 {trial.get('确认时间')} 确认"
                    f"（演算快照 {trial.get('演算快照')}），本次确认未生效，未生成新方案"
                )
            now = _now()
            snapshots = store.rows(MODULE_SNAPSHOT)
            snap_code = _next_code(snapshots, "快照编号", "SNAP")
            reports = store.rows(MODULE_REPORT)
            report_code = _next_code(reports, "报告编号", "COST")
            summary = trial["汇总"]
            buckets = trial["候选"]
            migrating = [item for item in buckets["迁移候选"] + buckets["冲突裁决"] if item["建议目标层"] != item["当前层"]]
            suggestion_parts = []
            if migrating:
                top = "、".join(f"{item['表名称']}→{item['建议目标层']}" for item in migrating[:3])
                suggestion_parts.append(f"迁移{len(migrating)}表：{top}{'等' if len(migrating) > 3 else ''}")
            if buckets["热点保留"]:
                suggestion_parts.append(f"热点保留{len(buckets['热点保留'])}表（热点分区不得仅因总量大判冷）")
            suggestion_parts.append(f"依据演算快照 {snap_code}（口径 {trial['口径版本']}）")
            report = {
                "id": max((int(row.get("id", 0)) for row in reports), default=0) + 1,
                "报告编号": report_code,
                "分析范围": trial["分析范围"],
                "存储总量": f"{summary['评估总容量GB']:.0f} GB",
                "冷数据占比": summary["冷数据占比"],
                "优化建议": "；".join(suggestion_parts),
                "预计节省": f"{summary['预计节省元每月']:.1f} 元/月",
                "执行状态": "待执行",
                "生成时间": now.split(" ")[0],
                "口径版本": trial["口径版本"],
                "演算快照": snap_code,
                "status": "已生成",
                "pending": True,
                "abnormal": False,
            }
            snapshot = {
                "id": max((int(row.get("id", 0)) for row in snapshots), default=0) + 1,
                "快照编号": snap_code,
                "试算版本": trial["试算版本"],
                "口径版本": trial["口径版本"],
                "分析范围": trial["分析范围"],
                "报告编号": report_code,
                "确认人": operator,
                "确认时间": now,
                "候选汇总": copy.deepcopy(summary),
                "口径内容": copy.deepcopy(trial["口径内容"]),
                "status": "已确认",
                "pending": False,
                "abnormal": False,
            }
            # 结果同步到分层定义清单：目标层与被保留层都登记成本报告引用
            referenced_tiers = {item["建议目标层"] for item in migrating}
            referenced_tiers.update(item["当前层"] for item in buckets["热点保留"])
            for tier in store.rows(MODULE_TIER):
                if tier.get("分层名称") in referenced_tiers:
                    refs = tier.setdefault("成本报告引用", [])
                    ref = f"{report_code}({snap_code})"
                    if ref not in refs:
                        refs.append(ref)
            # 热度记录登记快照引用，采样批次可追溯到本次演算
            evaluated = {item["表名称"] for bucket in buckets.values() for item in bucket}
            for record in store.rows(MODULE_HEATMAP):
                if record.get("表名称") in evaluated or trial["分析范围"] in ("全域", record.get("业务域")):
                    refs = record.setdefault("试算引用", [])
                    if snap_code not in refs:
                        refs.append(snap_code)
            trial["status"] = "已确认"
            trial["pending"] = False
            trial["确认人"] = operator
            trial["确认时间"] = now
            trial["演算快照"] = snap_code
            reports.append(report)
            snapshots.append(snapshot)
        return snapshot, f"试算版本 {trial['试算版本']} 已确认：演算快照 {snap_code} 生效，报告 {report_code} 已生成并同步分层定义与热度记录"

    def list_snapshots(self) -> list[dict[str, Any]]:
        return list(reversed(store.rows(MODULE_SNAPSHOT)))
