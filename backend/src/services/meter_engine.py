"""抄表累计量 → 月度观测的分摊与整批校验引擎。

口径（恒等式，不引入估计参数）：
- 每个分区相邻两次累计读数 (r0@t0, r1@t1) 的水量 Δr=r1-r0，按相邻读数间时长
  线性分摊到与区间 [t0,t1) 相交的各日历月：月水量 = Δr × 月内时长 / 区间总时长。
- 月度人日（有效作业人日）直接作为该月该分区的规模 scale，不做时间分摊。
- 因此“直接按月相减”被替换为按读数包围区间的时长分摊，跨月读数不再错位。

整批校验：同刻冲突、读数倒退、月界缺包围读数、人日缺漏/越界、分区集合不一致等
问题一次性定位（带分区、读数时刻、月份），任意一条命中即整批拒绝，不落任何数据。
"""

import math
from datetime import datetime, timezone


class MeterValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("；".join(e["message"] for e in errors))


def parse_time(value):
    """解析 ISO-8601 时刻并统一为 UTC 的 aware datetime；非法时刻抛错。"""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise MeterValidationError(
                [{"code": "bad_time", "ref": str(value), "message": f"无法识别的抄表时刻：{value}"}]
            )
    if dt.tzinfo is None:
        raise MeterValidationError(
            [{"code": "naive_time", "ref": str(value), "message": f"抄表时刻缺少时区信息：{value}"}]
        )
    return dt.astimezone(timezone.utc)


def month_key(dt):
    return f"{dt.year:04d}-{dt.month:02d}"


def month_start(key):
    year, month = (int(x) for x in key.split("-"))
    return datetime(year, month, 1, tzinfo=timezone.utc)


def next_month_key(key):
    year, month = (int(x) for x in key.split("-"))
    month += 1
    if month == 13:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


def _month_length(key):
    return month_start(next_month_key(key)) - month_start(key)


def iter_months(start_key, end_key):
    """闭区间枚举从 start_key 到 end_key 的全部月份键。"""
    key = start_key
    guard = 0
    while key <= end_key and guard < 100000:
        yield key
        key = next_month_key(key)
        guard += 1


def _err(errors, code, message, zone=None, ref=None, period=None):
    errors.append(
        {"code": code, "message": message, "zone": zone, "ref": ref, "period": period}
    )


def build(readings, person_days):
    """返回 {monthly, segments, reconciliation, covered_periods, zones, checksum_input}。

    readings: [{zone, read_at, cumulative}]，read_at 为 ISO 字符串或 datetime
    person_days: [{zone, period, scale}]
    任何口径问题抛 MeterValidationError（内含全部定位信息）。
    """
    errors = []

    # ---- 1. 解析、分组人日 -------------------------------------------------
    day_map = {}
    day_zones = set()
    day_periods = set()
    for idx, d in enumerate(person_days):
        zone = d["zone"]
        period = d["period"]
        scale = d["scale"]
        if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            _err(errors, "bad_person_day", f"{zone} {period} 人日必须为有限正值", zone, f"person_days[{idx}]", period)
            continue
        key = (zone, period)
        if key in day_map:
            _err(errors, "duplicate_person_day", f"{zone} 在 {period} 的人日重复", zone, f"person_days[{idx}]", period)
            continue
        day_map[key] = float(scale)
        day_zones.add(zone)
        day_periods.add(period)

    # ---- 2. 解析、分组读数 -------------------------------------------------
    grouped = {}
    reading_zones = set()
    parsed = []
    for idx, r in enumerate(readings):
        zone = r["zone"]
        try:
            when = parse_time(r["read_at"])
        except MeterValidationError as e:
            for item in e.errors:
                item["zone"] = zone
                item["ref"] = f"readings[{idx}]"
            errors.extend(e.errors)
            continue
        value = r["cumulative"]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            _err(errors, "bad_cumulative", f"{zone} 在 {when.isoformat()} 的累计读数必须为有限正值",
                 zone, f"readings[{idx}]", month_key(when))
            continue
        parsed.append((zone, when, float(value), idx))
        grouped.setdefault(zone, []).append((when, float(value), idx))
        reading_zones.add(zone)

    if errors:
        raise MeterValidationError(errors)

    # ---- 3. 分区集合一致性 -------------------------------------------------
    if reading_zones != day_zones:
        only_meter = sorted(reading_zones - day_zones)
        only_day = sorted(day_zones - reading_zones)
        if only_meter:
            _err(errors, "zone_without_person_day", f"分区 {', '.join(only_meter)} 有抄表读数但缺月度人日",
                 ref="zones")
        if only_day:
            _err(errors, "zone_without_reading", f"分区 {', '.join(only_day)} 有人日但缺抄表读数",
                 ref="zones")
        raise MeterValidationError(errors)

    segments = []
    monthly_acc = {}          # (zone, period) -> 分摊水量累加器
    covered_periods_by_zone = {}
    first_period_overall = None
    last_period_overall = None

    # ---- 4. 逐分区排序、同刻/倒退校验、月界包围校验、时长分摊 --------------
    for zone in sorted(reading_zones):
        items = sorted(grouped[zone], key=lambda t: (t[0], t[2]))
        if len(items) < 2:
            _err(errors, "single_reading", f"分区 {zone} 只有一条读数，无法构成相邻读数区间", zone)
            continue

        seen_times = {}
        ordered = []
        for when, value, idx in items:
            at = when.isoformat()
            if when in seen_times:
                _err(errors, "same_instant_conflict",
                     f"分区 {zone} 在 {at} 存在两条不同累计读数（同刻冲突）",
                     zone, f"readings[{idx}]", month_key(when))
                continue
            seen_times[when] = value
            ordered.append((when, value, idx))

        for j in range(1, len(ordered)):
            t0, v0, i0 = ordered[j - 1]
            t1, v1, i1 = ordered[j]
            if v1 < v0:
                _err(errors, "reading_regression",
                     f"分区 {zone} 读数倒退：{t0.isoformat()}={v0:g} → {t1.isoformat()}={v1:g}",
                     zone, f"readings[{i1}]", month_key(t1))

        if any(e["zone"] == zone and e["code"] in ("same_instant_conflict", "reading_regression")
               for e in errors):
            continue

        zone_periods = sorted(p for (z, p) in day_map if z == zone)
        covered = set()

        for j in range(1, len(ordered)):
            t0, v0, _ = ordered[j - 1]
            t1, v1, _ = ordered[j]
            delta = v1 - v0
            total_seconds = (t1 - t0).total_seconds()
            if total_seconds <= 0:
                _err(errors, "zero_span", f"分区 {zone} 相邻读数 {t0.isoformat()} 时长为零", zone)
                continue

            # 月份采用半开区间 [月初, 下月初)：落在月界的读数只属于前月终点，
            # 不会对下月产生零时长切片。
            start_key, end_key = month_key(t0), month_key(t1)
            seg_months = list(iter_months(start_key, end_key))

            pieces = []
            for period in seg_months:
                ms = month_start(period)
                me = ms + _month_length(period)
                lo = t0 if t0 > ms else ms
                hi = t1 if t1 < me else me
                part_seconds = (hi - lo).total_seconds()
                if part_seconds <= 0:
                    # 月界读数在相邻月的零时长重叠，跳过且不视为缺包围。
                    continue

                covered.add(period)

                share = part_seconds / total_seconds
                water = delta * share
                monthly_acc[(zone, period)] = monthly_acc.get((zone, period), 0.0) + water
                pieces.append({
                    "period": period,
                    "seconds": part_seconds,
                    "time_share": share,
                    "water": water,
                })

            segments.append({
                "zone": zone,
                "reading_from": {"read_at": t0.isoformat(), "cumulative": v0},
                "reading_to": {"read_at": t1.isoformat(), "cumulative": v1},
                "delta": delta,
                "span_seconds": total_seconds,
                "allocations": pieces,
            })

        # 月界包围（按整条连续读数链判定，允许月中补点把区间切成多段）：
        # 每个有人日的月份都必须被读数链从月初完整覆盖到下月初，否则定位缺侧。
        chain_first, chain_last = ordered[0][0], ordered[-1][0]
        for period in zone_periods:
            ms = month_start(period)
            me = ms + _month_length(period)
            if chain_first > ms:
                _err(errors, "month_unbracketed",
                     f"分区 {zone} 的 {period} 月缺少前包围读数（首条读数 {chain_first.date()} 晚于月初）",
                     zone, period=period)
            if chain_last < me:
                _err(errors, "month_unbracketed",
                     f"分区 {zone} 的 {period} 月缺少后包围读数（末条读数 {chain_last.date()} 早于月末）",
                     zone, period=period)

        covered_periods_by_zone[zone] = covered
        if zone_periods:
            z_first, z_last = zone_periods[0], zone_periods[-1]
            first_period_overall = z_first if first_period_overall is None else min(first_period_overall, z_first)
            last_period_overall = z_last if last_period_overall is None else max(last_period_overall, z_last)

    if errors:
        # 去重（同刻等可能被多处记录）
        unique = []
        seen = set()
        for e in errors:
            sig = (e["code"], e.get("zone"), e.get("period"), e["ref"], e["message"])
            if sig not in seen:
                seen.add(sig)
                unique.append(e)
        raise MeterValidationError(unique)

    # ---- 5. 人日与分摊月份对齐 ---------------------------------------------
    for zone in sorted(reading_zones):
        zone_day_periods = {p for (z, p) in day_map if z == zone}
        covered = covered_periods_by_zone.get(zone, set())
        missing = sorted(zone_day_periods - covered)
        extra = sorted(covered - zone_day_periods)
        for period in missing:
            _err(errors, "month_unbracketed",
                 f"分区 {zone} 的 {period} 月有人日，但读数链存在缺口、未形成包围读数", zone, period=period)
        for period in extra:
            _err(errors, "allocation_without_person_day",
                 f"分区 {zone} 的 {period} 产生了分摊水量却缺月度人日", zone, period=period)
    if errors:
        raise MeterValidationError(errors)

    # ---- 6. 组装月度观测（scale 用人日，water 用 fsum 分摊）----------------
    monthly = []
    for zone in sorted(reading_zones):
        for period in sorted(p for (z, p) in monthly_acc if z == zone):
            water = monthly_acc[(zone, period)]
            if not math.isfinite(water) or water <= 0:
                _err(errors, "nonpositive_monthly_water",
                     f"分区 {zone} 的 {period} 分摊水量非有限正值（{water:g}），不能进入分解定义域",
                     zone, period=period)
                continue
            monthly.append({
                "zone": zone,
                "period": period,
                "scale": day_map[(zone, period)],
                "water": water,
            })
    if errors:
        raise MeterValidationError(errors)

    # ---- 7. 对账：分摊还原累计差 vs 月度合计 --------------------------------
    reconciliation = _reconcile(grouped, monthly, sorted(reading_zones))
    return {
        "monthly": monthly,
        "segments": segments,
        "reconciliation": reconciliation,
        "covered_periods": sorted(day_periods),
        "zones": sorted(reading_zones),
        "first_period": first_period_overall,
        "last_period": last_period_overall,
    }


def _reconcile(grouped, monthly, zones):
    zone_rows = []
    all_meter_total = 0.0
    all_month_total = 0.0
    for zone in zones:
        items = sorted(grouped[zone], key=lambda t: t[0])
        meter_total = math.fsum(v1 - v0 for ((_, v0, _), (_, v1, _)) in zip(items, items[1:]))
        month_total = math.fsum(r["water"] for r in monthly if r["zone"] == zone)
        residual = month_total - meter_total
        all_meter_total += meter_total
        all_month_total += month_total
        zone_rows.append({
            "zone": zone,
            "meter_total": meter_total,
            "monthly_total": month_total,
            "residual": residual,
        })
    return {
        "meter_total": all_meter_total,
        "monthly_total": all_month_total,
        "residual": all_month_total - all_meter_total,
        "by_zone": zone_rows,
        "tolerance": 1e-6,
    }
