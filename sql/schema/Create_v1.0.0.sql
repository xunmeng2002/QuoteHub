-- BaoStock 本地数据库 Schema v1.0.0
-- 适用数据库: SQLite
--
-- 本文件是本仓唯一的建库脚本，由仓根的 init_database.py 执行：
--   python init_database.py --create    在 config.json 的 database_path 上建新库
--   python init_database.py --upgrade   认领或升级既有库
-- 应用侧（bao_stock.py / bao_stock_parquet.py）启动只连接、只增删改查，不建表、不迁移。
--
-- 重要: 本文件内容的 CRC32（行尾归一为 LF 后计算，存入 PRAGMA user_version）即数据库版本戳，
--       也是应用侧启动比对的期望值。改动本文件（哪怕只改注释）会让既有库判为版本不符，
--       须重跑 --upgrade 重新打戳。
--
-- 包含: 日K行情、复权因子、证券合约、交易日志、季频财务、除权除息、业绩快报与预告、
--       指数成分股、行业分类、交易日历、宏观经济、分钟线

-- ===================================================================
-- 行情与证券
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
-- 季频财务数据（6 张表）
-- ===================================================================

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

-- ===================================================================
-- 公司事件
-- ===================================================================

-- 除权除息信息
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
    CONSTRAINT PkDividendData PRIMARY KEY (Code, Year, YearType, DividOperateDate)
);

-- 业绩快报：列定义对齐 BaoStock 接口实际返回字段（performanceExpressTotalAsset /
-- NetAsset / EPSChgPct / ROEWa / EPSDiluted / GRYOY 营收同比 / OPYOY 营业利润同比）
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

-- 业绩预告
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

-- ===================================================================
-- 参考数据
-- ===================================================================

-- 指数成分股
CREATE TABLE IF NOT EXISTS IndexConstituents (
    IndexCode  TEXT NOT NULL,   -- 'hs300' / 'sz50' / 'zz500'
    Code       TEXT NOT NULL,
    CodeName   TEXT,
    UpdateDate TEXT,
    CONSTRAINT PkIndexConstituents PRIMARY KEY (IndexCode, Code, UpdateDate)
);

-- 行业分类
CREATE TABLE IF NOT EXISTS IndustryClassification (
    Code                   TEXT NOT NULL,
    CodeName               TEXT,
    Industry               TEXT,
    IndustryClassification TEXT,
    UpdateDate             TEXT,
    CONSTRAINT PkIndustryClassification PRIMARY KEY (Code, UpdateDate)
);

-- 交易日历
CREATE TABLE IF NOT EXISTS TradeDates (
    CalendarDate TEXT NOT NULL,
    IsTradingDay INTEGER,  -- 1=交易日, 0=非交易日
    CONSTRAINT PkTradeDates PRIMARY KEY (CalendarDate)
);

-- 存款利率（Baostock 宽表格式：每种利率类型独立列）
CREATE TABLE IF NOT EXISTS DepositRates (
    PubDate                          TEXT NOT NULL,
    DemandDepositRate                REAL,
    FixedDepositRate3Month           REAL,
    FixedDepositRate6Month           REAL,
    FixedDepositRate1Year            REAL,
    FixedDepositRate2Year            REAL,
    FixedDepositRate3Year            REAL,
    FixedDepositRate5Year            REAL,
    InstallmentFixedDepositRate1Year REAL,
    InstallmentFixedDepositRate3Year REAL,
    InstallmentFixedDepositRate5Year REAL,
    CONSTRAINT PkDepositRates PRIMARY KEY (PubDate)
);

-- 贷款利率（Baostock 宽表格式）
CREATE TABLE IF NOT EXISTS LoanRates (
    PubDate             TEXT NOT NULL,
    LoanRate6Month      REAL,
    LoanRate6MonthTo1Year REAL,
    LoanRate1YearTo3Year  REAL,
    LoanRate3YearTo5Year  REAL,
    LoanRateAbove5Year    REAL,
    MortgateRateBelow5Year  REAL,
    MortgateRateAbove5Year  REAL,
    CONSTRAINT PkLoanRates PRIMARY KEY (PubDate)
);

-- 存款准备金率（Baostock 宽表格式）
CREATE TABLE IF NOT EXISTS ReserveRatios (
    PubDate                    TEXT NOT NULL,
    EffectiveDate              TEXT,
    BigInstitutionsRatioPre    REAL,
    BigInstitutionsRatioAfter  REAL,
    MediumInstitutionsRatioPre    REAL,
    MediumInstitutionsRatioAfter  REAL,
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

-- SHIBOR 拆放利率：BaoStock 当前无此 API，未建表

-- ===================================================================
-- 分钟线
-- ===================================================================

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
    CONSTRAINT PkMinuteBars PRIMARY KEY (Code, Frequency, Time)
);

CREATE INDEX IF NOT EXISTS IdxMinuteBarsFrequencyTradingDay ON MinuteBars (Frequency, TradingDay);

-- 分钟线覆盖交易日的日度统计：Preclose 供导出时回填当日首根 bar 的 PreClosePrice，
-- Volume/Amount 供增量/累计口径判别与交叉校验。
CREATE TABLE IF NOT EXISTS MinuteBarTradingDays (
    Code        TEXT NOT NULL,
    TradingDay  TEXT NOT NULL,
    Preclose    REAL,
    Volume      REAL,
    Amount      REAL,
    CONSTRAINT PkMinuteBarTradingDays PRIMARY KEY (Code, TradingDay)
);

CREATE INDEX IF NOT EXISTS IdxMinuteBarTradingDaysTradingDay ON MinuteBarTradingDays (TradingDay);

-- 已问过的日区间账：记「这一只合约、这一档频率，这一次向上游问成功了哪一段」，而不是
-- 「哪几天真有 bar」。两者必须分开——上游对停牌日、未上市日根本不返回数据，只按 bar 记账的
-- 缺口永远补不上，覆盖判据会把含停牌日的区间每次重下一遍，永不收敛。账记的是我们做过的事，
-- 所以问过一次就闭口。
--
-- StartDay/EndDay 是实际问到的闭区间，末端已由调用方掐在组件交易日历的末日之内：当年还没走完，
-- 把 12-31 记成「问过了」就是把未来记进账里，那些日子真到了反而不会再取。多个区间由写入侧
-- 合并成互不相接的几段，故常驻行数是每合约一两行，而不是每次调用一行。
CREATE TABLE IF NOT EXISTS QueriedBarSpans (
    Code        TEXT NOT NULL,
    Frequency   TEXT NOT NULL,
    StartDay    TEXT NOT NULL,
    EndDay      TEXT NOT NULL,
    CONSTRAINT PkQueriedBarSpans PRIMARY KEY (Code, Frequency, StartDay)
);
