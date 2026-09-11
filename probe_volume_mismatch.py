# 一次性探针：分类"分钟量与日量对不上"的成因而非修复（用完可删）
import sqlite3
from collections import defaultdict

con = sqlite3.connect("stock_data.db")


def classify(code: str, year: str) -> None:
    bars = con.execute(
        "SELECT substr(Time,1,8), Volume FROM MinuteBars "
        "WHERE Code=? AND Frequency='5' AND Time LIKE ? ORDER BY Time",
        (code, year + "%"),
    ).fetchall()
    daily = {
        trading_day.replace("-", ""): volume
        for trading_day, volume in con.execute(
            "SELECT TradingDay, Volume FROM MinuteBarTradingDays WHERE Code=? AND TradingDay LIKE ?",
            (code, year + "%"),
        )
    }
    days: dict[str, list[float]] = defaultdict(list)
    for day, volume in bars:
        days[day].append(volume)

    cumulative_ok = 0        # 单调且末值≈日量：真累计口径，无告警
    cumulative_lost = []     # 单调但末值≠日量：疑似累计口径遇上日线多出的量（大宗/竞价）→ 现行代码会误判为增量，Volume 列会爆掉
    incremental_ok = 0       # 非单调（真增量口径），合计≈日量
    incremental_over = []    # 非单调且合计>日量 1% 以上
    incremental_under = []   # 非单调且合计<日量 1% 以上
    for day, values in sorted(days.items()):
        daily_volume = daily.get(day)
        if not daily_volume:
            continue
        monotonic = all(second >= first for first, second in zip(values, values[1:]))
        last_value, total = values[-1], sum(values)
        tolerance = max(1.0, daily_volume * 1e-6)
        deviation_pct = (total - daily_volume) / daily_volume * 100
        if monotonic and abs(last_value - daily_volume) <= tolerance:
            cumulative_ok += 1
        elif monotonic:
            cumulative_lost.append((day, len(values), last_value, total, daily_volume, round(deviation_pct, 2)))
        elif deviation_pct > 1.0:
            incremental_over.append((day, len(values), last_value, total, daily_volume, round(deviation_pct, 2)))
        elif deviation_pct < -1.0:
            incremental_under.append((day, len(values), last_value, total, daily_volume, round(deviation_pct, 2)))
        else:
            incremental_ok += 1

    print(f"\n== {code} {year} == 累计正常 {cumulative_ok} 天；非单调且偏差<1% {incremental_ok} 天")
    print(f"   单调但末值!=日量（现行代码误判风险）: {len(cumulative_lost)} 天")
    for row in cumulative_lost[:4]:
        print("     ", row)
    print(f"   非单调且合计>日量: {len(incremental_over)} 天；合计<日量: {len(incremental_under)} 天")
    for row in incremental_over[:3]:
        print("      over ", row)
    for row in incremental_under[:3]:
        print("      under", row)


classify("sh.600519", "2024")
classify("sh.600000", "2024")
classify("sz.000001", "2023")
classify("sz.000001", "2024")
