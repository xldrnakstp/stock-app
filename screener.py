"""
거래량 급증 + 주가 정체 종목 스크리너 (한국·미국)

조건 (config.py에서 변경 가능)
  1) 최근 5거래일 평균 거래량 >= 직전 20거래일 평균 거래량 x 3
  2) 최근 한 달(20거래일) 주가 수익률 0% ~ +3%
  3) 잡주 제외: 시가총액·주가·거래대금 하한

실행:  python screener.py            (한국+미국)
       python screener.py --market KR
"""
from __future__ import annotations

import argparse
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import requests

import config as C

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
RESULTS_DIR = Path(__file__).parent / "results"


def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


# ════════════════════════════════════════════════════════════
# 1. 종목 목록 (시가총액 필터까지)
# ════════════════════════════════════════════════════════════
KR_EXCLUDE_NAME = re.compile(r"스팩|SPAC|리츠|REIT|ETN|^(?:KODEX|TIGER|KBSTAR|RISE|ACE|SOL|HANARO|ARIRANG|KOSEF|PLUS)\b")


def _num(s) -> pd.Series:
    """'1,234' 같은 문자열도 숫자로"""
    return pd.to_numeric(pd.Series(s).astype(str).str.replace(",", "").str.strip(), errors="coerce")


def _fix_marcap_unit(cap: pd.Series) -> pd.Series:
    """시총 단위 자동 보정: 삼성전자급 최대값이 수백조 원(1e14~1e16) 범위가 되도록"""
    mx = cap.max()
    if pd.notna(mx) and mx > 0:
        if mx < 1e11:        # 억 원 단위로 들어온 경우
            cap = cap * 1e8
        elif mx < 1e14:      # 백만 원 단위
            cap = cap * 1e6
    return cap


def kr_universe() -> pd.DataFrame:
    """코스피·코스닥 종목. 반환 컬럼: ticker, name, market, marcap"""
    df = None
    try:
        df = _kr_universe_fdr()
    except Exception as e:
        log(f"KR 목록 FinanceDataReader 실패({e})")
    if df is None or df["marcap"].notna().sum() < 500:
        log("KR 시가총액 정보 부족 → 네이버 금융으로 대체")
        df = _kr_universe_naver()

    df["marcap"] = _fix_marcap_unit(df["marcap"])
    df = df[~df["name"].astype(str).str.contains(KR_EXCLUDE_NAME, na=False)]
    df = df[df["marcap"] >= C.KR_MIN_MARCAP_KRW]
    df["ticker"] = df["code"] + df["market"].map({"KOSPI": ".KS", "KOSDAQ": ".KQ"})
    df = df.dropna(subset=["ticker"])
    log(f"KR 시총 필터 후 {len(df)}개")
    return df[["ticker", "name", "market", "marcap"]].reset_index(drop=True)


def _kr_universe_fdr() -> pd.DataFrame:
    import FinanceDataReader as fdr
    raw = fdr.StockListing("KRX").reset_index(drop=True)
    log(f"KR 목록: FinanceDataReader {len(raw)}개, 컬럼 {list(raw.columns)}")
    mkt = raw["Market"].astype(str).str.upper()
    market = pd.Series(None, index=raw.index, dtype=object)
    market[mkt.str.contains("KOSDAQ")] = "KOSDAQ"
    market[mkt.str.contains("KOSPI")] = "KOSPI"

    cap = _num(raw["Marcap"]) if "Marcap" in raw else pd.Series(float("nan"), index=raw.index)
    if cap.notna().sum() < 500 and {"Stocks", "Close"} <= set(raw.columns):
        cap = _num(raw["Stocks"]) * _num(raw["Close"])      # 상장주식수 × 종가
    df = pd.DataFrame({
        "code": raw["Code"].astype(str).str.zfill(6),
        "name": raw["Name"].astype(str),
        "market": market,
        "marcap": cap.values,
    }).dropna(subset=["market"])
    log(f"KR 시가총액 확인된 종목 {df['marcap'].notna().sum()}개")
    return df


def _kr_universe_naver() -> pd.DataFrame:
    """네이버 증권(모바일) 시가총액 순위 — 일반 주식만"""
    rows = []
    for market in ["KOSPI", "KOSDAQ"]:
        page = 1
        while page <= 60:
            r = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                             params={"page": page, "pageSize": 100}, headers=UA, timeout=20)
            items = r.json().get("stocks", [])
            if not items:
                break
            for it in items:
                if str(it.get("stockEndType", "stock")).lower() != "stock":
                    continue
                cap = _num([it.get("marketValue")]).iloc[0]
                rows.append((str(it.get("itemCode")).zfill(6), it.get("stockName", ""), market, cap))
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.2)
    log(f"KR 목록: 네이버 {len(rows)}개")
    return pd.DataFrame(rows, columns=["code", "name", "market", "marcap"])


US_EXCLUDE_NAME = re.compile(r"Warrant|Right|Unit|Preferred|Depositary Shares?,? .*Series|Notes due|Debenture|Acquisition Corp|Acquisition Co\b|Merger Corp|Blank Check|SPAC", re.I)


def us_universe() -> pd.DataFrame:
    """NYSE·NASDAQ·AMEX 종목. 반환 컬럼: ticker, name, market, marcap"""
    frames = []
    try:
        r = requests.get("https://api.nasdaq.com/api/screener/stocks",
                         params={"tableonly": "true", "download": "true"},
                         headers={**UA, "Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        raw = pd.DataFrame(r.json()["data"]["rows"])
        raw["exchange"] = "US"
        frames.append(raw)
        log(f"US 목록: Nasdaq API {len(raw)}개")
    except Exception as e:
        log(f"US 목록 Nasdaq API 실패({e}) → GitHub 미러로 대체")
        base = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main"
        for ex in ["nasdaq", "nyse", "amex"]:
            raw = pd.DataFrame(requests.get(f"{base}/{ex}/{ex}_full_tickers.json", timeout=30).json())
            raw["exchange"] = ex.upper()
            frames.append(raw)
    raw = pd.concat(frames, ignore_index=True)

    df = pd.DataFrame({
        "symbol": raw["symbol"].astype(str).str.strip(),
        "name": raw["name"].astype(str),
        "market": raw["exchange"],
        "marcap": pd.to_numeric(raw["marketCap"].astype(str).str.replace(",", ""), errors="coerce"),
    })
    df = df[df["symbol"].str.fullmatch(r"[A-Z]+([./][A-Z])?")]      # 우선주·워런트 기호 제외
    df = df[~df["name"].str.contains(US_EXCLUDE_NAME, na=False)]
    df = df[df["marcap"] >= C.US_MIN_MARCAP_USD]
    df["ticker"] = df["symbol"].str.replace(r"[./]", "-", regex=True)  # BRK/B → BRK-B (야후 표기)
    df = df.drop_duplicates("ticker")
    log(f"US 시총 필터 후 {len(df)}개")
    return df[["ticker", "name", "market", "marcap"]].reset_index(drop=True)


# ════════════════════════════════════════════════════════════
# 2. 시세 수집 (야후 파이낸스, 수정주가, 2년치 일봉)
# ════════════════════════════════════════════════════════════
def download_history(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), C.DOWNLOAD_CHUNK):
        chunk = tickers[i:i + C.DOWNLOAD_CHUNK]
        for attempt in range(3):
            try:
                data = yf.download(chunk, period=C.HISTORY_PERIOD, interval="1d",
                                   group_by="ticker", auto_adjust=True, threads=True,
                                   progress=False)
                break
            except Exception as e:
                log(f"  다운로드 재시도 {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for t in chunk:
            try:
                d = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                d = d[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(d):
                    out[t] = d
            except KeyError:
                pass
        log(f"  시세 {min(i + C.DOWNLOAD_CHUNK, len(tickers))}/{len(tickers)}")
        time.sleep(1)
    return out


# ════════════════════════════════════════════════════════════
# 3. 조건 판정 (순수 함수 — 테스트 가능)
# ════════════════════════════════════════════════════════════
def evaluate(hist: pd.DataFrame) -> dict | None:
    """한 종목의 일봉(Close, Volume)으로 지표 계산. 데이터 부족 시 None."""
    need = max(C.RECENT_DAYS + C.BASE_DAYS, C.PRICE_LOOKBACK_DAYS + 1)
    h = hist[hist["Volume"] > 0]          # 거래정지일 제외
    if len(h) < need:
        return None
    vol, close = h["Volume"], h["Close"]
    recent = vol.iloc[-C.RECENT_DAYS:]
    base = vol.iloc[-(C.RECENT_DAYS + C.BASE_DAYS):-C.RECENT_DAYS]
    base_close = close.iloc[-(C.RECENT_DAYS + C.BASE_DAYS):-C.RECENT_DAYS]
    if base.mean() <= 0:
        return None
    return {
        "last_date": h.index[-1].date(),
        "close": float(close.iloc[-1]),
        "recent_vol_avg": float(recent.mean()),
        "base_vol_avg": float(base.mean()),
        "vol_ratio": float(recent.mean() / base.mean()),
        "return_1m_pct": float((close.iloc[-1] / close.iloc[-(C.PRICE_LOOKBACK_DAYS + 1)] - 1) * 100),
        "base_turnover": float((base * base_close).mean()),
    }


def price_in_range(m: dict) -> bool:
    ret = round(m["return_1m_pct"], 6)          # 부동소수점 오차로 경계값(3.00%)이 빠지지 않게
    return C.MIN_RETURN_PCT <= ret <= C.MAX_RETURN_PCT


def passes_base(m: dict, region: str) -> bool:
    """잡주 필터(주가·거래대금)"""
    min_price = C.KR_MIN_PRICE if region == "KR" else C.US_MIN_PRICE
    min_turn = C.KR_MIN_BASE_TURNOVER_KRW if region == "KR" else C.US_MIN_BASE_TURNOVER_USD
    return m["close"] >= min_price and m["base_turnover"] >= min_turn


def passes(m: dict, region: str) -> bool:
    min_price = C.KR_MIN_PRICE if region == "KR" else C.US_MIN_PRICE
    min_turn = C.KR_MIN_BASE_TURNOVER_KRW if region == "KR" else C.US_MIN_BASE_TURNOVER_USD
    ret = round(m["return_1m_pct"], 6)          # 부동소수점 오차로 경계값(3.00%)이 빠지지 않게
    return (round(m["vol_ratio"], 6) >= C.VOLUME_MULTIPLE
            and C.MIN_RETURN_PCT <= ret <= C.MAX_RETURN_PCT
            and m["close"] >= min_price
            and m["base_turnover"] >= min_turn)


def screen(region: str, universe: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """반환: (조건 충족 종목, 참고용 근접 종목 상위 10)"""
    metrics = {t: evaluate(h) for t, h in histories.items()}
    metrics = {t: m for t, m in metrics.items() if m}
    if not metrics:
        log(f"{region}: 계산 가능한 시세가 없습니다")
        return pd.DataFrame(), pd.DataFrame()
    # 마지막 거래일이 시장 최신일보다 오래된 종목(거래정지 등) 제외
    latest = max(m["last_date"] for m in metrics.values())
    rows = []
    for _, u in universe.iterrows():
        m = metrics.get(u["ticker"])
        if m and m["last_date"] >= latest - timedelta(days=3) and passes_base(m, region):
            rows.append({"region": region, **u.to_dict(), **m,
                         "hit": passes(m, region), "in_range": price_in_range(m)})
    df = pd.DataFrame(rows)
    if not len(df):
        return df, df
    n_vol = int((df["vol_ratio"].round(6) >= C.VOLUME_MULTIPLE).sum())
    log(f"{region}: 기준일 {latest} · 계산 {len(metrics)}개 · 거래량 {C.VOLUME_MULTIPLE:g}배↑ {n_vol}개 · "
        f"수익률 범위 내 {int(df['in_range'].sum())}개 · 둘 다 충족 {int(df['hit'].sum())}개")
    hits = df[df["hit"]].sort_values("vol_ratio", ascending=False)
    near = df[~df["hit"] & df["in_range"]].sort_values("vol_ratio", ascending=False).head(10)
    return hits, near


# ════════════════════════════════════════════════════════════
# 4. 결과 저장 · 알림
# ════════════════════════════════════════════════════════════
def fmt_cap(v: float, region: str) -> str:
    return f"{v / 1e8:,.0f}억" if region == "KR" else f"${v / 1e9:,.2f}B"


def _fmt_price(v: float, region: str) -> str:
    return f"{v:,.0f}원" if region == "KR" else f"${v:,.2f}"


def _part(df: pd.DataFrame, region: str) -> pd.DataFrame:
    return df[df["region"] == region] if len(df) and "region" in df else df.iloc[0:0]


def _cond_text() -> str:
    return (f"최근 {C.RECENT_DAYS}일 평균 거래량 ≥ 직전 {C.BASE_DAYS}일 평균 × {C.VOLUME_MULTIPLE:g} · "
            f"최근 {C.PRICE_LOOKBACK_DAYS}거래일 수익률 {C.MIN_RETURN_PCT:g}% ~ +{C.MAX_RETURN_PCT:g}%")


REGIONS = [("KR", "🇰🇷 한국"), ("US", "🇺🇸 미국")]


def to_markdown(result: pd.DataFrame, near: pd.DataFrame, run_date: str) -> str:
    def table(part, region):
        out = ["| 종목 | 코드 | 시장 | 종가 | 1개월 수익률 | 거래량 배수 | 시가총액 |",
               "|---|---|---|---:|---:|---:|---:|"]
        for _, r in part.iterrows():
            out.append(f"| {r['name']} | {r['ticker']} | {r['market']} | {_fmt_price(r['close'], region)} | "
                       f"{r['return_1m_pct']:+.2f}% | {r['vol_ratio']:.1f}배 | {fmt_cap(r['marcap'], region)} |")
        return out + [""]

    lines = [f"# 거래량 급증·주가 정체 종목 — {run_date}", "", f"조건: {_cond_text()}", ""]
    for region, title in REGIONS:
        part = _part(result, region)
        lines += [f"## {title} ({len(part)}종목)", ""]
        lines += table(part, region) if len(part) else ["조건에 맞는 종목 없음", ""]
        np_ = _part(near, region)
        if len(np_):
            lines += [f"### 참고: 수익률 조건은 맞고 거래량 배수가 높은 순 {len(np_)}종목", ""] + table(np_, region)
    return "\n".join(lines)


def to_html(result: pd.DataFrame, near: pd.DataFrame, run_date: str) -> str:
    """브라우저로 바로 열어보는 결과 페이지"""
    from html import escape

    def table(part, region, cls=""):
        rows = []
        for _, r in part.iterrows():
            code = r["ticker"].split(".")[0]
            link = (f"https://finance.naver.com/item/main.naver?code={code}" if region == "KR"
                    else f"https://finance.yahoo.com/quote/{r['ticker']}")
            rows.append(f"<tr><td><a href='{link}' target='_blank'>{escape(str(r['name']))}</a></td>"
                        f"<td>{escape(r['ticker'])}</td><td>{escape(str(r['market']))}</td>"
                        f"<td class=n>{_fmt_price(r['close'], region)}</td><td class=n>{r['return_1m_pct']:+.2f}%</td>"
                        f"<td class=n><b>{r['vol_ratio']:.1f}배</b></td><td class=n>{fmt_cap(r['marcap'], region)}</td></tr>")
        return (f"<table class='{cls}'><tr><th>종목</th><th>코드</th><th>시장</th><th>종가</th><th>1개월 수익률</th>"
                "<th>거래량 배수</th><th>시가총액</th></tr>" + "".join(rows) + "</table>")

    parts = []
    for region, title in REGIONS:
        part = _part(result, region)
        parts.append(f"<h2>{title} <small>{len(part)}종목</small></h2>")
        parts.append(table(part, region) if len(part) else "<p class='none'>오늘은 조건에 맞는 종목이 없습니다</p>")
        np_ = _part(near, region)
        if len(np_):
            parts.append(f"<h3>참고: 수익률 조건은 맞고 거래량 배수가 높은 순 <small>(거래량 {C.VOLUME_MULTIPLE:g}배 미달)</small></h3>")
            parts.append(table(np_, region, "near"))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>거래량 급증 종목 {run_date}</title><style>
body{{font-family:'Malgun Gothic',sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:22px}} h2{{margin-top:32px;font-size:19px}} h3{{margin-top:18px;font-size:15px;color:#555}}
small{{color:#888;font-weight:normal}}
.cond{{color:#555;background:#f4f4f4;padding:8px 12px;border-radius:6px}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:7px 10px;border-bottom:1px solid #e5e5e5;text-align:left}}
th{{background:#fafafa}} .n{{text-align:right}} a{{color:#0b57d0;text-decoration:none}} .none{{color:#888}}
table.near{{color:#666;font-size:14px}}
</style></head><body><h1>거래량 급증 · 주가 정체 종목 — {run_date}</h1>
<p class="cond">{_cond_text()}</p>{''.join(parts)}
<p class="none">종목명을 누르면 시세 페이지가 열립니다. 투자 판단의 참고용입니다.</p></body></html>"""


def send_email(subject: str, html_body: str, csv_path: Path) -> None:
    user, pw, to = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"), os.getenv("MAIL_TO")
    if not (user and pw and to):
        log("이메일 설정 없음 → 발송 생략")
        return
    msg = MIMEMultipart()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    from email.mime.application import MIMEApplication
    att = MIMEApplication(csv_path.read_bytes(), Name=csv_path.name)
    att["Content-Disposition"] = f'attachment; filename="{csv_path.name}"'
    msg.attach(att)
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), 465) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    log(f"이메일 발송 완료 → {to}")


def run_screen(market: str = "ALL") -> dict:
    """스크리닝 + 종목 분석. 파일 저장은 하지 않고 결과만 돌려줌 (PC·클라우드 공용)"""
    import analysis as A
    import report as RP

    run_date = datetime.now(KST).strftime("%Y-%m-%d")
    results, nears, sections = [], [], []
    names, stats_by_region, probs_by_region, all_hist, marcaps = {}, {}, {}, {}, {}
    for region, title, uni_fn in [("KR", "🇰🇷 한국", kr_universe), ("US", "🇺🇸 미국", us_universe)]:
        if market not in (region, "ALL"):
            continue
        hits = near = pd.DataFrame()
        cards, stats = [], None
        try:
            uni = uni_fn()
            names.update(dict(zip(uni["ticker"], uni["name"].astype(str))))
            marcaps.update(dict(zip(uni["ticker"], uni["marcap"])))
            hist = download_history(uni["ticker"].tolist())
            all_hist.update(hist)
            hits, near = screen(region, uni, hist)

            # 과거 신호 통계 (상승 확률)
            feats = A.build_feature_tables(hist, region)
            past = A.collect_past_signals(feats)
            prob = A.probability_table(past)
            stats = prob["all"]
            stats_by_region[region] = stats
            probs_by_region[region] = prob
            log(f"{region}: 과거 2년 신호 {len(past)}건으로 확률 계산")

            # 리포트 대상: 조건 충족 종목 전부 (없으면 참고 종목 상위 몇 개)
            targets = [(r, "조건 충족") for _, r in hits.iterrows()]
            if not targets:
                targets = [(r, "참고 (거래량 미달)") for _, r in near.head(C.REPORT_NEAR_COUNT).iterrows()]
            prob_txt, dump_txt, stars = {}, {}, set()
            for r, tag in targets:
                t = r["ticker"]
                if t not in feats:
                    continue
                log(f"  분석: {r['name']} ({t})")
                try:
                    a = RP.analyze(r, feats[t], hist[t], prob)
                    if tag != "조건 충족":   # 참고 종목은 신호 자체가 아니므로 후보에서 제외
                        a["candidate"] = False
                        a["cand"].append((False, f"거래량 {C.VOLUME_MULTIPLE:g}배 조건 충족", "참고 종목(거래량 미달)"))
                    cards.append(RP._card(a, hist[t], tag))
                    if a["p"]:
                        prob_txt[t] = f"{a['p']['p_up']:.0f}%"
                    dump_txt[t] = f"{RP.DUMP_TXT[a['dump']['level']]} {a['dump']['score']}"
                    if a["candidate"]:
                        stars.add(t)
                except Exception as e:
                    log(f"  분석 실패 {t}: {e}")
            # 요약표에 확률 표시 (분석한 종목만)
            for df in (hits, near):
                if len(df):
                    df["prob_txt"] = df["ticker"].map(prob_txt).fillna("")
                    df["dump_txt"] = df["ticker"].map(dump_txt).fillna("")
                    df["cand_star"] = df["ticker"].isin(stars)
        except Exception as e:
            log(f"{region} 처리 실패: {e}")
        results.append(hits)
        nears.append(near)
        sections.append({"region": region, "title": title, "hits": hits, "near": near,
                         "cards": cards, "stats": stats})

    return {"run_date": run_date, "sections": sections, "results": results, "nears": nears, "names": names,
            "probs": probs_by_region, "hist": all_hist, "marcaps": marcaps}


def main() -> int:
    # Windows 콘솔(cp949)에서 이모지·한글 출력 오류 방지
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    args = ap.parse_args()

    import report as RP
    r = run_screen(args.market)
    run_date, sections, results, nears = r["run_date"], r["sections"], r["results"], r["nears"]
    names, probs_by_region, all_hist, marcaps = r["names"], r["probs"], r["hist"], r["marcaps"]

    def combine(frames):
        frames = [f for f in frames if len(f)]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["region"])
    result, near = combine(results), combine(nears)
    result = result.drop(columns=["hit", "in_range", "prob_txt", "dump_txt", "cand_star"], errors="ignore")

    RESULTS_DIR.mkdir(exist_ok=True)
    csv_path = RESULTS_DIR / f"{run_date}.csv"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")   # 엑셀에서 한글 깨짐 방지
    md = to_markdown(result, near, run_date)
    (RESULTS_DIR / "latest.md").write_text(md, encoding="utf-8")
    # 내 보유 종목 점검 (trades.bat 으로 입력한 기록)
    import trades as TR
    top = ""
    try:
        TR.save_caches(names, probs_by_region)
        o, c = TR.check_all(probs_by_region)
        detail = TR.analyze_holdings(o, probs_by_region, all_hist, marcaps)
        top = TR.holdings_html(o, c) + detail
        log(f"보유 종목 점검: 보유 {len(o)}건, 매도 완료 {len(c)}건")
    except Exception as e:
        log(f"보유 종목 점검 실패: {e}")
    page = RP.build_page(run_date, sections, _cond_text(), top)
    (RESULTS_DIR / "latest.html").write_text(page, encoding="utf-8")
    (RESULTS_DIR / f"{run_date}.html").write_text(page, encoding="utf-8")
    log(f"저장: {csv_path}")
    print(md)

    send_email(f"[스크리너] {run_date} 거래량 급증·주가 정체 {len(result)}종목", page, csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
