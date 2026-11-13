"""存储成本优化接口：维护成本分析报告，覆盖开始分析、执行优化、归档报告等动作。

同时承载冷数据成本试算：口径维护、试算版本生成与确认、演算快照查询。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cost import CostService
from app.services.cost_trial import CostTrialService

router = APIRouter(prefix="/api/cost", tags=["存储成本优化"])

service = CostService()
trial_service = CostTrialService()

LIST_FIELDS = ["报告编号", "分析范围", "存储总量", "冷数据占比", "优化建议", "预计节省", "执行状态", "生成时间"]
STATUSES = ["草稿", "分析中", "已生成", "已执行"]


@router.get("/caliber")
def get_caliber() -> dict[str, Any]:
    """读取当前生效的试算口径（热度口径、成本口径、分层条件与优先级）。"""
    return trial_service.current_caliber()


@router.post("/caliber", response_model=ActionResult)
def update_caliber(payload: EntryPayload) -> ActionResult:
    """更新试算口径并生成新口径版本；历史报告仍按原采样口径保留，新试算按新口径核算。"""
    operator = str(payload.values.get("操作人") or "").strip()
    caliber, errors = trial_service.update_caliber(payload.values, operator)
    if errors:
        return ActionResult(ok=False, message=f"口径更新未生效：{'；'.join(errors)}")
    return ActionResult(ok=True, message=f"试算口径已更新为新版本 {caliber['口径版本']}，历史报告按原口径保留", entry=caliber)


@router.get("/scopes")
def list_scopes() -> dict[str, Any]:
    """试算可选的分析范围：全域加上热度记录里出现过的业务域。"""
    return {"scopes": trial_service.list_scopes()}


@router.get("/trials")
def list_trials() -> dict[str, Any]:
    """试算版本列表：版本号、口径版本、确认状态与演算快照引用。"""
    items = trial_service.list_trials()
    return {"items": items, "total": len(items)}


@router.post("/trials", response_model=ActionResult)
def create_trial(payload: EntryPayload) -> ActionResult:
    """按分析范围发起一次冷数据成本试算，关联历史热度、分区访问与分层策略。"""
    scope = str(payload.values.get("分析范围") or "").strip()
    operator = str(payload.values.get("操作人") or "").strip()
    trial, errors = trial_service.create_trial(scope, operator)
    if errors:
        return ActionResult(ok=False, message=f"试算未生成：{'；'.join(errors)}")
    return ActionResult(ok=True, message=f"试算版本 {trial['试算版本']} 已生成（口径 {trial['口径版本']}）", entry=trial)


@router.get("/trials/{trial_id}")
def get_trial(trial_id: int) -> dict[str, Any]:
    """读取单个试算版本明细：三类候选、目标层、预计节省与风险项。"""
    trial = trial_service.get_trial(trial_id)
    if trial is None:
        raise HTTPException(status_code=404, detail=f"成本试算版本 {trial_id} 不存在")
    return trial


@router.post("/trials/{trial_id}/confirm", response_model=ActionResult)
def confirm_trial(trial_id: int, payload: EntryPayload) -> ActionResult:
    """确认试算版本：同一版本只允许一次成功确认，重复或并发确认不会产生第二份方案。"""
    operator = str(payload.values.get("操作人") or "").strip()
    snapshot, message = trial_service.confirm_trial(trial_id, operator)
    if snapshot is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=snapshot)


@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    """演算快照清单：每次确认试算版本时固化的口径与候选汇总。"""
    items = trial_service.list_snapshots()
    return {"items": items, "total": len(items)}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已生成、已执行"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报告编号与状态过滤存储成本优化列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出存储成本优化清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "cost", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条成本分析报告明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"成本分析报告 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条成本分析报告，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="成本分析报告已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条成本分析报告执行开始分析、执行优化、归档报告；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
