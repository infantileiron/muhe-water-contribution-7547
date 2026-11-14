"""端到端：抄表批次 → 分摊观测集 → 双方法分解；补点另建新集、旧对照不被改写。"""
import pytest
from fastapi.testclient import TestClient

from src.services import meter as meter_service, comparisons as comparison_service
from src.services.analysis import analyze
from src.schemas.comparison import ComparisonInput
from src.models import PeriodComparison, ConsumptionDataset
from tests.conftest import valid_payload, _Row


def test_supplement_keeps_existing_comparison_on_old_set(session):
    # 1. 首批抄表 → 观测集 v1 / 数据集 v1
    first, _ = meter_service.create(session, valid_payload(key="batch-1", name="首批"))
    ds_v1 = session.get(ConsumptionDataset, first["dataset_id"])

    # 2. 在 v1 数据集上建立两期对照并完成分解
    comp = comparison_service.create(session, ComparisonInput(
        name="v1 对照", dataset_id=ds_v1.id,
        period_before="2026-08", period_after="2026-09"))
    result_v1 = analyze(ds_v1.rows, "2026-08", "2026-09")
    assert abs(result_v1["shapley"]["residual"]) < 1e-6

    # 3. 乱序补点（9 月中插入读数），新批次 → 观测集 v2 / 数据集 v2
    payload2 = valid_payload(key="batch-2", name="补点批次")
    extra = _Row({"zone": "研发东区", "read_at": "2026-09-16T00:00:00Z", "cumulative": 145.0})
    payload2.readings.append(extra)
    second, reused = meter_service.create(session, payload2)
    assert reused is False
    assert second["id"] != first["id"]
    ds_v2 = session.get(ConsumptionDataset, second["dataset_id"])
    assert ds_v2.id != ds_v1.id

    # 4. 既有对照仍引用旧集，分解结果可复现、未被改写
    stale = session.get(PeriodComparison, comp["id"])
    assert stale.dataset_id == ds_v1.id
    again = analyze(
        session.get(ConsumptionDataset, stale.dataset_id).rows,
        "2026-08", "2026-09")
    assert again["checksum"] == result_v1["checksum"]

    # 5. 新集可独立建立对照，受影响区间（9 月）被重切
    comp2 = comparison_service.create(session, ComparisonInput(
        name="v2 对照", dataset_id=ds_v2.id,
        period_before="2026-08", period_after="2026-09"))
    assert comp2["dataset_id"] == ds_v2.id


def test_viewer_cannot_create_batch(app):
    from src.controllers.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"email": "viewer@muhe.demo", "role": "viewer"}
    client = TestClient(app)
    resp = client.post("/meter/batches", json={})
    assert resp.status_code == 403
    # 只读账号仍可查询
    assert client.get("/meter/sets").status_code == 200


def test_reconciliation_matches_monthly_totals(session):
    summary, _ = meter_service.create(session, valid_payload())
    detail = meter_service.detail(session, summary["id"])
    # 月度对账之和 == 总对账
    month_sum = sum(p["water"] for p in detail["period_reconciliation"])
    assert month_sum == pytest.approx(detail["reconciliation"]["monthly_total"], rel=1e-12)
    # 每条区间的分摊水量之和 == 该区间累计差
    for seg in detail["segments"]:
        assert sum(a["water"] for a in seg["allocations"]) == pytest.approx(seg["delta"], rel=1e-12)
        assert sum(a["time_share"] for a in seg["allocations"]) == pytest.approx(1.0, abs=1e-12)
