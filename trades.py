"""
내 매매 기록 관리 + 보유 종목 매도 시점 판단

  python trades.py           → 메뉴 (매수 추가 / 매도 기록 / 목록 / 지금 점검)
  python trades.py --check   → 보유 종목만 바로 점검해서 results/holdings.html 생성

기록 파일: my_trades.csv (엑셀로 열어서 직접 고쳐도 됩니다)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

BASE = Path(__file__).parent
TRADES_FILE = BASE / "my_trades.csv"
CACHE = BASE / "cache"
RESULTS = BASE / "results"
COLS = ["종목코드", "종목명", "매수일", "매수가", "수량", "매도일", "매도가", "메모"]


# ════════════════════════════════════════════════════════════
# 기록 파일
# ════════════════════════════════════════════════════════════
def load_trades() -> pd.DataFrame:
    if not TRADES_FILE.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(TRADES_FILE, dtype=str, encoding="utf-8-sig").fillna("")
    for c in COLS:
        if c not in df:
            df[c] = ""
    return df[COLS]


def save_trades(df: pd.DataFrame) -> None:
    df[COLS].to_csv(TRADES_FILE, index=False, encoding="utf-8-sig")   # 엑셀 한글 호환


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("원", "").strip())
    except ValueError:
        return float("nan")


def _fmt_num(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def _date(x) -> date | None:
    s = str(x).strip().replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _names() -> dict:
    f = CACHE / "names.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _stats_cache() -> dict:
    f = CACHE / "stats.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        out = {}
        for region, table in d.items():          # json 키는 문자열 → 점수·기간을 숫자로 되돌림
            t2 = {}
            for k, st in (table or {}).items():
                if st and st.get("horizons"):
                    st["horizons"] = {int(h): v for h, v in st["horizons"].items()}
                t2[int(k) if str(k).isdigit() else k] = st
            out[region] = t2
        return out
    except Exception:
        return {}


def save_caches(names: dict, stats: dict) -> None:
    """stats = {지역: 확률표(probability_table 결과)}"""
    """매일 실행 때 종목명·과거 통계를 저장 → 단독 점검에서 재사용"""
    CACHE.mkdir(exist_ok=True)
    old = _names()
    old.update(names)
    (CACHE / "names.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    (CACHE / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, default=float), encoding="utf-8")


# ════════════════════════════════════════════════════════════
# 시세
# ════════════════════════════════════════════════════════════
def _download(ticker: str, period: str = "1y", adjust: bool = False) -> pd.DataFrame:
    import yfinance as yf
    try:
        d = yf.download(ticker, period=period, interval="1d", auto_adjust=adjust, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        return d[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        return pd.DataFrame()


KR_CODE = re.compile(r"^[0-9][0-9A-Z]{5}$")          # 005930, 0190C0(영문 섞인 새 종목코드)
ETF_NAME = re.compile(r"^(KODEX|TIGER|RISE|ACE|SOL|PLUS|HANARO|KIWOOM|KBSTAR|ARIRANG|KOSEF|TIMEFOLIO|WON|BNK|"
                      r"1Q|UNICORN|마이티|파워|에셋플러스|트러스톤|히어로즈|VITA|TRUSTON|FOCUS|DAISHIN343)\b|ETF|ETN", re.I)


def is_etf(name: str) -> bool:
    return bool(ETF_NAME.search(str(name or "")))


def naver_daily(code: str, count: int = 520) -> pd.DataFrame:
    """네이버 일봉(실제 체결가). 영문 섞인 새 종목코드·ETF도 됨"""
    import requests
    try:
        r = requests.get("https://fchart.stock.naver.com/sise.nhn",
                         params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        rows = re.findall(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"', r.text)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["Date"])
        return df.set_index("Date").astype(float)
    except Exception:
        return pd.DataFrame()


def _kr_suffix(code: str) -> str:
    names = _names()
    for suf in (".KS", ".KQ"):
        if code + suf in names:
            return suf
    return ".KS"


def resolve(code: str, period_days: int = 260) -> tuple[str, pd.DataFrame]:
    """사용자가 입력한 코드 → 티커와 일봉(실제 체결가 기준, 수정주가 아님)"""
    code = str(code).strip().upper()
    if code.isdigit() and len(code) < 6:          # 엑셀이 앞의 0을 지운 경우
        code = code.zfill(6)
    base = code.split(".")[0]
    if KR_CODE.match(base):
        d = naver_daily(base, period_days)
        if len(d):
            suf = code[-3:] if code.endswith((".KS", ".KQ")) else _kr_suffix(base)
            return base + suf, d
        for suf in (".KS", ".KQ"):
            d = _download(base + suf)
            if len(d):
                return base + suf, d
        return base + ".KS", pd.DataFrame()
    t = code.replace(".", "-").replace("/", "-")
    return t, _download(t)


# ════════════════════════════════════════════════════════════
# 매도 시점 판단
# ════════════════════════════════════════════════════════════
def evaluate(trade: pd.Series, ticker: str, h: pd.DataFrame, stats: dict | None) -> dict:
    import analysis as A
    region = "KR" if ticker.endswith((".KS", ".KQ")) else "US"
    buy_d, buy_p = _date(trade["매수일"]), _num(trade["매수가"])
    out = {"ticker": ticker, "region": region, "name": trade["종목명"] or ticker, "buy_date": buy_d,
           "buy_price": buy_p, "qty": _num(trade["수량"]), "memo": trade["메모"], "ok": False}
    if not len(h) or buy_d is None or not buy_p == buy_p:
        out["error"] = "시세를 못 받았거나 매수일·매수가 형식이 잘못됨"
        return out
    idx = pd.to_datetime(h.index).date
    after = h[idx > buy_d]
    since = h[idx >= buy_d]
    before = h[idx < buy_d]
    close = float(h["Close"].iloc[-1])
    ret = (close / buy_p - 1) * 100
    held = len(after)
    max_close = max(float(since["Close"].max()) if len(since) else close, buy_p)
    max_ret = (max_close / buy_p - 1) * 100

    stop = buy_p * (1 - C.STOP_LOSS_PCT / 100)
    target = buy_p * (1 + C.TAKE_PROFIT_PCT / 100)
    trail_on = max_ret >= C.TAKE_PROFIT_PCT
    trail = max(max_close * (1 - C.TRAIL_PCT / 100), buy_p) if trail_on else None
    support = float(before["Low"].tail(60).min()) if len(before) >= 20 else None

    # 매수 후 '대량 매도' 출현: 최근 3일 중 거래량 3배+ & 저가 쪽 마감 & −3% 이상 하락
    dump_day = None
    v20 = h["Volume"].rolling(20).mean().shift(1)
    rng = (h["High"] - h["Low"]).replace(0, np.nan)
    clv = (((h["Close"] - h["Low"]) - (h["High"] - h["Close"])) / rng).fillna(0)
    chg = h["Close"].pct_change() * 100
    for i in range(max(len(h) - 3, 0), len(h)):
        if idx[i] > buy_d and h["Volume"].iloc[i] >= 3 * v20.iloc[i] and clv.iloc[i] < -0.3 and chg.iloc[i] <= -3:
            dump_day = (idx[i], float(chg.iloc[i]), float(h["Volume"].iloc[i] / v20.iloc[i]))

    best = A.best_horizon(stats) if stats else None

    # 판단 (위에서부터 우선)
    reasons = []
    if close <= stop:
        status = ("sell", "매도 (손절)")
        reasons.append(f"현재가가 손절가({_p(stop, region)}, 매수가 −{C.STOP_LOSS_PCT:g}%) 이하입니다. 손실이 더 커지기 전에 정리하세요.")
    elif trail_on and close <= trail:
        status = ("sell", "매도 (이익 보호)")
        reasons.append(f"매수 후 최고 {_p(max_close, region)}(+{max_ret:.1f}%)에서 −{C.TRAIL_PCT:g}% 이상 내려와 "
                       f"추적 손절가 {_p(trail, region)}를 깼습니다. 남은 이익을 지키세요.")
    elif support and close < support:
        status = ("sell", "매도 고려 (지지선 이탈)")
        reasons.append(f"매수 전 60일 저점({_p(support, region)}) 아래로 내려왔습니다. 박스권이 깨진 신호입니다.")
    elif dump_day:
        status = ("warn", "매도 고려 (대량 매도 출현)")
        reasons.append(f"{dump_day[0]}에 평소의 {dump_day[2]:.1f}배 거래량으로 {dump_day[1]:.1f}% 하락·저가 마감 — "
                       "세력 물량 출회일 수 있습니다.")
    elif ret >= C.TAKE_PROFIT_PCT:
        status = ("take", "일부 익절 고려")
        reasons.append(f"+{C.TAKE_PROFIT_PCT:g}% 목표에 도달했습니다. 절반 정도 이익을 확정하고, "
                       f"나머지는 추적 손절가({_p(trail, region)})로 관리하는 방법을 권합니다.")
    elif best and held >= best and ret < C.TIME_EXIT_MIN_RET:
        status = ("warn", "정리 고려 (기대 기간 경과)")
        reasons.append(f"과거 통계상 가장 유리했던 보유 기간({best}거래일)이 지났는데 수익이 +{C.TIME_EXIT_MIN_RET:g}% 미만입니다. "
                       "기대했던 움직임이 안 나온 것이니 다른 기회로 옮기는 것도 방법입니다.")
    else:
        status = ("hold", "보유 유지")
        if best:
            reasons.append(f"손절·이탈 신호 없음. 과거 통계상 유리했던 보유 기간 {best}거래일 중 {held}일 경과"
                           + (f" — {best - held}거래일 정도 더 지켜보세요." if held < best else "."))
        else:
            reasons.append("손절·이탈 신호 없음.")
    if trail_on and status[0] in ("hold", "take"):
        reasons.append(f"추적 손절 작동 중: 종가가 {_p(trail, region)} 아래로 내려가면 매도.")
    if support and status[0] == "hold" and close < support * 1.03:
        reasons.append(f"지지선({_p(support, region)})에 가깝습니다. 이탈하면 매도.")

    pnl = (close - buy_p) * out["qty"] if out["qty"] == out["qty"] else None
    out.update({"ok": True, "close": close, "last_date": idx[-1], "ret": ret, "held": held, "max_ret": max_ret,
                "stop": stop, "target": target, "trail": trail, "support": support, "status": status,
                "reasons": reasons, "best": best, "stats": stats, "pnl": pnl})
    return out


def _dump_tag(o: dict) -> str:
    d = o.get("dump")
    if not d:
        return ""
    color = {"high": "var(--bad)", "mid": "var(--warn)", "low": "var(--ok)"}[d["level"]]
    txt = {"high": "높음", "mid": "중간", "low": "낮음"}[d["level"]]
    return f" · 세력 이탈 위험 <b style='color:{color}'>{txt} {d['score']}</b>"


def _p(v, region):
    if v is None:
        return "-"
    return f"{v:,.0f}원" if region == "KR" else f"${v:,.2f}"


# ════════════════════════════════════════════════════════════
# 실행: 보유 종목 전체 점검
# ════════════════════════════════════════════════════════════
def check_all(stats_by_region: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(보유 중 평가 목록, 매도 완료 목록)"""
    df = load_trades()
    stats_by_region = stats_by_region or _stats_cache()
    names = _names()
    open_, closed = [], []
    for row_i, t in df.iterrows():
        if not str(t["종목코드"]).strip():
            continue
        if str(t["매도일"]).strip() and str(t["매도가"]).strip():
            bp, sp = _num(t["매수가"]), _num(t["매도가"])
            bd, sd = _date(t["매수일"]), _date(t["매도일"])
            closed.append({"name": t["종목명"] or t["종목코드"], "code": t["종목코드"], "buy_date": bd, "sell_date": sd,
                           "buy": bp, "sell": sp, "ret": (sp / bp - 1) * 100 if bp else float("nan"),
                           "days": (sd - bd).days if bd and sd else None, "row": row_i,
                           "region": "KR" if KR_CODE.match(str(t["종목코드"]).strip().upper().zfill(6)) else "US"})
            continue
        ticker, h = resolve(t["종목코드"])
        if not t["종목명"]:
            t = t.copy()
            t["종목명"] = names.get(ticker, ticker)
        region = "KR" if ticker.endswith((".KS", ".KQ")) else "US"
        o = evaluate(t, ticker, h, (stats_by_region.get(region) or {}).get("all"))
        o["row"] = row_i
        open_.append(o)
    return open_, closed


STATUS_CLS = {"sell": "bad", "warn": "warn", "take": "ok", "hold": "info"}
STATUS_ICON = {"sell": "🔴", "warn": "🟠", "take": "🟢", "hold": "🔵"}

HOLD_CSS = """
.hold{border:2px solid var(--fg);border-radius:14px;padding:18px 20px;margin:18px 0}
.hold h2{margin-top:0;border:none;padding:0}
.pos{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.pos .top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}
.pos .nm{font-size:17px;font-weight:700} .pos .nm small{font-weight:normal}
.pos .badge{font-weight:800;font-size:16px;padding:6px 12px;border-radius:8px}
.pos .badge.bad{background:var(--badb);color:var(--bad)} .pos .badge.warn{background:var(--warnb);color:var(--warn)}
.pos .badge.ok{background:var(--okb);color:var(--ok)} .pos .badge.info{background:var(--infob);color:var(--info)}
.pos .ret{font-size:24px;font-weight:800} .pos .ret.up{color:#d93025} .pos .ret.dn{color:#1a73e8}
.pos ul{margin:8px 0;padding-left:18px} .pos li{font-size:14px;margin:3px 0}
.pos .lv{grid-template-columns:repeat(4,1fr)} @media(max-width:700px){.pos .lv{grid-template-columns:repeat(2,1fr)}}
.prog{height:8px;background:#ececf0;border-radius:5px;overflow:hidden;margin:6px 0 2px} .prog i{display:block;height:100%;background:var(--acc)}
.closed td{font-size:14px}
"""


def holdings_html(open_: list[dict], closed: list[dict]) -> str:
    if not open_ and not closed:
        return ("<div class='hold'><h2>📒 내 보유 종목</h2><p class='muted'>아직 입력한 매수 기록이 없습니다. "
                "<b>trades.bat</b>을 더블클릭해서 매수한 종목을 입력하면, 매일 여기에서 매도 시점을 점검해 드립니다.</p></div>")
    parts = ["<div class='hold'><h2>📒 내 보유 종목 점검</h2>"]
    order = {"sell": 0, "warn": 1, "take": 2, "hold": 3}
    for o in sorted(open_, key=lambda o: order.get(o.get("status", ("hold",))[0], 9) if o["ok"] else -1):
        if not o["ok"]:
            parts.append(f"<div class='pos'><div class='nm'>{escape(str(o['name']))} <small>{escape(o['ticker'])}</small></div>"
                         f"<p class='muted'>⚠️ {escape(o.get('error', ''))} — my_trades.csv 를 확인하세요.</p></div>")
            continue
        r = o["region"]
        st, label = o["status"]
        rc = "up" if o["ret"] > 0 else ("dn" if o["ret"] < 0 else "")
        pnl = ""
        if o["pnl"] is not None:
            pnl = f" · 평가손익 {'+' if o['pnl'] >= 0 else ''}{_p(o['pnl'], r)}"
        lv = (f"<div class='lv'><div><span>손절가 (−{C.STOP_LOSS_PCT:g}%)</span><b>{_p(o['stop'], r)}</b></div>"
              f"<div><span>1차 목표 (+{C.TAKE_PROFIT_PCT:g}%)</span><b>{_p(o['target'], r)}</b></div>"
              f"<div><span>추적 손절가</span><b>{_p(o['trail'], r) if o['trail'] else '목표 도달 후 작동'}</b></div>"
              f"<div><span>지지선 (매수 전 60일 저점)</span><b>{_p(o['support'], r)}</b></div></div>")
        prog = ""
        if o["best"]:
            pct = min(100, o["held"] / o["best"] * 100)
            prog = (f"<div class='muted'>보유 {o['held']}거래일 / 과거 기준 유리했던 보유 기간 {o['best']}거래일</div>"
                    f"<div class='prog'><i style='width:{pct:.0f}%'></i></div>")
        hz = ""
        if o["stats"] and o["stats"].get("horizons"):
            hs = o["stats"]["horizons"]
            hz = ("<table class='hz'><tr><th>같은 신호 과거 성과</th>"
                  + "".join(f"<th class='{'best' if k == o['best'] else ''}'>{k}일 뒤</th>" for k in hs) + "</tr><tr><th>이익 비율 · 중간 수익률</th>"
                  + "".join(f"<td class='{'best' if k == o['best'] else ''}'><b>{v['p_up']:.0f}%</b><span>{v['median']:+.1f}%</span></td>"
                            for k, v in hs.items()) + "</tr></table>")
        parts.append(f"""<div class='pos'><div class='top'>
<div><div class='nm'>{escape(str(o['name']))} <small>{escape(o['ticker'])}</small></div>
<div class='muted'>매수 {o['buy_date']} · {_p(o['buy_price'], r)} → 현재 {_p(o['close'], r)} ({o['last_date']} 종가){pnl}</div></div>
<div style='text-align:right'><div class='ret {rc}'>{o['ret']:+.2f}%</div><div class='badge {STATUS_CLS[st]}'>{STATUS_ICON[st]} {label}</div></div></div>
<ul>{''.join(f'<li>{escape(x)}</li>' for x in o['reasons'])}</ul>
<p class='links'><a href='#r-{escape(o['ticker'])}'>↓ 상세 분석 보기 (세력 이탈·차트·공시·수급)</a>{_dump_tag(o)}</p>
{lv}{prog}{hz}
{f"<p class='muted'>메모: {escape(str(o['memo']))}</p>" if o['memo'] else ''}</div>""")
    if closed:
        rows = "".join(
            f"<tr><td>{escape(str(c['name']))}</td><td>{c['buy_date']}</td><td class=n>{_p(c['buy'], c['region'])}</td>"
            f"<td>{c['sell_date']}</td><td class=n>{_p(c['sell'], c['region'])}</td>"
            f"<td class=n><b style='color:{'#d93025' if c['ret'] > 0 else '#1a73e8'}'>{c['ret']:+.2f}%</b></td>"
            f"<td class=n>{c['days'] if c['days'] is not None else '-'}일</td></tr>" for c in closed)
        wins = sum(1 for c in closed if c["ret"] > 0)
        avg = np.nanmean([c["ret"] for c in closed])
        parts.append(f"<h4>매도 완료 기록 <small>{len(closed)}건 · 이익 {wins}건 · 평균 {avg:+.2f}%</small></h4>"
                     "<table class='closed'><tr><th>종목</th><th>매수일</th><th>매수가</th><th>매도일</th><th>매도가</th>"
                     "<th>수익률</th><th>보유</th></tr>" + rows + "</table>")
    parts.append(f"<p class='muted'>판단 기준: 손절 −{C.STOP_LOSS_PCT:g}% · 목표 +{C.TAKE_PROFIT_PCT:g}%에서 일부 익절 · 이후 최고가 대비 "
                 f"−{C.TRAIL_PCT:g}% 하락 시 매도 · 매수 전 60일 저점 이탈 시 매도 · 대량 매도 출현 시 경고 · "
                 "유리했던 보유 기간이 지나도 수익이 미미하면 정리 고려. 숫자는 config.py에서 바꿀 수 있습니다.</p></div>")
    return "".join(parts)


def analyze_holdings(open_: list[dict], probs: dict, hist_lookup: dict | None = None,
                     marcaps: dict | None = None) -> str:
    """보유 종목마다 추천 종목과 같은 상세 분석 카드를 만들고, 세력 이탈 위험을 매도 판단에 반영"""
    import analysis as A
    import report as RP
    hist_lookup, marcaps = hist_lookup or {}, marcaps or {}
    cards, seen = [], set()
    for o in open_:
        if not o.get("ok") or o["ticker"] in seen:
            continue
        seen.add(o["ticker"])
        t, region = o["ticker"], o["region"]
        try:
            h = hist_lookup.get(t)
            if h is None or len(h) < 60:
                h = naver_daily(t.split(".")[0]) if region == "KR" else pd.DataFrame()
            if h is None or len(h) < 60:
                h = _download(t, C.HISTORY_PERIOD, adjust=True)
            if is_etf(o["name"]):
                o["dump"] = None
                o["etf"] = True
                o["reasons"].append("ETF는 여러 종목을 묶은 상품이라 세력 이탈·공시 분석 대신 손절·익절·추적 손절·지지선 "
                                    "규칙으로만 판단합니다.")
                cards.append(_etf_card(o, h))
                continue
            feat = A._features(h, region)
            last = feat.iloc[-1]
            row = pd.Series({"region": region, "ticker": t, "name": o["name"],
                             "market": {"KR": "KOSPI" if t.endswith(".KS") else "KOSDAQ"}.get(region, "US"),
                             "close": float(last["close"]), "return_1m_pct": float(last["ret_pct"]),
                             "vol_ratio": float(last["vol_ratio"]), "marcap": marcaps.get(t, float("nan"))})
            a = RP.analyze(row, feat, h, probs.get(region) or {})
            o["dump"] = a["dump"]
            lvl = a["dump"]["level"]
            if lvl == "high":
                if o["status"][0] != "sell":
                    o["status"] = ("sell" if o["ret"] < 0 else "warn", "매도 고려 (세력 이탈 위험 높음)")
                o["reasons"].insert(0, f"세력 이탈 위험도 {a['dump']['score']}/100 (높음) — 이 종목은 세력이 물량을 넘기고 빠진 "
                                       "흔적이 많습니다. 아래 상세 분석의 근거를 확인하세요.")
            elif lvl == "mid":
                o["reasons"].append(f"세력 이탈 위험도 {a['dump']['score']}/100 (중간) — 상세 분석에서 근거를 확인하세요.")
            cards.append(RP._card(a, h, "내 보유 종목"))
        except Exception as e:
            o["reasons"].append(f"상세 분석 실패: {e}")
    if not cards:
        return ""
    return "<h2>📒 내 보유 종목 상세 분석</h2>" + "".join(cards)


def _etf_card(o: dict, h: pd.DataFrame) -> str:
    import report as RP
    r = o["region"]
    tail = h.tail(60)
    levels = {"breakout": float(tail["High"].max()), "stop": float(tail["Low"].min()), "close": float(h["Close"].iloc[-1])}
    code = o["ticker"].split(".")[0]
    return f"""<section class="card" id="r-{escape(o['ticker'])}">
<div class="hd"><div><span class="tagx">내 보유 종목 · ETF</span><h3>{escape(str(o['name']))} <small>{escape(o['ticker'])}</small></h3>
<div class="meta">현재 {_p(o['close'], r)} · 매수가 대비 {o['ret']:+.2f}%</div></div></div>
<div class="verdict info">ℹ️ ETF는 세력 이탈·공시 분석 대상이 아닙니다. 위의 매도 규칙(손절·익절·추적 손절·지지선)으로 관리하세요.</div>
{RP.chart_svg(None, h, levels, r)}
<p class="links"><a href='https://finance.naver.com/item/main.naver?code={code}' target='_blank'>네이버 종목</a></p>
</section>"""


def standalone_page() -> Path:
    import report as RP
    probs = _stats_cache()
    open_, closed = check_all(probs)
    detail = analyze_holdings(open_, probs)
    RESULTS.mkdir(exist_ok=True)
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>내 보유 종목 점검</title><style>{RP.CSS}{HOLD_CSS}</style></head><body>
<h1>내 보유 종목 점검 — {datetime.now():%Y-%m-%d %H:%M}</h1>{holdings_html(open_, closed)}{detail}</body></html>"""
    out = RESULTS / "holdings.html"
    out.write_text(html, encoding="utf-8")
    return out


# ════════════════════════════════════════════════════════════
# 메뉴 (trades.bat)
# ════════════════════════════════════════════════════════════
def _ask(msg: str, default: str = "") -> str:
    v = input(msg).strip()
    return v or default


def add_buy() -> None:
    print("\n── 매수 기록 추가 ──")
    code = _ask("종목코드 (예: 005930 / 미국은 AAPL): ").upper()
    if not code:
        return
    print("  확인 중...")
    ticker, h = resolve(code)
    if not len(h):
        print(f"  ⚠️ {code} 시세를 찾지 못했습니다. 코드를 확인하세요.")
        return
    region = "KR" if ticker.endswith((".KS", ".KQ")) else "US"
    name = _names().get(ticker, "")
    name = _ask(f"종목명 (엔터 = {name or '건너뛰기'}): ", name)
    d = _ask(f"매수일 (예: {date.today()}, 엔터 = 오늘): ", str(date.today()))
    if not _date(d):
        print("  ⚠️ 날짜 형식이 잘못됐습니다 (예: 2026-10-14)")
        return
    last = float(h["Close"].iloc[-1])
    p = _ask(f"매수가 (엔터 = 최근 종가 {_p(last, region)}): ", str(last))
    if _num(p) != _num(p):
        print("  ⚠️ 가격은 숫자로 입력하세요")
        return
    q = _ask("수량 (선택, 엔터 = 건너뛰기): ")
    memo = _ask("메모 (선택): ")
    df = load_trades()
    df.loc[len(df)] = [code, name, str(_date(d)), _fmt_num(_num(p)), q, "", "", memo]
    save_trades(df)
    print(f"  ✅ 저장했습니다: {name or code} {_date(d)} {_p(_num(p), region)}")


def record_sell() -> None:
    df = load_trades()
    opens = df[(df["매도일"].str.strip() == "") & (df["종목코드"].str.strip() != "")]
    if not len(opens):
        print("\n보유 중인 기록이 없습니다.")
        return
    print("\n── 매도 기록 ──")
    for n, (i, r) in enumerate(opens.iterrows(), 1):
        print(f"  {n}. {r['종목명'] or r['종목코드']} ({r['종목코드']}) 매수 {r['매수일']} {r['매수가']}")
    k = _ask("매도한 번호: ")
    if not k.isdigit() or not 1 <= int(k) <= len(opens):
        return
    i = opens.index[int(k) - 1]
    d = _ask(f"매도일 (엔터 = 오늘 {date.today()}): ", str(date.today()))
    p = _ask("매도가: ")
    if not _date(d) or _num(p) != _num(p):
        print("  ⚠️ 날짜나 가격 형식이 잘못됐습니다")
        return
    df.loc[i, "매도일"], df.loc[i, "매도가"] = str(_date(d)), _fmt_num(_num(p))
    save_trades(df)
    bp = _num(df.loc[i, "매수가"])
    print(f"  ✅ 기록했습니다. 수익률 {(_num(p) / bp - 1) * 100:+.2f}%")


def show_list() -> None:
    df = load_trades()
    if not len(df):
        print("\n기록이 없습니다.")
        return
    print()
    for _, r in df.iterrows():
        state = f"매도 {r['매도일']} {r['매도가']}" if r["매도일"] else "보유 중"
        print(f"  · {r['종목명'] or r['종목코드']} ({r['종목코드']}) 매수 {r['매수일']} {r['매수가']}  [{state}]")
    print(f"\n  기록 파일: {TRADES_FILE}  (엑셀로 열어서 고칠 수 있습니다)")


def open_excel() -> None:
    import os
    if not TRADES_FILE.exists():
        save_trades(pd.DataFrame(columns=COLS))
    print(f"""
  엑셀로 기록 파일을 엽니다: {TRADES_FILE.name}
  ─ 한 줄에 한 종목씩 적으세요 ─
    종목코드 : 005930 (한국 6자리) / AAPL (미국)
    종목명   : 삼성전자 (비워도 됨)
    매수일   : 2026-10-14
    매수가   : 58000  (쉼표·원 없이 숫자만, 미국은 달러 12.5)
    수량     : 10 (비워도 됨)
    매도일·매도가 : 아직 보유 중이면 비워두기
    메모     : 자유
  ※ 같은 종목을 여러 번 나눠 샀으면 평균 단가로 한 줄, 또는 매수할 때마다 한 줄씩 적어도 됩니다.
  ※ 다 적은 뒤 '저장'할 때 형식을 묻는 창이 뜨면 → [예] (CSV 형식 유지)를 누르세요.
  ※ 엑셀이 005930을 5930으로 바꿔 보여줘도 괜찮습니다. 프로그램이 알아서 6자리로 맞춥니다.""")
    try:
        os.startfile(TRADES_FILE)   # Windows: 엑셀(또는 기본 프로그램)로 열기
    except Exception:
        print("  파일을 직접 여세요.")
    input("\n  엑셀에서 저장하고 닫은 뒤 엔터를 누르세요...")
    show_list()


def menu() -> None:
    import os
    while True:
        print("\n========== 내 매매 기록 ==========")
        print("  1. 매수 기록 추가")
        print("  2. 매도 기록 (판 종목 표시)")
        print("  3. 기록 목록 보기")
        print("  4. 지금 보유 종목 점검 (매도 시점 + 상세 분석)")
        print("  5. 엑셀로 여러 종목 한 번에 입력")
        print("  0. 끝내기")
        k = _ask("번호 선택: ")
        if k == "1":
            add_buy()
        elif k == "2":
            record_sell()
        elif k == "3":
            show_list()
        elif k == "4":
            print("  점검 중... (1분 정도)")
            out = standalone_page()
            print(f"  ✅ 결과를 엽니다: {out}")
            try:
                os.startfile(out)   # Windows
            except Exception:
                pass
        elif k == "5":
            open_excel()
        elif k == "0" or k == "":
            break


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--check" in sys.argv:
        print(standalone_page())
    else:
        menu()
