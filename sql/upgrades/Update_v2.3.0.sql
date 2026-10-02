-- v2.3.0: 已问过上游的日区间账
--
-- 记的是「这一只合约、这一档频率，这一次向上游问成功了哪一段」，而不是「哪几天真有 bar」。
-- 两者必须分开：上游对停牌日、未上市日根本不返回数据，只按 bar 记账的那个缺口**永远补不上**，
-- 覆盖判据会判它「缺」，于是含停牌日的区间每次提交都重下一次，永不收敛。账记的是我们做过的
-- 事（问过了），不是上游给出的答案（有 bar），所以问过一次就闭口。
--
-- StartDay / EndDay 是**实际问到**的闭区间，末端已由调用方掐在组件交易日历的末日之内：当前
-- 年份还没走完，把 12-31 记成「问过了」就是把未来记进账里，那些日子真到了反而不会再取。
--
-- 多个区间由写入侧合并成互不相接的几段（重叠或首尾相接的都并成一段），故常驻行数是每合约
-- 一两行，而不是每次调用一行。
CREATE TABLE IF NOT EXISTS QueriedBarSpans (
    Code        TEXT NOT NULL,
    Frequency   TEXT NOT NULL,
    StartDay    TEXT NOT NULL,
    EndDay      TEXT NOT NULL,
    PRIMARY KEY (Code, Frequency, StartDay)
);
