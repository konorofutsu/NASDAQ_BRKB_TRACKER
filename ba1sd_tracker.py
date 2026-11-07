"""
BA1SD 訊號追蹤器
---------------------------------------------------------------------------
預設策略：BA1SD + MA20 50% + dl25%cash（另有 10/15/21 投票版、原版可選）
  = BA1 + 條件絕對防禦 → 10 年債 + MA200 且 波動 > 20% → BRK.B
    + MA20 反彈小注 50% + 從高點跌 25% 鎖定（至少 1 個月、站回 MA200 解鎖，鎖定期持現金）

安裝： pip install streamlit yfinance pandas numpy
執行： streamlit run ba1sd_tracker.py   （或 python ba1sd_tracker.py）
       瀏覽器會自動打開 http://localhost:8501

資料：Yahoo Finance（yfinance），盤中報價約延遲 15 分鐘。
訊號以「美東 16:00 收盤價」為準；盤中顯示的是「若以現價收盤」的預估。
本工具只是把回測規則自動化，不是投資建議。
"""
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
UK = ZoneInfo("Europe/London")

# 策略預設組合
PRESETS = {
    "BA1SD + MA20 50% + dl25%cash（預設）": {
        "trigger": 1.0, "lookbacks": [21], "vol_th": 20.0, "abs_th": 0.0, "ma": 200,
        "bet": 50, "bet_ma": 20,
        "lock_on": True, "lock_dd": 25.0, "lock_min": 21, "lock_asset": "現金",
        "bt": "回測（Nasdaq Comp＋BRK-B，1982–2026，合成 TQQQ，0.05%/邊）：年化約 30.5%、最大回撤約 −52%。",
    },
    "10/15/21 投票 + BA1SD + MA20 50% + dl25%cash": {
        "trigger": 1.0, "lookbacks": [10, 15, 21], "vol_th": 20.0, "abs_th": 0.0, "ma": 200,
        "bet": 50, "bet_ma": 20,
        "lock_on": True, "lock_dd": 25.0, "lock_min": 21, "lock_asset": "現金",
        "bt": "回測（Nasdaq Comp＋BRK-B，1982–2026，合成 TQQQ，0.05%/邊）：年化約 35.3%、最大回撤約 −45%。",
    },
    "BA1SD=10Y+MA200Motion（原版）": {
        "trigger": 1.0, "lookbacks": [21], "vol_th": 20.0, "abs_th": 0.0, "ma": 200,
        "bet": 0, "bet_ma": 20,
        "lock_on": False, "lock_dd": 25.0, "lock_min": 21, "lock_asset": "現金",
        "bt": "回測（Nasdaq Comp＋BRK-B，1982–2026，合成 TQQQ，0.05%/邊）：年化約 30.0%、最大回撤約 −52%。",
    },
}


def _parse_lbs(text: str, fallback):
    try:
        Ls = sorted({int(x) for x in text.replace("/", ",").split(",") if x.strip()})
        Ls = [l for l in Ls if 2 <= l <= 252]
        return Ls or list(fallback)
    except ValueError:
        return list(fallback)


def lookbacks_of(p: dict):
    """回看期列表；舊設定只有單一 lookback 時也相容。"""
    Ls = p.get("lookbacks") or [p.get("lookback", 21)]
    return [int(x) for x in Ls]
DEFAULT_PRESET = "BA1SD + MA20 50% + dl25%cash（預設）"

ASSET = {
    "T": ("TQQQ", "3 倍做多 Nasdaq-100", "#d9534f"),
    "B": ("BRK.B", "波克夏 B 股", "#2a6fdb"),
    "C": ("10 年期公債", "10 年公債 ETF 或同等資產", "#2e9e5b"),
    "R": ("現金", "貨幣基金／短期國庫券", "#8a8f98"),
}
ORDER = ["T", "B", "C", "R"]


# ───────────────────────── 策略邏輯（與回測相同） ─────────────────────────
def _votes(v, Ls):
    """把投票總和轉成「BRK.B x : Nasdaq y」（假設沒有平手的回看期）。"""
    n = len(Ls)
    b = int(round((n + v) / 2))
    return f"BRK.B {b} : Nasdaq {n - b}"


def compute(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """close: index=日期, 欄位 brk / ndx（收盤價）。
    每一列 = 該日收盤後，「下一個交易日」應有的配置（wT/wB/wC/wR）與所有中間指標。"""
    b, n = close["brk"], close["ndx"]
    rb, rn = b.pct_change(), n.pct_change()
    d = (rb - rn).values
    Ls = lookbacks_of(p)
    bLs = {l: b.pct_change(l).values for l in Ls}
    nLs = {l: n.pct_change(l).values for l in Ls}
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            bL = np.nanmean([bLs[l] for l in Ls], axis=0)      # 絕對防禦：各回看期報酬的平均
            nL = np.nanmean([nLs[l] for l in Ls], axis=0)
    vote = np.sum([np.sign(bLs[l] - nLs[l]) for l in Ls], axis=0)   # 相對強弱：多數決
    vol =(rn.rolling(21).std() * np.sqrt(252)).values
    ma = n.rolling(int(p["ma"])).mean().values
    maS = n.rolling(int(p["bet_ma"])).mean().values
    dd = (n / n.rolling(252, min_periods=1).max() - 1).values
    px = n.values
    trig, vth, ath = p["trigger"] / 100, p["vol_th"] / 100, p["abs_th"] / 100
    bet, lock_x, lock_n = p["bet"] / 100, p["lock_dd"] / 100, int(p["lock_min"])

    cur = "T"
    locked, left, armed = False, 0, True
    rows = []
    for k in range(len(close)):
        # ① ② BA1 + 條件絕對防禦
        reason = "未觸發（日差距未超過門檻），維持原狀態"
        if not np.isnan(bL[k]) and not np.isnan(d[k]) and abs(d[k]) > trig:
            if vol[k] > vth and bL[k] < ath and nL[k] < ath:
                cur, reason = "C", "觸發：高波動且兩者回看報酬皆為負 → 絕對防禦（10 年債）"
            elif vote[k] > 0:
                cur, reason = "B", "觸發：BRK.B 回看報酬較強" + (f"（投票 {_votes(vote[k], Ls)}）" if len(Ls) > 1 else "")
            elif vote[k] < 0:
                cur, reason = "T", "觸發：Nasdaq 回看報酬較強" + (f"（投票 {_votes(vote[k], Ls)}）" if len(Ls) > 1 else "")
        # ③ MA200 且 高波動 → TQQQ 改 BRK.B
        hold = cur
        below = not np.isnan(ma[k]) and px[k] < ma[k]
        if cur == "T" and below and vol[k] > vth:
            hold = "B"
            reason += "；Nasdaq 低於均線且高波動 → TQQQ 改 BRK.B"
        # ④ MA20 反彈小注
        bet_on = (bet > 0 and hold != "T" and not np.isnan(maS[k]) and not np.isnan(ma[k])
                  and px[k] > maS[k] and px[k] < ma[k])
        w = {"T": 0.0, "B": 0.0, "C": 0.0, "R": 0.0}
        if hold == "T":
            w["T"] = 1.0
        else:
            w["T"] = bet if bet_on else 0.0
            w[hold] = 1.0 - w["T"]
        if bet_on:
            reason += f"；站上 MA{int(p['bet_ma'])} 但仍低於 MA{int(p['ma'])} → 反彈小注 {p['bet']:g}% TQQQ"
        # ⑤ 跌幅鎖定
        lock_phase = ""
        if p["lock_on"]:
            if dd[k] <= -lock_x and armed and not locked:
                locked, left, armed = True, lock_n, False
            if dd[k] > -lock_x:
                armed = True
            if locked:
                if left > 0:
                    left -= 1
                    lock_phase = "最短鎖定期"
                elif not np.isnan(ma[k]) and px[k] > ma[k]:
                    locked = False
                else:
                    lock_phase = "等待站回均線"
            if lock_phase:
                if p["lock_asset"] == "現金":
                    w = {"T": 0.0, "B": 0.0, "C": 0.0, "R": 1.0}
                else:
                    t = w["T"]
                    w["T"] = 0.0
                    w["B" if hold == "T" else hold] += t
                reason = (f"🔒 鎖定中（{lock_phase}）：Nasdaq 從 252 日高點跌 ≥ {p['lock_dd']:g}% → "
                          f"全部{p['lock_asset']}" + (f"，最短期還剩 {left} 個交易日" if left > 0 else
                                                  f"；收盤站回 MA{int(p['ma'])} 後下一交易日解鎖"))
        rows.append({"base": cur, "hold": hold, "bet_on": bet_on, "lock": lock_phase,
                     "lock_left": left if lock_phase else 0, "why": reason,
                     "wT": w["T"], "wB": w["B"], "wC": w["C"], "wR": w["R"]})

    out = pd.DataFrame(rows, index=close.index)
    for c, v in [("brk", b), ("ndx", n), ("rb", rb), ("rn", rn)]:
        out[c] = v
    out["diff"], out["bL"], out["nL"], out["vol"] = d, bL, nL, vol
    out["vote"] = vote
    for l in Ls:
        out[f"b{l}"], out[f"n{l}"] = bLs[l], nLs[l]
    out["ma"], out["maS"], out["dd"] = ma, maS, dd
    out["alloc"] = [alloc_str(r) for r in out[["wT", "wB", "wC", "wR"]].itertuples(index=False)]
    return out


def alloc_str(w):
    parts = [(c, v) for c, v in zip(ORDER, w) if v > 1e-9]
    if len(parts) == 1:
        return ASSET[parts[0][0]][0]
    return " ＋ ".join(f"{v*100:.0f}% {ASSET[c][0]}" for c, v in parts)


# ───────────────────────────── 資料下載 ─────────────────────────────
def _download_daily():
    import yfinance as yf
    raw = yf.download(["BRK-B", "^IXIC"], period="5y", interval="1d",
                      auto_adjust=True, progress=False)["Close"]
    close = raw.rename(columns={"BRK-B": "brk", "^IXIC": "ndx"})[["brk", "ndx"]]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.dropna()


def _guess_brk_earnings(today: dt.date):
    """波克夏通常在週六早上公布：2 月底／3 月初（年報）、5 月、8 月、11 月的第一個週六。"""
    def first_sat(y, m):
        d = dt.date(y, m, 1)
        return d + dt.timedelta(days=(5 - d.weekday()) % 7)
    def last_sat_feb(y):
        d = dt.date(y, 3, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() - 5) % 7)
    cands = []
    for y in (today.year, today.year + 1):
        cands += [last_sat_feb(y), first_sat(y, 5), first_sat(y, 8), first_sat(y, 11)]
    return min(d for d in cands if d >= today)


def _download_earnings():
    """回傳 (日期, 來源)。先試 Yahoo 的財報日曆，失敗就用波克夏的慣例推估。"""
    today = dt.datetime.now(NY).date()
    try:
        import yfinance as yf
        cal = yf.Ticker("BRK-B").calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else cal.loc["Earnings Date"].tolist()
        dates = [pd.Timestamp(d).date() for d in (dates or [])]
        future = [d for d in dates if d >= today - dt.timedelta(days=3)]
        if future:
            return min(future), "Yahoo 財報日曆"
    except Exception:
        pass
    return _guess_brk_earnings(today), "依波克夏慣例推估（請以官方公告為準）"


def _download_live():
    import yfinance as yf
    out = {}
    for t, k in [("BRK-B", "brk"), ("^IXIC", "ndx")]:
        try:
            h = yf.Ticker(t).history(period="1d", interval="1m")
            if len(h):
                out[k] = (float(h["Close"].iloc[-1]), h.index[-1].tz_convert(NY))
        except Exception:
            pass
    return out


def market_state(now: dt.datetime) -> str:
    if now.weekday() >= 5:
        return "休市（週末）"
    t = now.time()
    if t < dt.time(9, 30):
        return "開盤前"
    if t < dt.time(16, 0):
        return "交易中"
    return "已收盤"


def split_confirmed(daily: pd.DataFrame, live: dict, now: dt.datetime):
    """回傳 (已確認收盤資料, 盤中預估資料或 None)。"""
    today = now.date()
    last = daily.index[-1].date()
    is_open_today = market_state(now) in ("交易中",) or (
        market_state(now) == "已收盤" and now.time() < dt.time(16, 15))
    partial = last == today and now.time() < dt.time(16, 15)
    confirmed = daily.iloc[:-1] if partial else daily

    prov = None
    if is_open_today or partial:
        row = {}
        for k in ("brk", "ndx"):
            if k in live and live[k][1].date() == today:
                row[k] = live[k][0]
            elif partial:
                row[k] = daily[k].iloc[-1]
        if len(row) == 2:
            prov = pd.concat([confirmed, pd.DataFrame(row, index=[pd.Timestamp(today)])])
    return confirmed, prov


# ───────────────────────────── 介面 ─────────────────────────────
def pct(v, digits=2, sign=True):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v*100:+.{digits}f}%" if sign else f"{v*100:.{digits}f}%"


def main_code(row):
    w = {c: row[f"w{c}"] for c in ORDER}
    return max(w, key=w.get)


def card(title, row, sub):
    code = main_code(row)
    _, _, color = ASSET[code]
    bars = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
        f'<span style="width:88px;font-size:.85rem">{ASSET[c][0]}</span>'
        f'<span style="flex:1;height:10px;background:#8884;border-radius:5px;overflow:hidden">'
        f'<span style="display:block;height:100%;width:{row[f"w{c}"]*100:.0f}%;background:{ASSET[c][2]}"></span></span>'
        f'<span style="width:40px;text-align:right;font-size:.85rem">{row[f"w{c}"]*100:.0f}%</span></div>'
        for c in ORDER if row[f"w{c}"] > 1e-9)
    return f"""
    <div style="border-radius:14px;padding:18px 20px;background:{color}14;
                border:1.5px solid {color};height:100%">
      <div style="font-size:0.9rem;opacity:.75">{title}</div>
      <div style="font-size:1.6rem;font-weight:700;color:{color};margin:4px 0 8px">{row['alloc']}</div>
      {bars}
      <div style="font-size:0.85rem;margin-top:10px">{sub}</div>
    </div>"""


def check_table(row, p, label):
    trig = abs(row["diff"]) > p["trigger"] / 100
    hv = row["vol"] > p["vol_th"] / 100
    both_neg = row["bL"] < p["abs_th"] / 100 and row["nL"] < p["abs_th"] / 100
    below = row["ndx"] < row["ma"]
    ok = lambda x: "✅ 是" if x else "❌ 否"
    Ls = lookbacks_of(p)
    M, S = int(p["ma"]), int(p["bet_ma"])
    lb_vals, lb_idx = [], []
    for l in Ls:
        bl, nl = row[f"b{l}"], row[f"n{l}"]
        win = "BRK.B" if bl > nl else ("Nasdaq" if bl < nl else "平手")
        lb_vals.append(f"BRK.B {pct(bl)} vs Nasdaq {pct(nl)} → {win}")
        lb_idx.append(f"② {l} 日報酬")
    if len(Ls) > 1:
        v = row["vote"]
        lb_vals.append(f"{_votes(v, Ls)} → " + ("BRK.B" if v > 0 else ("Nasdaq" if v < 0 else "平手")))
        lb_idx.append("② 投票結果（多數決）")
        avg_lbl = f"兩者 {'/'.join(map(str, Ls))} 日平均報酬皆 < {p['abs_th']:g}%？"
    else:
        avg_lbl = f"兩者 {Ls[0]} 日報酬皆 < {p['abs_th']:g}%？"
    vals = [
        pct(row["rb"]), pct(row["rn"]), pct(row["diff"]), ok(trig),
        *lb_vals,
        pct(row["vol"], 1, False), ok(hv),
        f"{ok(both_neg)}（BRK.B {pct(row['bL'])}、Nasdaq {pct(row['nL'])}）" if len(Ls) > 1 else ok(both_neg),
        f"{row['ndx']:,.0f} / {row['ma']:,.0f}（{pct(row['ndx']/row['ma']-1, 1)}）", ok(below),
        f"{row['ndx']:,.0f} / {row['maS']:,.0f}（{pct(row['ndx']/row['maS']-1, 1)}）",
        ok(row["bet_on"]) if p["bet"] > 0 else "（未啟用）",
        pct(row["dd"], 1), (f"🔒 {row['lock']}" if row["lock"] else "🔓 未鎖定") if p["lock_on"] else "（未啟用）",
        row["alloc"],
    ]
    idx = [
        "BRK.B 當日漲跌", "Nasdaq 當日漲跌", "① 日差距（BRK.B − Nasdaq）",
        f"① 觸發？（|差距| > {p['trigger']:g}%）",
        *lb_idx,
        "Nasdaq 21 日年化波動", f"波動 > {p['vol_th']:g}%？", avg_lbl,
        f"③ Nasdaq / MA{M}", f"③ 低於 MA{M}？",
        f"④ Nasdaq / MA{S}", f"④ 反彈小注（站上 MA{S}、低於 MA{M}）？",
        "⑤ 距 252 日高點", f"⑤ 跌幅鎖定（≥ {p['lock_dd']:g}%）",
        "➡ 下一交易日配置",
    ]
    return pd.DataFrame({label: vals}, index=idx)


def main():
    import streamlit as st

    st.set_page_config(page_title="BA1SD 訊號追蹤", page_icon="📈", layout="wide")

    # ── 側邊欄 ──
    with st.sidebar:
        st.header("⚙️ 策略")
        preset_name = st.selectbox("策略組合", list(PRESETS) + ["自訂"], index=0)
        base = PRESETS.get(preset_name, PRESETS[DEFAULT_PRESET])
        custom = preset_name == "自訂"
        with st.expander("參數", expanded=custom):
            p = {
                "trigger": st.number_input("① 日差距觸發門檻（%）", 0.0, 10.0, base["trigger"], 0.1, disabled=not custom),
                "lookbacks": _parse_lbs(st.text_input(
                    "② 相對強弱回看（交易日，多個用逗號分隔 → 多數決）",
                    ",".join(map(str, lookbacks_of(base))), disabled=not custom), lookbacks_of(base)),
                "vol_th": st.number_input("波動門檻（%，年化）", 5.0, 80.0, base["vol_th"], 1.0, disabled=not custom),
                "abs_th": st.number_input("絕對防禦門檻（%）", -20.0, 20.0, base["abs_th"], 0.5, disabled=not custom),
                "ma": st.number_input("③ 長均線（日）", 20, 400, base["ma"], 10, disabled=not custom),
                "bet": st.number_input("④ 反彈小注比重（%，0 = 不用）", 0, 100, base["bet"], 5, disabled=not custom),
                "bet_ma": st.number_input("④ 反彈小注短均線（日）", 5, 100, base["bet_ma"], 1, disabled=not custom),
                "lock_on": st.checkbox("⑤ 啟用跌幅鎖定", base["lock_on"], disabled=not custom),
                "lock_dd": st.number_input("⑤ 鎖定：從 252 日高點跌幅（%）", 5.0, 60.0, base["lock_dd"], 0.5, disabled=not custom),
                "lock_min": st.number_input("⑤ 最短鎖定（交易日，21 ≈ 1 個月）", 0, 252, base["lock_min"], 1, disabled=not custom),
                "lock_asset": st.selectbox("⑤ 鎖定期持有", ["現金", "BRK.B"],
                                           index=0 if base["lock_asset"] == "現金" else 1, disabled=not custom),
            }
        refresh = st.selectbox("自動更新間隔", [60, 120, 300, 900], index=1,
                               format_func=lambda s: f"{s//60} 分鐘")
        if st.button("🔄 立即更新資料"):
            st.cache_data.clear()
        st.divider()
        st.caption("資料來源：Yahoo Finance（約延遲 15 分鐘）。\n\n"
                   "本工具僅將回測規則自動化，不構成投資建議。")

    load_daily = st.cache_data(ttl=600, show_spinner=False)(_download_daily)
    load_live = st.cache_data(ttl=55, show_spinner=False)(_download_live)
    load_earn = st.cache_data(ttl=6 * 3600, show_spinner=False)(_download_earnings)

    def dashboard():
        now = dt.datetime.now(NY)
        try:
            daily = load_daily()
        except Exception as e:
            st.error(f"無法下載歷史資料：{e}")
            return
        if len(daily) < int(p["ma"]) + 260:
            st.error("歷史資料不足，請稍後再試。")
            return
        live = load_live()
        confirmed, prov = split_confirmed(daily, live, now)
        res_c = compute(confirmed, p)
        res_p = compute(prov, p) if prov is not None else None
        last_c = res_c.iloc[-1]

        # ── 標題列 ──
        st.title("📈 BA1SD 訊號追蹤")
        st.caption(f"策略：{preset_name}")
        state = market_state(now)
        live_t = max((v[1] for v in live.values()), default=None)
        st.markdown(
            f"**美股狀態：** {state}　｜　美東 {now:%m/%d %H:%M}　｜　英國 "
            f"{now.astimezone(UK):%m/%d %H:%M}　｜　最後確認收盤：{res_c.index[-1]:%Y-%m-%d}"
            + (f"　｜　盤中報價：{live_t:%H:%M}（美東）" if live_t is not None else ""))

        # ── BRK.B 財報提醒 ──
        try:
            e_date, e_src = load_earn()
            days = (e_date - now.date()).days
            if -3 <= days <= 10:
                wk = "一二三四五六日"[e_date.weekday()]
                holding_brk = last_c["wB"] > 0 or (res_p is not None and res_p.iloc[-1]["wB"] > 0)
                msg = (f"📢 **BRK.B 財報：{e_date:%Y/%m/%d}（週{wk}）**"
                       + ("，今天" if days == 0 else (f"，{days} 天後" if days > 0 else f"，{-days} 天前已公布"))
                       + f"（來源：{e_src}）。")
                if e_date.weekday() == 5:
                    msg += " 週六公布 → 下週一開盤才反映。"
                msg += (" 歷史上財報後第一個交易日 BRK.B 平均單日波動約 ±1.8%（平常約 ±0.8%），"
                        "約 6% 的情況超過 ±5%。")
                if holding_brk:
                    msg += " **你目前的配置有 BRK.B。**"
                st.info(msg)
        except Exception:
            pass

        if p["lock_on"] and last_c["lock"]:
            st.error(f"🔒 **鎖定中**（{last_c['lock']}）：Nasdaq 距 252 日高點 {pct(last_c['dd'], 1)}。"
                     + (f" 最短鎖定期還剩 **{int(last_c['lock_left'])}** 個交易日。" if last_c["lock_left"] > 0 else
                        f" 需收盤站回 MA{int(p['ma'])}（目前 {last_c['ma']:,.0f}，"
                        f"還要上漲約 {pct(max(last_c['ma']/last_c['ndx']-1, 0), 1, False)}）才解鎖。"))
        elif p["lock_on"]:
            need = (1 - p["lock_dd"] / 100) / (1 + last_c["dd"]) - 1   # 還要再跌多少（負數）
            st.caption(f"🔓 未鎖定。Nasdaq 距 252 日高點 {pct(last_c['dd'], 1)}，"
                       f"從現在再跌約 {pct(max(-need, 0), 1, False)} 會觸發鎖定。")

        # 目前配置從何時開始
        a = res_c["alloc"]
        start = a[a != a.shift()].index[-1]
        held_days = len(a.loc[start:])

        c1, c2, c3 = st.columns(3)
        c1.markdown(card(f"✅ 下一交易日應持有（依 {res_c.index[-1]:%m/%d} 收盤）",
                         last_c, last_c["why"]), unsafe_allow_html=True)
        if res_p is not None:
            last_p = res_p.iloc[-1]
            c2.markdown(card("⏱ 盤中預估：若以現價收盤", last_p, last_p["why"]),
                        unsafe_allow_html=True)
        else:
            c2.info("目前不在交易時段，沒有盤中預估。\n\n下一次判斷：美東 16:00 收盤後"
                    f"（英國時間約 {dt.datetime.combine(now.date(), dt.time(16), NY).astimezone(UK):%H:%M}）。")
        c3.markdown(card("📅 此配置開始", last_c,
                         f"決定日 {start:%Y-%m-%d}（已 {held_days} 個交易日）"),
                    unsafe_allow_html=True)

        if res_p is not None and res_p.iloc[-1]["alloc"] != last_c["alloc"]:
            st.warning(f"⚠️ 若今天以現價收盤，配置會改變："
                       f"**{last_c['alloc']} → {res_p.iloc[-1]['alloc']}**。請在收盤前留意。")
        elif res_p is not None:
            st.success("盤中預估與目前配置相同，今天收盤大概不會換倉。")

        # ── 條件檢查 ──
        st.subheader("🔍 條件檢查")
        tbl = check_table(last_c, p, f"已確認（{res_c.index[-1]:%m/%d} 收盤）")
        if res_p is not None:
            tbl = tbl.join(check_table(res_p.iloc[-1], p, "盤中預估（現價）"))
        st.table(tbl)

        # ── 圖表 ──
        st.subheader("📊 圖表（近一年）")
        yr = res_c.iloc[-252:]
        t1, t2, t3 = st.tabs(["Nasdaq 與均線", "21 日波動與跌幅", "配置時間軸"])
        import altair as alt

        def stretch(chart):
            try:
                st.altair_chart(chart, width="stretch")          # Streamlit ≥ 1.46
            except TypeError:
                st.altair_chart(chart, use_container_width=True)  # 較舊版本
        with t1:
            cols = {"ndx": "Nasdaq", "ma": f"MA{int(p['ma'])}"}
            if p["bet"] > 0:
                cols["maS"] = f"MA{int(p['bet_ma'])}"
            long = (yr[list(cols)].rename(columns=cols).reset_index(names="日期")
                    .melt("日期", var_name="線", value_name="點數"))
            stretch(alt.Chart(long).mark_line().encode(
                x=alt.X("日期:T", title=None),
                y=alt.Y("點數:Q", scale=alt.Scale(zero=False), title=None),
                color=alt.Color("線:N", legend=alt.Legend(orient="bottom", title=None)),
                tooltip=["日期:T", "線:N", alt.Tooltip("點數:Q", format=",.0f")]))
        with t2:
            v = pd.DataFrame({"21 日年化波動 %": yr["vol"] * 100, "波動門檻 %": p["vol_th"],
                              "距 252 日高點 %": yr["dd"] * 100}, index=yr.index)
            if p["lock_on"]:
                v["鎖定門檻 %"] = -p["lock_dd"]
            st.line_chart(v)
        with t3:
            w = yr[["wT", "wB", "wC", "wR"]].rename(columns={f"w{c}": ASSET[c][0] for c in ORDER}) * 100
            long = w.reset_index(names="日期").melt("日期", var_name="資產", value_name="比重 %")
            stretch(alt.Chart(long).mark_area().encode(
                x=alt.X("日期:T", title=None),
                y=alt.Y("比重 %:Q", stack=True, scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("資產:N", scale=alt.Scale(domain=[ASSET[c][0] for c in ORDER],
                                                         range=[ASSET[c][2] for c in ORDER]),
                                legend=alt.Legend(orient="bottom", title=None)),
                tooltip=["日期:T", "資產:N", alt.Tooltip("比重 %:Q", format=".0f")]))

        # ── 換倉紀錄 ──
        st.subheader("🔁 最近換倉紀錄")
        sw = res_c[res_c["alloc"] != res_c["alloc"].shift()].iloc[1:].tail(15)
        if len(sw):
            prev = res_c["alloc"].shift().loc[sw.index]
            log = pd.DataFrame({
                "決定日（收盤）": sw.index.strftime("%Y-%m-%d"),
                "從": prev.values, "換成": sw["alloc"].values, "原因": sw["why"].values,
            }).iloc[::-1]
            st.dataframe(log, hide_index=True)
            per_year = (res_c["alloc"] != res_c["alloc"].shift()).iloc[-252:].sum()
            st.caption(f"近一年配置變動 {per_year} 次。")

        with st.expander("📘 策略規則"):
            Ls, M, S = lookbacks_of(p), int(p["ma"]), int(p["bet_ma"])
            lbtxt = "/".join(map(str, Ls))
            if len(Ls) > 1:
                step2 = (f"2. **② 判斷**（分別看過去 **{lbtxt}** 個交易日的報酬）：\n"
                         f"   - Nasdaq 21 日波動 > **{p['vol_th']:g}%**，且兩者在各回看期的**平均**報酬都 < **{p['abs_th']:g}%** → **10 年期公債**（條件絕對防禦）\n"
                         f"   - 否則每個回看期各投一票（BRK.B 較強或 Nasdaq 較強），**多數決**：BRK.B 勝 → **BRK.B**；Nasdaq 勝 → **TQQQ**\n")
            else:
                step2 = (f"2. **② 判斷**（看過去 **{lbtxt}** 個交易日報酬）：\n"
                         f"   - Nasdaq 21 日波動 > **{p['vol_th']:g}%**，且兩者回看報酬都 < **{p['abs_th']:g}%** → **10 年期公債**（條件絕對防禦）\n"
                         f"   - 否則 BRK.B 較強 → **BRK.B**；Nasdaq 較強 → **TQQQ**\n")
            rules = f"""
每天 **美東 16:00 收盤後** 判斷，決定 **下一個交易日** 的配置：

1. **① 日差距觸發**：|BRK.B 當日漲跌 − Nasdaq 當日漲跌| > **{p['trigger']:g}%** 才進入步驟②，否則維持原狀態。
{step2.rstrip()}
3. **③ MA 波動過濾**：結果是 TQQQ，但 Nasdaq 低於 **MA{M}** 且 21 日波動 > **{p['vol_th']:g}%** → 改持 **BRK.B**。
"""
            if p["bet"] > 0:
                rules += (f"4. **④ 反彈小注**：沒有持 TQQQ，且 Nasdaq 站上 **MA{S}** 但仍低於 **MA{M}** → "
                          f"撥 **{p['bet']:g}%** 到 TQQQ，其餘維持原資產。\n")
            if p["lock_on"]:
                rules += (f"5. **⑤ 跌幅鎖定**：Nasdaq 從 252 日高點跌 ≥ **{p['lock_dd']:g}%** → 鎖定，"
                          f"至少 **{int(p['lock_min'])}** 個交易日；之後收盤站回 **MA{M}** 才解鎖（下一交易日恢復）。"
                          f"鎖定期間全部持 **{p['lock_asset']}**。解鎖後要等跌幅回到 {p['lock_dd']:g}% 以內，才會再次觸發。\n")
            rules += f"\n{base['bt'] if not custom else '（自訂參數，未回測）'}\n\n參數皆在同一份歷史數據上挑選，**未來表現很可能較低**；個別事件的回測數字對每日價格雜訊很敏感。"
            st.markdown(rules)
        st.caption(f"頁面每 {refresh//60} 分鐘自動更新。美國假日未特別處理。")

    if hasattr(st, "fragment"):
        st.fragment(run_every=refresh)(dashboard)()
    else:
        dashboard()
        st.caption("（你的 Streamlit 版本較舊，請按側邊欄「立即更新」或升級：pip install -U streamlit）")


if __name__ == "__main__":
    from streamlit import runtime
    if runtime.exists():          # 已經是 `streamlit run` 啟動
        main()
    else:                         # 用 `python ba1sd_tracker.py` 啟動 → 自動改用 streamlit run
        import os
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "streamlit", "run", os.path.abspath(__file__)])
