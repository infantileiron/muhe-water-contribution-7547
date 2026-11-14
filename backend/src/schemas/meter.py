from pydantic import BaseModel, Field


class MeterReading(BaseModel):
    zone: str = Field(min_length=1, max_length=60)
    read_at: str = Field(min_length=2, max_length=40)
    cumulative: float = Field(gt=0, le=1000000000, allow_inf_nan=False)


class MeterPersonDay(BaseModel):
    zone: str = Field(min_length=1, max_length=60)
    period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    scale: float = Field(gt=0, le=1000000000, allow_inf_nan=False)


class MeterBatchInput(BaseModel):
    # 同刻冲突、重复人日等业务口径统一交由 meter_engine 定位并整批拒绝，
    # 这里只做字段级约束，确保错误结构一致（带分区/月份/索引）。
    idempotency_key: str = Field(min_length=8, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    source: str = Field(default="meter_readings.json", max_length=120)
    readings: list[MeterReading] = Field(min_length=2, max_length=5000)
    person_days: list[MeterPersonDay] = Field(min_length=1, max_length=5000)
