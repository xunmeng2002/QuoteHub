-- BaoStock 本地数据库 Schema v2.0.0
-- 适用数据库: SQLite
-- 包含: 日K行情、复权因子、证券合约、交易日志、财务数据、指数成分股、
--       宏观经济、行业分类、交易日历
--
-- 从 v1.0.0 升级: 执行 sql/upgrades/Update_v2.0.0.sql

-- ===================================================================
-- v1.0.0 表（保持不变）
-- ===================================================================

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

-- ===================================================================
-- v2.0.0 新增表
-- ===================================================================

-- ----------------------------------------------------------------
-- 季频财务数据（6 张表）
-- ----------------------------------------------------------------

-- 盈利能力
CREATE TABLE IF NOT EXISTS ProfitData (
    Code     TEXT NOT NULL,
    Year     INTEGER NOT NULL,
    Quarter  INTEGER NOT NULL,
    PubDate  TEXT,
    StatDate TEXT,
    RoeAvg   REAL,  -- 净资产收益率 %
    NpMargin REAL,  -- 销售净利率 %
    GpMargin REAL,  -- 销售毛利率 %
    NetProfit REAL, -- 净利润（元）
    EpsTTM     REAL,  -- 每股收益
    MBRevenue  REAL,  -- 主营营业收入（元）
    TotalShare REAL,  -- 总股本
    LiqaShare  REAL,  -- 流通股本
    CONSTRAINT PkProfitData PRIMARY KEY (Code, Year, Quarter)
);

-- 营运能力
CREATE TABLE IF NOT EXISTS OperationData (
    Code            TEXT NOT NULL,
    Year            INTEGER NOT NULL,
    Quarter         INTEGER NOT NULL,
    PubDate         TEXT,
    StatDate        TEXT,
    NRTurnRatio     REAL,  -- 应收账款周转率
    NRTurnDays      REAL,  -- 应收账款周转天数
    INVTurnRatio    REAL,  -- 存货周转率
    INVTurnDays     REAL,  -- 存货周转天数
    CATurnRatio     REAL,  -- 流动资产周转率
    AssetTurnRatio  REAL,  -- 总资产周转率
    CONSTRAINT PkOperationData PRIMARY KEY (Code, Year, Quarter)
);

-- 成长能力
CREATE TABLE IF NOT EXISTS GrowthData (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    YOYEquity   REAL,  -- 净资产同比增长率
    YOYAsset    REAL,  -- 资产同比增长率
    YOYNI       REAL,  -- 净利润同比增长率
    YOYEPSBasic REAL,  -- 基本每股收益同比增长率
    YOYPNI      REAL,  -- 归属母公司股东净利润同比增长率
    CONSTRAINT PkGrowthData PRIMARY KEY (Code, Year, Quarter)
);

-- 偿债能力
CREATE TABLE IF NOT EXISTS BalanceData (
    Code            TEXT NOT NULL,
    Year            INTEGER NOT NULL,
    Quarter         INTEGER NOT NULL,
    PubDate         TEXT,
    StatDate        TEXT,
    CurrentRatio    REAL,  -- 流动比率
    QuickRatio      REAL,  -- 速动比率
    CashRatio       REAL,  -- 现金比率
    YOYLiability    REAL,  -- 负债同比增长率
    LiabilityToAsset REAL, -- 资产负债率 %
    AssetToEquity   REAL,  -- 权益乘数
    CONSTRAINT PkBalanceData PRIMARY KEY (Code, Year, Quarter)
);

-- 现金流量
CREATE TABLE IF NOT EXISTS CashFlowData (
    Code             TEXT NOT NULL,
    Year             INTEGER NOT NULL,
    Quarter          INTEGER NOT NULL,
    PubDate          TEXT,
    StatDate         TEXT,
    CAToAsset        REAL,  -- 流动资产占总资产比率
    NCAToAsset       REAL,  -- 非流动资产占总资产比率
    TangibleAssetToAsset REAL, -- 有形资产占总资产比率
    EbitToInterest   REAL,  -- 已获利息倍数（EBIT/利息）
    CFOToOR          REAL,  -- 经营性现金净流量对主营收入比率
    CFOToNP          REAL,  -- 经营性现金净流量对净利润比率
    CFOToGr          REAL,  -- 经营性现金净流量对营业利润比率
    CONSTRAINT PkCashFlowData PRIMARY KEY (Code, Year, Quarter)
);

-- 杜邦分析
CREATE TABLE IF NOT EXISTS DuPontData (
    Code              TEXT NOT NULL,
    Year              INTEGER NOT NULL,
    Quarter           INTEGER NOT NULL,
    PubDate           TEXT,
    StatDate          TEXT,
    DupontROE         REAL,  -- 净资产收益率
    DupontAssetStoEquity REAL,  -- 权益乘数（资产/权益）
    DupontAssetTurn   REAL,  -- 资产周转率
    DupontPnitoni     REAL,  -- 净利润/利润总额
    DupontNitogr      REAL,  -- 利润总额/息税前利润
    DupontTaxBurden   REAL,  -- 税收负担率
    DupontIntburden   REAL,  -- 利息负担率
    DupontEbittogr    REAL,  -- 息税前利润/营业总收入
    CONSTRAINT PkDuPontData PRIMARY KEY (Code, Year, Quarter)
);

-- ----------------------------------------------------------------
-- 除权除息信息
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DividendData (
    Code                  TEXT NOT NULL,
    Year                  INTEGER NOT NULL,
    YearType              TEXT NOT NULL,  -- 'report'=预案公告年份, 'operate'=除权除息年份
    DividPreNoticeDate    TEXT,   -- 预披露公告日期
    DividAgmPumDate       TEXT,   -- 股东大会公告日期
    DividPlanAnnounceDate TEXT,   -- 预案公告日期
    DividPlanDate         TEXT,   -- 预案日期
    DividRegistDate       TEXT,   -- 股权登记日
    DividOperateDate      TEXT,   -- 除权除息日
    DividPayDate          TEXT,   -- 派息日
    DividStockMarketDate  TEXT,   -- 红股上市日
    DividCashPsBeforeTax  REAL,   -- 每股派息（税前）
    DividCashPsAfterTax   REAL,   -- 每股派息（税后）
    DividStocksPs         REAL,   -- 每股转增股
    DividCashStock        REAL,   -- 每股送股
    DividReserveToStockPs REAL,   -- 每股转增资本公积金
    CONSTRAINT PkDividendData PRIMARY KEY (Code, DividOperateDate)
);

-- ----------------------------------------------------------------
-- 业绩快报
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS PerformanceExpress (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    Revenue     REAL,   -- 营业收入（元）
    DeductedNP  REAL,   -- 扣非净利润（元）
    NetProfit   REAL,   -- 净利润（元）
    EPSChgPct   REAL,   -- 每股收益同比变化 %
    ROEChgPct   REAL,   -- ROE 同比变化 %
    CONSTRAINT PkPerformanceExpress PRIMARY KEY (Code, Year, Quarter)
);

-- ----------------------------------------------------------------
-- 业绩预告
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ForecastReports (
    Code        TEXT NOT NULL,
    Year        INTEGER NOT NULL,
    Quarter     INTEGER NOT NULL,
    PubDate     TEXT,
    StatDate    TEXT,
    ForecastType TEXT,   -- 'increase'/'decrease'/'turnaround'/首亏/续盈/续亏/略增/略减/不确定
    Abstract    TEXT,    -- 预告内容摘要
    ChgPctUp    REAL,    -- 净利润同比增速上限 %
    ChgPctDwn   REAL,    -- 净利润同比增速下限 %
    CONSTRAINT PkForecastReports PRIMARY KEY (Code, Year, Quarter, PubDate)
);

-- ----------------------------------------------------------------
-- 指数成分股
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS IndexConstituents (
    IndexCode  TEXT NOT NULL,   -- 'hs300' / 'sz50' / 'zz500'
    Code       TEXT NOT NULL,
    CodeName   TEXT,
    UpdateDate TEXT,
    CONSTRAINT PkIndexConstituents PRIMARY KEY (IndexCode, Code, UpdateDate)
);

-- ----------------------------------------------------------------
-- 行业分类
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS IndustryClassification (
    Code                   TEXT NOT NULL,
    CodeName               TEXT,
    Industry               TEXT,
    IndustryClassification TEXT,
    UpdateDate             TEXT,
    CONSTRAINT PkIndustryClassification PRIMARY KEY (Code, UpdateDate)
);

-- ----------------------------------------------------------------
-- 交易日历
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS TradeDates (
    CalendarDate TEXT NOT NULL,
    IsTradingDay INTEGER,  -- 1=交易日, 0=非交易日
    CONSTRAINT PkTradeDates PRIMARY KEY (CalendarDate)
);

-- ----------------------------------------------------------------
-- 宏观经济数据（5 张表）
-- ----------------------------------------------------------------

-- 存款利率
CREATE TABLE IF NOT EXISTS DepositRates (
    PubDate     TEXT NOT NULL,
    DepositType TEXT NOT NULL,
    DepositRate REAL,
    CONSTRAINT PkDepositRates PRIMARY KEY (PubDate, DepositType)
);

-- 贷款利率
CREATE TABLE IF NOT EXISTS LoanRates (
    PubDate   TEXT NOT NULL,
    LoanType  TEXT NOT NULL,
    LoanRate  REAL,
    CONSTRAINT PkLoanRates PRIMARY KEY (PubDate, LoanType)
);

-- 存款准备金率
CREATE TABLE IF NOT EXISTS ReserveRatios (
    PubDate          TEXT NOT NULL,
    EffectiveDate    TEXT,
    DepositRate      REAL,  -- 大型存款类机构准备金率
    SmallDepositRate REAL,  -- 中小型存款类机构准备金率
    CONSTRAINT PkReserveRatios PRIMARY KEY (PubDate)
);

-- 货币供应量（月度）
CREATE TABLE IF NOT EXISTS MoneySupply (
    StatYear        INTEGER NOT NULL,
    StatMonth       INTEGER NOT NULL,
    M0Month         REAL,  -- M0 当月供应量（亿）
    M0YOY           REAL,  -- M0 同比 %
    M0ChainRelative REAL,  -- M0 环比 %
    M1Month         REAL,  -- M1 当月供应量（亿）
    M1YOY           REAL,  -- M1 同比 %
    M1ChainRelative REAL,  -- M1 环比 %
    M2Month         REAL,  -- M2 当月供应量（亿）
    M2YOY           REAL,  -- M2 同比 %
    M2ChainRelative REAL,  -- M2 环比 %
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
