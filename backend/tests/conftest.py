import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI

from src.config.database import Base, db_session
from src.models import (  # noqa: F401  确保全部模型注册到 metadata
    User, ConsumptionDataset, MeterBatch, MeterObservationSet,
    PeriodComparison, DecompositionRun, DriverReview, PublishedReport, AuditEvent,
)
from src.controllers.auth import current_user
from src.routes import auth, datasets, meter, comparisons, runs, reviews, reports, audit as audit_route


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def app(session):
    application = FastAPI()
    for module in (auth, datasets, meter, comparisons, runs, reviews, reports, audit_route):
        application.include_router(module.router)
    application.dependency_overrides[current_user] = lambda: {"email": "analyst@muhe.demo", "role": "analyst"}
    application.dependency_overrides[db_session] = lambda: session
    return application


def valid_payload(name="第一批抄表", key=None):
    # 两个分区，读数落在月界，覆盖 8、9 两个月
    base = [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-10-01T00:00:00Z", 169.0),
    ]
    readings = []
    person_days = []
    for zi, zone in enumerate(("研发东区", "试制中心")):
        mult = zi + 1
        for t, v in base:
            readings.append(_Row({"zone": zone, "read_at": t, "cumulative": v * mult}))
        for period in ("2026-08", "2026-09"):
            person_days.append(_Row({"zone": zone, "period": period, "scale": 1000.0 + zi * 500}))
    return _Payload({
        "idempotency_key": key or "key-0001",
        "name": name,
        "source": "meter_demo.json",
        "readings": readings,
        "person_days": person_days,
    })


class _Row:
    def __init__(self, data):
        self.__dict__.update(data)

    def model_dump(self):
        return dict(self.__dict__)


class _Payload:
    def __init__(self, data):
        for key, value in data.items():
            setattr(self, key, value)

    def model_dump(self):
        return {k: getattr(self, k) for k in
                ("idempotency_key", "name", "source", "readings", "person_days")}
