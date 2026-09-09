-- v2.2.0: PerformanceExpress 列定义对齐 BaoStock 接口实际返回字段
-- 旧列（Revenue/DeductedNP/NetProfit/ROEChgPct）为臆测口径，接口无这些字段，
-- 历史上从未成功写入；表为空，故旧库由 init_database 检测旧结构后重建（无数据迁移）。
-- 实际接口字段: performanceExpressTotalAsset / NetAsset / EPSChgPct / ROEWa /
--               EPSDiluted / GRYOY(营收同比) / OPYOY(营业利润同比)
CREATE TABLE IF NOT EXISTS PerformanceExpress (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    UpdateDate  TEXT,
    StatDate    TEXT,
    TotalAsset  REAL,
    NetAsset    REAL,
    EPSChgPct   REAL,
    ROEWa       REAL,
    EPSDiluted  REAL,
    GRYOY       REAL,
    OPYOY       REAL,
    CONSTRAINT PkPerformanceExpress PRIMARY KEY (Code, Year, Quarter)
);
