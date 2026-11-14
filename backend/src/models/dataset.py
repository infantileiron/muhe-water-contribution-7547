from sqlalchemy import String, Float, Integer, JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class ConsumptionDataset(Base):
    __tablename__ = "consumption_datasets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    source: Mapped[str] = mapped_column(String)
    rows: Mapped[list] = mapped_column(JSON)
    # manual=原 JSON 表单导入；meter=抄表批次按读数时长分摊生成
    origin: Mapped[str] = mapped_column(String, default="manual")
    meter_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("meter_observation_sets.id", use_alter=True), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String)
