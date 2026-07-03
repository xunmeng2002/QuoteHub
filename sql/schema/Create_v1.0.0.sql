-- BaoStock 本地数据库 Schema v1.0.0
-- 适用数据库: SQLite
-- 包含: 日K行情、复权因子、证券合约、交易日志

-- 日K不复权价格表
CREATE TABLE IF NOT EXISTS DailyRawPrices (
    Code   TEXT NOT NULL,
    Date   TEXT NOT NULL,
    Open   REAL,
    High   REAL,
    Low    REAL,
    Close  REAL,
    Volume REAL,
    Amount REAL,
    CONSTRAINT PkDailyRawPrices PRIMARY KEY (Code, Date)
);

-- 复权因子表
CREATE TABLE IF NOT EXISTS AdjustFactors (
    Code       TEXT NOT NULL,
    EventDate  TEXT NOT NULL,
    ForeFactor REAL,
    BackFactor REAL,
    CumFactor  REAL,
    CONSTRAINT PkAdjustFactors PRIMARY KEY (Code, EventDate)
);

-- 证券合约主表
CREATE TABLE IF NOT EXISTS Securities (
    Code               TEXT NOT NULL,
    CodeName           TEXT,
    IpoDate            TEXT,
    DelistDate         TEXT,
    StockType          TEXT,
    Exchange           TEXT,
    IndustryCode       TEXT,
    IndustryName       TEXT,
    CurrentTradeStatus TEXT,
    UpdatedAt          TEXT,
    CONSTRAINT PkSecurities PRIMARY KEY (Code)
);

-- 交易状态变更日志（停牌/复牌）
CREATE TABLE IF NOT EXISTS TradeStatusLogs (
    Code        TEXT NOT NULL,
    Date        TEXT NOT NULL,
    TradeStatus TEXT,
    CONSTRAINT PkTradeStatusLogs PRIMARY KEY (Code, Date)
);

-- 证券更名日志
CREATE TABLE IF NOT EXISTS NameChangeLogs (
    Code    TEXT NOT NULL,
    Date    TEXT NOT NULL,
    OldName TEXT,
    NewName TEXT,
    CONSTRAINT PkNameChangeLogs PRIMARY KEY (Code, Date)
);
