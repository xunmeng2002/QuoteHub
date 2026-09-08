-- v2.1.0: 分钟线落库（BaoStockParquet 管线的事实源）
-- 分钟 K 线。Code 为 sh./sz. 合并形式；Frequency 为分钟数（'5'/'15'/'30'/'60'，文件名后缀加 m）；
-- Time 为 BaoStock 原生 bar 结束时间 'YYYYMMDDHHMMSSsss'；
-- Volume/Amount 为本周期增量（口径由脚本与日线统计交叉校验）。
CREATE TABLE IF NOT EXISTS MinuteBars (
    Code        TEXT NOT NULL,
    Frequency   TEXT NOT NULL,
    TradingDay  TEXT NOT NULL,
    Time        TEXT NOT NULL,
    Open        REAL,
    High        REAL,
    Low         REAL,
    Close       REAL,
    Volume      REAL,
    Amount      REAL,
    PRIMARY KEY (Code, Frequency, Time)
);

CREATE INDEX IF NOT EXISTS IX_MinuteBars_Frequency_TradingDay ON MinuteBars (Frequency, TradingDay);

-- 分钟线覆盖交易日的日度统计：Preclose 供导出时回填当日首根 bar 的 PreClosePrice，
-- Volume/Amount 供增量/累计口径判别与交叉校验。
CREATE TABLE IF NOT EXISTS MinuteBarTradingDays (
    Code        TEXT NOT NULL,
    TradingDay  TEXT NOT NULL,
    Preclose    REAL,
    Volume      REAL,
    Amount      REAL,
    PRIMARY KEY (Code, TradingDay)
);

CREATE INDEX IF NOT EXISTS IX_MinuteBarTradingDays_TradingDay ON MinuteBarTradingDays (TradingDay);
