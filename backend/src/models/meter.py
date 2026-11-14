from sqlalchemy import String, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MeterBatch(Base):
    """一次抄表批次导入。整批校验通过才落库；失败不保留任何部分数据。"""

    __tablename__ = "meter_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    source: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="accepted")
    readings: Mapped[list] = mapped_column(JSON)
    person_days: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String)


class MeterObservationSet(Base):
    """一个批次派生出的不可变月度观测集。补点永远另建新集，旧集不被改写。"""

    __tablename__ = "meter_observation_sets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("meter_batches.id"), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="accepted")
    monthly: Mapped[list] = mapped_column(JSON)
    segments: Mapped[list] = mapped_column(JSON)
    reconciliation: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("consumption_datasets.id", use_alter=True)
    )
    created_at: Mapped[str] = mapped_column(String)
