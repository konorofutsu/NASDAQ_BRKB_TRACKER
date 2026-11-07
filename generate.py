"""
BA1SD 手機版：產生一個靜態網頁（docs/index.html），可放在 GitHub Pages 用手機瀏覽。
預設策略：10/15/21 投票 + BA1SD + MA20 50% + dl25%cash

本機測試：  python generate.py           → 產生 docs/index.html，用瀏覽器打開
GitHub Actions 會依 .github/workflows/update.yml 的排程自動執行並發布。

可選：設定環境變數 TELEGRAM_TOKEN、TELEGRAM_CHAT_ID，配置改變時會推播到 Telegram。
"""
import datetime as dt
import html
import json
import os
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

import ba1sd_tracker as T

PRESET = os.environ.get("BA1SD_PRESET", "10/15/21 投票 + BA1SD + MA20 50% + dl25%cash")
OUT_DIR = os.environ.get("BA1SD_OUT", "docs")
NY, UK = T.NY, T.UK


# ───────────────────────── 資料 ─────────────────────────
def load(now):
    """回傳 (確認收盤資料, 盤中預估資料或 None, 盤中報價時間)。
    測試用：設定 BA1SD_TEST_CSV（欄位 brk、ndx）就不會連網。"""
    test_csv = os.environ.get("BA1SD_TEST_CSV")
    if test_csv:
        daily = pd.read_csv(test_csv, index_col=0, parse_dates=True)[["brk", "ndx"]].dropna().iloc[-1260:]
        live = {}
    else:
        daily = T._download_daily()
        live = T._download_live()
    confirmed, prov = T.split_confirmed(daily, live, now)
    live_t = max((v[1] for v in live.values()), default=None)
    return confirmed, prov, live_t


# ───────────────────────── 小工具 ─────────────────────────
def esc(s):
    return html.escape(str(s))


def bars(row):
    out = []
    for c in T.ORDER:
        w = row[f"w{c}"]
        if w > 1e-9:
            name, _, color = T.ASSET[c]
            out.append(f'<div class="bar"><span class="bn">{esc(name)}</span>'
                       f'<span class="bt"><span style="width:{w*100:.0f}%;background:{color}"></span></span>'
                       f'<span class="bp">{w*100:.0f}%</span></div>')
    return "".join(out)


def card(title, row, sub, cls=""):
    code = T.main_code(row)
    color = T.ASSET[code][2]
    return (f'<section class="card {cls}" style="--accent:{color}">'
            f'<div class="ct">{esc(title)}</div>'
            f'<div class="alloc">{esc(row["alloc"])}</div>{bars(row)}'
            f'<div class="why">{esc(sub)}</div></section>')


def table_html(df):
    cols = list(df.columns)
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join(
        "<tr><td class='k'>" + esc(i) + "</td>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>"
        for i, r in zip(df.index, df.values))
    return f"<div class='tw'><table><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def spark(res, p, days=126):
    """Nasdaq 與 MA200／MA20 的小圖（inline SVG）。"""
    y = res.iloc[-days:]
    series = [("ndx", "Nasdaq", "var(--fg)", 2), ("ma", f"MA{int(p['ma'])}", "#e0a03a", 1.5)]
    if p["bet"] > 0:
        series.append(("maS", f"MA{int(p['bet_ma'])}", "#7b8cff", 1.2))
    vals = pd.concat([y[c] for c, *_ in series]).dropna()
    lo, hi = vals.min(), vals.max()
    W, H, pad = 340, 140, 6
    def pts(col):
        s = y[col]
        xs = np.linspace(pad, W - pad, len(s))
        return " ".join(f"{x:.1f},{H - pad - (v - lo) / (hi - lo) * (H - 2 * pad):.1f}"
                        for x, v in zip(xs, s.values) if not np.isnan(v))
    lines = "".join(f'<polyline fill="none" stroke="{c}" stroke-width="{w}" points="{pts(col)}"/>'
                    for col, _, c, w in series)
    # 配置色帶
    xs = np.linspace(pad, W - pad, len(y) + 1)
    band = ""
    for i, (_, r) in enumerate(y.iterrows()):
        band += f'<rect x="{xs[i]:.1f}" y="{H}" width="{xs[i+1]-xs[i]+0.3:.1f}" height="8" fill="{T.ASSET[T.main_code(r)][2]}"/>'
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for _, n, c, _ in series)
    return (f'<svg viewBox="0 0 {W} {H+10}" class="spark" role="img" aria-label="Nasdaq 與均線">{lines}{band}</svg>'
            f'<div class="legend">{legend}<span class="muted">下方色帶 = 每日主要持倉</span></div>')


# ───────────────────────── Telegram ─────────────────────────
def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20)
        return True
    except Exception as e:
        print("Telegram 失敗：", e)
        return False


# ───────────────────────── 主程式 ─────────────────────────
def main():
    now = dt.datetime.now(NY)
    p = T.PRESETS[PRESET]
    confirmed, prov, live_t = load(now)
    res_c = T.compute(confirmed, p)
    res_p = T.compute(prov, p) if prov is not None else None
    last_c = res_c.iloc[-1]
    state = T.market_state(now)
    os.makedirs(OUT_DIR, exist_ok=True)

    # 財報提醒
    notes = []
    try:
        if os.environ.get("BA1SD_TEST_CSV"):
            e_date, e_src = T._guess_brk_earnings(now.date()), "依波克夏慣例推估（請以官方公告為準）"
        else:
            e_date, e_src = T._download_earnings()
        days = (e_date - now.date()).days
        if -3 <= days <= 10:
            wk = "一二三四五六日"[e_date.weekday()]
            msg = f"📢 BRK.B 財報：{e_date:%Y/%m/%d}（週{wk}）" + (
                "，今天" if days == 0 else (f"，{days} 天後" if days > 0 else f"，{-days} 天前已公布")) + f"（{e_src}）。"
            if e_date.weekday() == 5:
                msg += "週六公布 → 下週一開盤反映。"
            notes.append(("info", msg))
    except Exception:
        pass

    if p["lock_on"] and last_c["lock"]:
        txt = f"🔒 鎖定中（{last_c['lock']}）：Nasdaq 距 252 日高點 {T.pct(last_c['dd'], 1)}。"
        txt += (f"最短鎖定期還剩 {int(last_c['lock_left'])} 個交易日。" if last_c["lock_left"] > 0 else
                f"需收盤站回 MA{int(p['ma'])}（{last_c['ma']:,.0f}，還要上漲約 "
                f"{T.pct(max(last_c['ma']/last_c['ndx']-1, 0), 1, False)}）才解鎖。")
        notes.append(("danger", txt))
    elif p["lock_on"]:
        need = (1 - p["lock_dd"] / 100) / (1 + last_c["dd"]) - 1
        notes.append(("muted", f"🔓 未鎖定。Nasdaq 距 252 日高點 {T.pct(last_c['dd'], 1)}，"
                               f"再跌約 {T.pct(max(-need, 0), 1, False)} 會觸發鎖定。"))
    if res_p is not None:
        if res_p.iloc[-1]["alloc"] != last_c["alloc"]:
            notes.insert(0, ("warn", f"⚠️ 若今天以現價收盤，配置會改變：{last_c['alloc']} → {res_p.iloc[-1]['alloc']}"))
        else:
            notes.append(("ok", "盤中預估與目前配置相同，今天收盤大概不會換倉。"))

    # 配置開始日
    a = res_c["alloc"]
    start = a[a != a.shift()].index[-1]
    held = len(a.loc[start:])

    # 條件表
    tbl = T.check_table(last_c, p, f"確認（{res_c.index[-1]:%m/%d}）")
    if res_p is not None:
        tbl = tbl.join(T.check_table(res_p.iloc[-1], p, "盤中預估"))

    # 換倉紀錄
    sw = res_c[res_c["alloc"] != res_c["alloc"].shift()].iloc[1:].tail(10)
    prev = res_c["alloc"].shift().loc[sw.index]
    log_rows = "".join(
        f"<li><b>{d:%Y/%m/%d}</b>　{esc(pv)} → <b>{esc(nw)}</b><br><span class='muted'>{esc(w)}</span></li>"
        for d, pv, nw, w in zip(sw.index[::-1], prev.values[::-1], sw["alloc"].values[::-1], sw["why"].values[::-1]))

    uk_now = now.astimezone(UK)
    close_uk = dt.datetime.combine(now.date(), dt.time(16), NY).astimezone(UK)
    cards = card(f"✅ 下一交易日應持有（依 {res_c.index[-1]:%m/%d} 收盤）", last_c, last_c["why"])
    if res_p is not None:
        cards += card("⏱ 盤中預估：若以現價收盤", res_p.iloc[-1], res_p.iloc[-1]["why"], "prov")
    cards += (f'<section class="card mini"><div class="ct">📅 此配置自 {start:%Y/%m/%d} 起</div>'
              f'<div class="why">已 {held} 個交易日　｜　近一年配置變動 '
              f'{int((a != a.shift()).iloc[-252:].sum())} 次</div></section>')

    Ls = "/".join(map(str, T.lookbacks_of(p)))
    page = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>BA1SD 訊號</title>
<meta name="theme-color" content="#0f1115">
<link rel="manifest" href="manifest.json">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon.svg">
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#14161a;--muted:#6b7280;--line:#e5e7eb;--warn:#fff4d6;--warnb:#e0a03a;--danger:#fde8e8;--dangerb:#d9534f;--ok:#e6f6ec;--okb:#2e9e5b;--info:#e8f0fe;--infob:#2a6fdb}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0f1115;--card:#181b21;--fg:#e8eaed;--muted:#9aa0a6;--line:#2a2e36;--warn:#3a2f14;--danger:#3a1a1a;--ok:#14301f;--info:#152238}}}}
:root[data-theme=dark]{{--bg:#0f1115;--card:#181b21;--fg:#e8eaed;--muted:#9aa0a6;--line:#2a2e36;--warn:#3a2f14;--danger:#3a1a1a;--ok:#14301f;--info:#152238}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Noto Sans TC","PingFang TC",sans-serif;padding:16px 16px 40px;max-width:720px;margin:auto}}
h1{{font-size:1.35rem;margin:4px 0 2px}} h2{{font-size:1.05rem;margin:22px 0 8px}}
.sub,.muted{{color:var(--muted);font-size:.85rem}}
.card{{background:var(--card);border:1.5px solid var(--accent,var(--line));border-radius:14px;padding:14px 16px;margin:10px 0}}
.card.prov{{border-style:dashed}} .card.mini{{border-color:var(--line)}}
.ct{{font-size:.85rem;color:var(--muted)}}
.alloc{{font-size:1.5rem;font-weight:700;color:var(--accent);margin:2px 0 8px}}
.why{{font-size:.85rem;margin-top:8px}}
.bar{{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:.85rem}}
.bn{{width:84px}} .bp{{width:38px;text-align:right}}
.bt{{flex:1;height:10px;background:var(--line);border-radius:5px;overflow:hidden}} .bt span{{display:block;height:100%}}
.note{{border-radius:10px;padding:10px 12px;margin:8px 0;font-size:.9rem;border-left:4px solid}}
.note.warn{{background:var(--warn);border-color:var(--warnb)}} .note.danger{{background:var(--danger);border-color:var(--dangerb)}}
.note.ok{{background:var(--ok);border-color:var(--okb)}} .note.info{{background:var(--info);border-color:var(--infob)}}
.note.muted{{background:transparent;border-color:var(--line);color:var(--muted)}}
.tw{{overflow-x:auto;background:var(--card);border-radius:12px;border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;font-size:.82rem}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}} td.k{{color:var(--muted);min-width:9.5em}}
tr:last-child td{{border-bottom:0;font-weight:700}}
.spark{{width:100%;height:auto;background:var(--card);border-radius:12px;border:1px solid var(--line)}}
.legend{{display:flex;flex-wrap:wrap;gap:10px;font-size:.8rem;margin:6px 2px}} .legend i{{display:inline-block;width:12px;height:3px;margin-right:4px;vertical-align:middle}}
ul.log{{list-style:none;padding:0;margin:0}} ul.log li{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:6px 0;font-size:.88rem}}
details{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-top:10px}} summary{{font-weight:600;cursor:pointer}}
details li{{margin:4px 0;font-size:.88rem}}
</style></head><body>
<h1>📈 BA1SD 訊號</h1>
<div class="sub">{esc(PRESET)}</div>
<div class="sub">更新：英國 {uk_now:%m/%d %H:%M}（美東 {now:%H:%M}）｜美股：{esc(state)}｜最後確認收盤：{res_c.index[-1]:%Y/%m/%d}{f"｜盤中報價 {live_t:%H:%M} 美東" if live_t is not None else ""}</div>
{"".join(f'<div class="note {k}">{esc(t)}</div>' for k, t in notes)}
{cards}
<h2>🔍 條件檢查</h2>
{table_html(tbl)}
<h2>📊 近半年 Nasdaq 與均線</h2>
{spark(res_c, p)}
<h2>🔁 最近換倉</h2>
<ul class="log">{log_rows or "<li>（無）</li>"}</ul>
<details><summary>📘 策略規則</summary><ol>
<li>① |BRK.B 當日漲跌 − Nasdaq 當日漲跌| &gt; {p['trigger']:g}% 才判斷，否則維持原狀態。</li>
<li>② Nasdaq 21 日波動 &gt; {p['vol_th']:g}% 且兩者 {Ls} 日平均報酬都 &lt; {p['abs_th']:g}% → 10 年期公債；否則 {Ls} 日各投一票，多數決：BRK.B 勝 → BRK.B，Nasdaq 勝 → TQQQ。</li>
<li>③ 結果是 TQQQ，但 Nasdaq 低於 MA{int(p['ma'])} 且波動 &gt; {p['vol_th']:g}% → BRK.B。</li>
<li>④ 沒持 TQQQ，且 Nasdaq 站上 MA{int(p['bet_ma'])}、仍低於 MA{int(p['ma'])} → 撥 {p['bet']:g}% 到 TQQQ。</li>
<li>⑤ Nasdaq 從 252 日高點跌 ≥ {p['lock_dd']:g}% → 全部{esc(p['lock_asset'])}，至少 {int(p['lock_min'])} 個交易日；之後收盤站回 MA{int(p['ma'])} → 下一交易日解鎖。</li>
</ol><p class="muted">{esc(p['bt'])} 參數在同一份歷史數據上挑選，未來表現很可能較低。訊號以美東 16:00（英國約 {close_uk:%H:%M}）收盤為準。</p></details>
<p class="muted">資料：Yahoo Finance（約延遲 15 分鐘）。這只是把回測規則自動化，不是投資建議。</p>
</body></html>"""
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "BA1SD 訊號", "short_name": "BA1SD", "start_url": ".", "display": "standalone",
                   "background_color": "#0f1115", "theme_color": "#0f1115",
                   "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}]}, fh, ensure_ascii=False)
    with open(os.path.join(OUT_DIR, "icon.svg"), "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" rx="20" fill="#0f1115"/>'
                 '<polyline points="14,66 34,48 50,58 80,26" fill="none" stroke="#2e9e5b" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>')

    # 訊號 JSON（給推播判斷用）
    sig = {"updated": uk_now.isoformat(), "confirmed_date": f"{res_c.index[-1]:%Y-%m-%d}", "alloc": last_c["alloc"],
           "why": last_c["why"], "prov_alloc": res_p.iloc[-1]["alloc"] if res_p is not None else None,
           "lock": last_c["lock"]}
    path = os.path.join(OUT_DIR, "signal.json")
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:
            old = {}
    json.dump(sig, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # Telegram 推播：確認配置改變，或盤中預估顯示今天會換倉（同一天只提醒一次）
    msgs = []
    if old.get("alloc") and old.get("alloc") != sig["alloc"]:
        msgs.append(f"🔁 BA1SD 換倉（{sig['confirmed_date']} 收盤）：{old['alloc']} → {sig['alloc']}\n{sig['why']}")
    if sig["prov_alloc"] and sig["prov_alloc"] != sig["alloc"] and old.get("warned") != f"{now:%Y-%m-%d}":
        msgs.append(f"⚠️ BA1SD 盤中預估：若今天以現價收盤，{sig['alloc']} → {sig['prov_alloc']}")
        sig["warned"] = f"{now:%Y-%m-%d}"
        json.dump(sig, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elif old.get("warned"):
        sig["warned"] = old["warned"]
        json.dump(sig, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for m in msgs:
        print(m)
        telegram(m)
    print(f"已產生 {OUT_DIR}/index.html：{sig['alloc']}（{sig['confirmed_date']}）")


if __name__ == "__main__":
    main()
