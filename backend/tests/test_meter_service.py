import pytest
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from src.services import meter as meter_service
from src.models import MeterBatch, MeterObservationSet, ConsumptionDataset
from tests.conftest import valid_payload, _Row, _Payload


def test_create_persists_batch_set_and_dataset(session):
    summary, reused = meter_service.create(session, valid_payload())
    assert reused is False
    assert session.query(MeterBatch).count() == 1
    assert session.query(MeterObservationSet).count() == 1
    dataset = session.get(ConsumptionDataset, summary["dataset_id"])
    assert dataset.origin == "meter"
    assert dataset.meter_set_id == summary["id"]
    # 分摊结果可直接进入既有分解链路（每条都是严格正值）
    assert all(r["water"] > 0 and r["scale"] > 0 for r in dataset.rows)
    assert len(dataset.rows) == 4  # 2 分区 × 2 月


def test_idempotent_retry_returns_same_set(session):
    payload = valid_payload()
    first, reused1 = meter_service.create(session, payload)
    second, reused2 = meter_service.create(session, payload)
    assert reused1 is False and reused2 is True
    assert first["id"] == second["id"]
    assert session.query(MeterBatch).count() == 1
    assert session.query(MeterObservationSet).count() == 1


def test_concurrent_retry_race_reuses_existing(session, monkeypatch):
    # 预置一份已存在的同 key 批次
    existing, _ = meter_service.create(session, valid_payload(key="race-key"))
    # 再次提交：让首次 commit 抛唯一键冲突，回退后必须复用既有结果而非新建
    real_commit = session.commit

    def flaky_commit():
        monkeypatch.setattr(session, "commit", real_commit)
        raise IntegrityError("insert", {}, Exception("unique constraint"))

    monkeypatch.setattr(session, "commit", flaky_commit)
    summary, reused = meter_service.create(session, valid_payload(key="race-key"))
    assert reused is True
    assert summary["id"] == existing["id"]


def test_supplement_points_create_new_set_keep_old(session):
    first, _ = meter_service.create(session, valid_payload(key="k1", name="第一批"))
    # 补点：在 9 月中为研发东区插入一条读数，另建新批次/新观测集
    payload2 = valid_payload(key="k2", name="第二批补点")
    payload2.readings.append(
        _Row({"zone": "研发东区", "read_at": "2026-09-16T00:00:00Z", "cumulative": 145.0}))
    second, reused = meter_service.create(session, payload2)
    assert reused is False
    assert first["id"] != second["id"]
    assert session.query(MeterObservationSet).count() == 2
    assert session.query(ConsumptionDataset).count() == 2
    # 旧集仍完整、未被改写
    old_set = session.get(MeterObservationSet, first["id"])
    assert len(old_set.monthly) == 4
    # 新集 9 月由两段拼成（受影响区间被重切）
    new_set = session.get(MeterObservationSet, second["id"])
    sep_segments = [s for s in new_set.segments
                    if s["zone"] == "研发东区"
                    and any(a["period"] == "2026-09" for a in s["allocations"])]
    assert len(sep_segments) == 2


def test_invalid_batch_rolls_back_and_locates_errors(session):
    payload = valid_payload()
    payload.readings.append(
        _Row({"zone": "研发东区", "read_at": "2026-09-01T00:00:00Z", "cumulative": 999.0}))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        meter_service.create(session, payload)
    assert ei.value.status_code == 422
    session.rollback()
    assert session.query(MeterBatch).count() == 0
    assert session.query(MeterObservationSet).count() == 0
    assert session.query(ConsumptionDataset).filter(
        ConsumptionDataset.origin == "meter").count() == 0


def test_detail_exposes_trace_and_reconciliation(session):
    summary, _ = meter_service.create(session, valid_payload())
    detail = meter_service.detail(session, summary["id"])
    assert detail["reconciliation"]["residual"] == pytest.approx(0.0, abs=1e-9)
    # 每条来源区间都带分摊明细，月度对账非空
    assert detail["segments"]
    for seg in detail["segments"]:
        assert seg["allocations"]
        assert all("time_share" in a and "water" in a for a in seg["allocations"])
    assert detail["period_reconciliation"]
    assert {p["period"] for p in detail["period_reconciliation"]} == {"2026-08", "2026-09"}


def test_generated_dataset_feeds_analysis(session):
    # 端到端口径：抄表分摊生成的观测集能被既有双方法分解消费
    from src.services.analysis import analyze
    summary, _ = meter_service.create(session, valid_payload())
    dataset = session.get(ConsumptionDataset, summary["dataset_id"])
    result = analyze(dataset.rows, "2026-08", "2026-09")
    assert result["delta"] == pytest.approx(
        result["water_after"] - result["water_before"])
    assert abs(result["shapley"]["residual"]) < 1e-6
    assert abs(result["lmdi"]["residual"]) < 1e-6


# ---- HTTP 层 ---------------------------------------------------------------

def test_http_create_and_retry(app):
    client = TestClient(app)
    resp = client.post("/meter/batches", json=_as_json(valid_payload()))
    assert resp.status_code == 200, resp.text
    assert resp.json()["reused"] is False
    again = client.post("/meter/batches", json=_as_json(valid_payload()))
    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert again.json()["observation_set"]["id"] == resp.json()["observation_set"]["id"]


def test_http_rejection_returns_located_errors(app):
    client = TestClient(app)
    payload = valid_payload()
    payload.readings[1].cumulative = 10.0  # 造成倒退
    resp = client.post("/meter/batches", json=_as_json(payload))
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["message"] == "抄表批次整批拒绝"
    assert any(e["code"] == "reading_regression" for e in body["errors"])
    assert all("zone" in e for e in body["errors"] if e["code"] == "reading_regression")


def test_http_detail_and_listing(app):
    client = TestClient(app)
    created = client.post("/meter/batches", json=_as_json(valid_payload())).json()["observation_set"]
    detail = client.get(f"/meter/sets/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["segments"]
    listing = client.get("/meter/sets")
    assert listing.status_code == 200 and len(listing.json()) == 1
    batches = client.get("/meter/batches")
    assert batches.json()[0]["reading_count"] == 6


def _as_json(payload):
    return {
        "idempotency_key": payload.idempotency_key,
        "name": payload.name,
        "source": payload.source,
        "readings": [r.model_dump() for r in payload.readings],
        "person_days": [r.model_dump() for r in payload.person_days],
    }
