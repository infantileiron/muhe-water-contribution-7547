import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import MeterBatch, MeterObservationSet, ConsumptionDataset
from src.services import meter_engine
from src.utils.time import now
from src.utils.serialization import serialize, rows


def _checksum(readings, person_days):
    payload = json.dumps(
        {"readings": readings, "person_days": person_days},
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _set_summary(obs_set, batch=None):
    data = serialize(obs_set)
    data["month_count"] = len({m["period"] for m in obs_set.monthly})
    data["zone_count"] = len({m["zone"] for m in obs_set.monthly})
    if batch is not None:
        data["batch_name"] = batch.name
        data["source"] = batch.source
    return data


def create(db, payload):
    # 并发/重试幂等：同 key 只返回既有观测集，绝不生成第二份。
    existing = db.scalar(
        select(MeterBatch).where(MeterBatch.idempotency_key == payload.idempotency_key)
    )
    if existing:
        obs_set = db.scalar(
            select(MeterObservationSet).where(MeterObservationSet.batch_id == existing.id)
        )
        return _set_summary(obs_set, existing), True

    readings = [r.model_dump() for r in payload.readings]
    person_days = [p.model_dump() for p in payload.person_days]

    # 整批校验：任何口径问题一次性抛出，拒绝整批、不落部分数据。
    try:
        built = meter_engine.build(readings, person_days)
    except meter_engine.MeterValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": "抄表批次整批拒绝", "errors": exc.errors})

    if db.scalar(select(MeterBatch).where(MeterBatch.name == payload.name)):
        raise HTTPException(409, "抄表批次名称已存在")

    checksum = _checksum(readings, person_days)
    batch_id = str(uuid.uuid4())
    set_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    dataset_name = f"{payload.name}·抄表分摊月度观测"

    if db.scalar(select(ConsumptionDataset).where(ConsumptionDataset.name == dataset_name)):
        raise HTTPException(409, "派生观测集名称已存在，请调整批次名称")

    batch = MeterBatch(
        id=batch_id,
        idempotency_key=payload.idempotency_key,
        name=payload.name,
        source=payload.source,
        status="accepted",
        readings=readings,
        person_days=person_days,
        created_at=now(),
    )
    obs_set = MeterObservationSet(
        id=set_id,
        batch_id=batch_id,
        version=1,
        status="accepted",
        monthly=built["monthly"],
        segments=built["segments"],
        reconciliation=built["reconciliation"],
        checksum=checksum,
        dataset_id=dataset_id,
        created_at=now(),
    )
    # 接入既有双方法分解链路：分摊结果落成标准 ConsumptionDataset。
    dataset = ConsumptionDataset(
        id=dataset_id,
        name=dataset_name,
        source=payload.source,
        rows=built["monthly"],
        origin="meter",
        meter_set_id=set_id,
        created_at=now(),
    )
    db.add_all([batch, obs_set, dataset])
    try:
        db.commit()
    except IntegrityError:
        # 并发下唯一键（幂等键/名称）冲突：回滚并复用既有结果。
        db.rollback()
        raced = db.scalar(
            select(MeterBatch).where(MeterBatch.idempotency_key == payload.idempotency_key)
        )
        if raced:
            raced_set = db.scalar(
                select(MeterObservationSet).where(MeterObservationSet.batch_id == raced.id)
            )
            return _set_summary(raced_set, raced), True
        raise HTTPException(409, "同名批次或观测集已存在")

    db.refresh(obs_set)
    return _set_summary(obs_set, batch), False


def list_batches(db):
    result = []
    for batch in db.scalars(select(MeterBatch).order_by(MeterBatch.created_at.desc())):
        obs_set = db.scalar(
            select(MeterObservationSet).where(MeterObservationSet.batch_id == batch.id)
        )
        item = serialize(batch)
        item["reading_count"] = len(batch.readings)
        item["person_day_count"] = len(batch.person_days)
        if obs_set:
            item["observation_set"] = _set_summary(obs_set, batch)
        result.append(item)
    return result


def list_sets(db):
    out = []
    for obs_set in db.scalars(select(MeterObservationSet).order_by(MeterObservationSet.created_at.desc())):
        batch = db.get(MeterBatch, obs_set.batch_id)
        out.append(_set_summary(obs_set, batch))
    return out


def _require_set(db, set_id):
    obs_set = db.get(MeterObservationSet, set_id)
    if not obs_set:
        raise HTTPException(404, "抄表观测集不存在")
    return obs_set


def detail(db, set_id):
    obs_set = _require_set(db, set_id)
    batch = db.get(MeterBatch, obs_set.batch_id)
    dataset = db.get(ConsumptionDataset, obs_set.dataset_id)
    monthly_lookup = {(m["zone"], m["period"]): m for m in obs_set.monthly}

    # 来源区间 → 月度分摊明细，页面可逐级追溯。
    trace_segments = []
    for seg in obs_set.segments:
        allocations = []
        for a in seg["allocations"]:
            month_row = monthly_lookup.get((seg["zone"], a["period"]))
            allocations.append({
                **a,
                "scale": month_row["scale"] if month_row else None,
                "monthly_water": month_row["water"] if month_row else None,
            })
        trace_segments.append({**seg, "allocations": allocations})

    # 月度对账：每个月分摊合计与其来源区间。
    period_totals = {}
    for m in obs_set.monthly:
        t = period_totals.setdefault(m["period"], {"period": m["period"], "water": 0.0, "scale": 0.0, "zones": 0})
        t["water"] += m["water"]
        t["scale"] += m["scale"]
        t["zones"] += 1
    period_reconciliation = sorted(period_totals.values(), key=lambda x: x["period"])

    return {
        **_set_summary(obs_set, batch),
        "batch": serialize(batch),
        "dataset_id": obs_set.dataset_id,
        "dataset_name": dataset.name if dataset else None,
        "monthly": obs_set.monthly,
        "segments": trace_segments,
        "reconciliation": obs_set.reconciliation,
        "period_reconciliation": period_reconciliation,
    }
