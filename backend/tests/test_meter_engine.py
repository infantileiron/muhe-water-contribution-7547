import math
import pytest

from src.services.meter_engine import build, MeterValidationError


def readings(zone, points):
    return [{"zone": zone, "read_at": t, "cumulative": v} for t, v in points]


# 每月一条人日
def person_days(zone, periods, scale=1000.0):
    return [{"zone": zone, "period": p, "scale": scale} for p in periods]


def test_basic_month_boundary_readings_allocate_full_delta():
    # 读数恰好落在月界，整月水量 = 相邻累计差
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-10-01T00:00:00Z", 169.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    out = build(r, d)
    water = {(m["zone"], m["period"]): m["water"] for m in out["monthly"]}
    assert water[("A", "2026-08")] == pytest.approx(30.0)
    assert water[("A", "2026-09")] == pytest.approx(39.0)
    # 对账：分摊合计 == 累计读数差
    rec = out["reconciliation"]
    assert rec["meter_total"] == pytest.approx(69.0)
    assert rec["monthly_total"] == pytest.approx(69.0)
    assert abs(rec["residual"]) < 1e-9


def test_mid_month_reading_split_by_elapsed_time():
    # 月中抄表：8/1→9/15 跨 8、9 两月，按时长比例切
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 0.0 + 100.0),
        ("2026-09-16T00:00:00Z", 100.0 + 46.0),
        ("2026-10-01T00:00:00Z", 146.0 + 15.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    out = build(r, d)
    seg = out["segments"][0]
    total = seg["span_seconds"]
    shares = {a["period"]: a["time_share"] for a in seg["allocations"]}
    assert math.isclose(sum(shares.values()), 1.0, rel_tol=1e-12)
    # 8 月占整段 31/46 天，9 月占 15/46 天
    assert shares["2026-08"] == pytest.approx(31 / 46, rel=1e-9)
    assert shares["2026-09"] == pytest.approx(15 / 46, rel=1e-9)
    waters = {a["period"]: a["water"] for a in seg["allocations"]}
    assert math.fsum(waters.values()) == pytest.approx(46.0)
    # 全局仍闭合
    assert abs(out["reconciliation"]["residual"]) < 1e-9


def test_out_of_order_supplement_points_rebuild_affected_range():
    # 乱序提交的读数，引擎排序后重切受影响区间，结果与有序一致
    ordered = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-09-16T00:00:00Z", 145.0),
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    a = build(ordered, d)
    b = build(shuffled, d)
    wa = {(m["period"]): m["water"] for m in a["monthly"]}
    wb = {(m["period"]): m["water"] for m in b["monthly"]}
    assert wa == pytest.approx(wb)
    # 补点后 9 月由两段拼成，覆盖原整月区间
    sep_segments = [s for s in b["segments"]
                    if any(x["period"] == "2026-09" for x in s["allocations"])]
    assert len(sep_segments) == 2


def test_same_instant_conflict_rejects_whole_batch():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-09-01T00:00:00Z", 131.0),  # 同刻不同值
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    codes = {e["code"] for e in ei.value.errors}
    assert "same_instant_conflict" in codes
    assert ei.value.errors[0]["zone"] == "A"


def test_reading_regression_rejects_with_location():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 90.0),   # 倒退
        ("2026-10-01T00:00:00Z", 120.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "reading_regression" and e["zone"] == "A" for e in ei.value.errors)


def test_month_without_bracketing_reading_rejected():
    # 第一条读数在 8 月中，8 月缺前包围读数；且区间不到 9 月底，9 月缺后包围
    r = readings("A", [
        ("2026-08-15T00:00:00Z", 100.0),
        ("2026-09-15T00:00:00Z", 130.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    codes = {e["code"] for e in ei.value.errors}
    assert "month_unbracketed" in codes
    periods = {e["period"] for e in ei.value.errors if e["code"] == "month_unbracketed"}
    assert {"2026-08", "2026-09"} <= periods


def test_person_day_mismatch_rejected():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    # 人日只给 8 月，9 月分摊无人日
    d = person_days("A", ["2026-08"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "allocation_without_person_day" and e["period"] == "2026-09"
               for e in ei.value.errors)


def test_zone_set_mismatch_rejected():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
    ])
    d = person_days("B", ["2026-08"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    codes = {e["code"] for e in ei.value.errors}
    assert "zone_without_person_day" in codes
    assert "zone_without_reading" in codes


def test_multi_zone_each_bracketed_and_reconciled():
    pts = [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-10-01T00:00:00Z", 170.0),
    ]
    r = readings("A", pts) + readings("B", [(t, v * 2) for t, v in pts])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0) + \
        person_days("B", ["2026-08", "2026-09"], 2000.0)
    out = build(r, d)
    assert out["zones"] == ["A", "B"]
    assert len(out["monthly"]) == 4
    for zrow in out["reconciliation"]["by_zone"]:
        assert abs(zrow["residual"]) < 1e-9
    # scale 直接取人日，不做时间分摊
    a_aug = next(m for m in out["monthly"] if m["zone"] == "A" and m["period"] == "2026-08")
    assert a_aug["scale"] == 1000.0


def test_exact_duplicate_reading_rejected():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-09-01T00:00:00Z", 130.0),  # 完全重复（同刻同值）
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "same_instant_conflict" for e in ei.value.errors)


def test_duplicate_person_day_rejected():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0) + [
        {"zone": "A", "period": "2026-08", "scale": 1100.0}]
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "duplicate_person_day" and e["period"] == "2026-08"
               for e in ei.value.errors)


def test_multiple_errors_all_located_in_one_rejection():
    # 一次返回全部问题：倒退 + 另一分区月界缺包围，而不是只报第一条
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 90.0),   # 倒退
        ("2026-10-01T00:00:00Z", 120.0),
    ]) + readings("B", [
        ("2026-08-10T00:00:00Z", 50.0),   # 8 月缺前包围
        ("2026-09-01T00:00:00Z", 70.0),
        ("2026-10-01T00:00:00Z", 90.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0) + \
        person_days("B", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    codes = {e["code"] for e in ei.value.errors}
    assert "reading_regression" in codes
    assert "month_unbracketed" in codes
    assert any(e.get("zone") == "B" for e in ei.value.errors)


def test_continuous_long_segment_spanning_months_is_accepted():
    # 一个相邻读数区间连续覆盖 9、10 两月：每月都被读数链完整包围，合法且按时长切
    r = readings("A", [
        ("2026-09-01T00:00:00Z", 130.0),
        ("2026-11-01T00:00:00Z", 200.0),
    ])
    d = person_days("A", ["2026-09", "2026-10"], 1000.0)
    out = build(r, d)
    water = {m["period"]: m["water"] for m in out["monthly"]}
    assert math.isclose(water["2026-09"] + water["2026-10"], 70.0, rel_tol=1e-12)
    # 9 月 30 天、10 月 31 天，按各自时长占比
    assert water["2026-09"] == pytest.approx(70.0 * 30 / 61, rel=1e-9)
    assert abs(out["reconciliation"]["residual"]) < 1e-9


def test_late_first_reading_missing_front_bracket():
    r = readings("A", [
        ("2026-08-15T00:00:00Z", 100.0),
        ("2026-10-01T00:00:00Z", 160.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "month_unbracketed" and e["period"] == "2026-08" for e in ei.value.errors)


def test_early_last_reading_missing_back_bracket():
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-20T00:00:00Z", 160.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "month_unbracketed" and e["period"] == "2026-09" for e in ei.value.errors)


def test_naive_timestamp_rejected():
    r = [
        {"zone": "A", "read_at": "2026-08-01T00:00:00", "cumulative": 100.0},
        {"zone": "A", "read_at": "2026-09-01T00:00:00", "cumulative": 130.0},
    ]
    d = person_days("A", ["2026-08"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "naive_time" for e in ei.value.errors)


def test_constant_cumulative_gives_nonpositive_month_rejected():
    # 累计量持平 → 某区间 delta=0，分摊水量为 0，违反严格正值定义域
    r = readings("A", [
        ("2026-08-01T00:00:00Z", 100.0),
        ("2026-09-01T00:00:00Z", 100.0),
        ("2026-10-01T00:00:00Z", 120.0),
    ])
    d = person_days("A", ["2026-08", "2026-09"], 1000.0)
    with pytest.raises(MeterValidationError) as ei:
        build(r, d)
    assert any(e["code"] == "nonpositive_monthly_water" and e["period"] == "2026-08"
               for e in ei.value.errors)
