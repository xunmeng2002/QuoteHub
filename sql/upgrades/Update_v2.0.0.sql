-- BaoStock 数据库 v1.0.0 → v2.0.0 增量升级
-- 适用数据库: SQLite
-- 说明: 仅包含 v2.0.0 新增表，v1.0.0 表保持不变
--       所有 CREATE TABLE 均使用 IF NOT EXISTS，保证多次执行幂等

-- ===================================================================
-- 季频财务数据（6 张表）
-- ===================================================================

CREATE TABLE IF NOT EXISTS ProfitData (
    Code     TEXT NOT NULL,
    Year     INTEGER NOT NULL,
    Quarter  INTEGER NOT NULL,
    PubDate  TEXT,
    StatDate TEXT,
    RoeAvg   REAL,
    NpMargin REAL,
    GpMargin REAL,
    NetProfit REAL,
    EpsTTM     REAL,
    MBRevenue  REAL,
    TotalShare REAL,
    LiqaShare  REAL,
    CONSTRAINT PkProfitData PRIMARY KEY (Code, Year, Quarter)
);

CREATE TABLE IF NOT EXISTS OperationData (
    Code            TEXT NOT NULL,
    Year            INTEGER NOT NULL,
    Quarter         INTEGER NOT NULL,
    PubDate         TEXT,
    StatDate        TEXT,
    NRTurnRatio     REAL,
    NRTurnDays      REAL,
    INVTurnRatio    REAL,
    INVTurnDays     REAL,
    CATurnRatio     REAL,
    AssetTurnRatio  REAL,
    CONSTRAINT PkOperationData PRIMARY KEY (Code, Year, Quarter)
);

CREATE TABLE IF NOT EXISTS GrowthData (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    YOYEquity   REAL,
    YOYAsset    REAL,
    YOYNI       REAL,
    YOYEPSBasic REAL,
    YOYPNI      REAL,
    CONSTRAINT PkGrowthData PRIMARY KEY (Code, Year, Quarter)
);

CREATE TABLE IF NOT EXISTS BalanceData (
    Code            TEXT NOT NULL,
    Year            INTEGER NOT NULL,
    Quarter         INTEGER NOT NULL,
    PubDate         TEXT,
    StatDate        TEXT,
    CurrentRatio    REAL,
    QuickRatio      REAL,
    CashRatio       REAL,
    YOYLiability    REAL,
    LiabilityToAsset REAL,
    AssetToEquity   REAL,
    CONSTRAINT PkBalanceData PRIMARY KEY (Code, Year, Quarter)
);

CREATE TABLE IF NOT EXISTS CashFlowData (
    Code             TEXT NOT NULL,
    Year             INTEGER NOT NULL,
    Quarter          INTEGER NOT NULL,
    PubDate          TEXT,
    StatDate         TEXT,
    CAToAsset        REAL,
    NCAToAsset       REAL,
    TangibleAssetToAsset REAL,
    EbitToInterest   REAL,
    CFOToOR          REAL,
    CFOToNP          REAL,
    CFOToGr          REAL,
    CONSTRAINT PkCashFlowData PRIMARY KEY (Code, Year, Quarter)
);

CREATE TABLE IF NOT EXISTS DuPontData (
    Code              TEXT NOT NULL,
    Year              INTEGER NOT NULL,
    Quarter           INTEGER NOT NULL,
    PubDate           TEXT,
    StatDate          TEXT,
    DupontROE         REAL,
    DupontAssetStoEquity REAL,
    DupontAssetTurn   REAL,
    DupontPnitoni     REAL,
    DupontNitogr      REAL,
    DupontTaxBurden   REAL,
    DupontIntburden   REAL,
    DupontEbittogr    REAL,
    CONSTRAINT PkDuPontData PRIMARY KEY (Code, Year, Quarter)
);

-- ===================================================================
-- 除权除息信息
-- ===================================================================

CREATE TABLE IF NOT EXISTS DividendData (
    Code                  TEXT NOT NULL,
    Year                  INTEGER NOT NULL,
    YearType              TEXT NOT NULL,
    DividPreNoticeDate    TEXT,
    DividAgmPumDate       TEXT,
    DividPlanAnnounceDate TEXT,
    DividPlanDate         TEXT,
    DividRegistDate       TEXT,
    DividOperateDate      TEXT,
    DividPayDate          TEXT,
    DividStockMarketDate  TEXT,
    DividCashPsBeforeTax  REAL,
    DividCashPsAfterTax   REAL,
    DividStocksPs         REAL,
    DividCashStock        REAL,
    DividReserveToStockPs REAL,
    CONSTRAINT PkDividendData PRIMARY KEY (Code, DividOperateDate)
);

-- ===================================================================
-- 业绩快报
-- ===================================================================

CREATE TABLE IF NOT EXISTS PerformanceExpress (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    Revenue     REAL,
    DeductedNP  REAL,
    NetProfit   REAL,
    EPSChgPct   REAL,
    ROEChgPct   REAL,
    CONSTRAINT PkPerformanceExpress PRIMARY KEY (Code, Year, Quarter)
);

-- ===================================================================
-- 业绩预告
-- ===================================================================

CREATE TABLE IF NOT EXISTS ForecastReports (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    ForecastType TEXT,
    Abstract    TEXT,
    ChgPctUp    REAL,
    ChgPctDwn   REAL,
    CONSTRAINT PkForecastReports PRIMARY KEY (Code, Year, Quarter, PubDate)
);

-- ===================================================================
-- 指数成分股
-- ===================================================================

CREATE TABLE IF NOT EXISTS IndexConstituents (
    IndexCode  TEXT NOT NULL,
    Code       TEXT NOT NULL,
    CodeName   TEXT,
    UpdateDate TEXT,
    CONSTRAINT PkIndexConstituents PRIMARY KEY (IndexCode, Code, UpdateDate)
);

-- ===================================================================
-- 行业分类
-- ===================================================================

CREATE TABLE IF NOT EXISTS IndustryClassification (
    Code                   TEXT NOT NULL,
    CodeName               TEXT,
    Industry               TEXT,
    IndustryClassification TEXT,
    UpdateDate             TEXT,
    CONSTRAINT PkIndustryClassification PRIMARY KEY (Code, UpdateDate)
);

-- ===================================================================
-- 交易日历
-- ===================================================================

CREATE TABLE IF NOT EXISTS TradeDates (
    CalendarDate TEXT NOT NULL,
    IsTradingDay INTEGER,
    CONSTRAINT PkTradeDates PRIMARY KEY (CalendarDate)
);

-- ===================================================================
-- 宏观经济数据（5 张表）
-- ===================================================================

CREATE TABLE IF NOT EXISTS DepositRates (
    PubDate     TEXT NOT NULL,
    DepositType TEXT NOT NULL,
    DepositRate REAL,
    CONSTRAINT PkDepositRates PRIMARY KEY (PubDate, DepositType)
);

CREATE TABLE IF NOT EXISTS LoanRates (
    PubDate   TEXT NOT NULL,
    LoanType  TEXT NOT NULL,
    LoanRate  REAL,
    CONSTRAINT PkLoanRates PRIMARY KEY (PubDate, LoanType)
);

CREATE TABLE IF NOT EXISTS ReserveRatios (
    PubDate          TEXT NOT NULL,
    EffectiveDate    TEXT,
    DepositRate      REAL,
    SmallDepositRate REAL,
    CONSTRAINT PkReserveRatios PRIMARY KEY (PubDate)
);

CREATE TABLE IF NOT EXISTS MoneySupply (
    StatYear        INTEGER NOT NULL,
    StatMonth       INTEGER NOT NULL,
    M0Month         REAL,
    M0YOY           REAL,
    M0ChainRelative REAL,
    M1Month         REAL,
    M1YOY           REAL,
    M1ChainRelative REAL,
    M2Month         REAL,
    M2YOY           REAL,
    M2ChainRelative REAL,
    CONSTRAINT PkMoneySupply PRIMARY KEY (StatYear, StatMonth)
);

-- SHIBOR 拆放利率（BaoStock 当前版本未提供此 API，预留）
-- CREATE TABLE IF NOT EXISTS ShiborRates (
--     Date TEXT NOT NULL,
--     Overnight REAL,
--     W1   REAL,
--     M1   REAL,
--     M3   REAL,
--     M6   REAL,
--     M9   REAL,
--     Y1   REAL,
--     CONSTRAINT PkShiborRates PRIMARY KEY (Date)
-- );
