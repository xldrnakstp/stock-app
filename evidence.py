"""
종목별 외부 확인 자료: 공시(DART/SEC), 수급(네이버), 공매도·기관 보유(야후)
모든 함수는 실패해도 프로그램을 멈추지 않고 {"ok": False, "error": ...}를 돌려줍니다.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

import config as C

BASE = Path(__file__).parent
CACHE = BASE / "cache"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 공시 분류: event = 주가가 특정 가격에 묶이는 이벤트, risk = 매도 물량·악재 가능성, good = 호재성, info = 참고
DART_RULES = [
    ("event", ["공개매수", "합병", "주식교환", "주식이전", "상장폐지", "영업양수", "영업양도", "분할"]),
    ("risk", ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "전환청구권", "신주인수권행사", "교환청구권",
              "추가상장", "최대주주변경", "감자", "관리종목", "불성실공시", "회생", "감사의견", "횡령", "배임",
              "소송", "보호예수", "의무보유", "자기주식처분"]),
    ("good", ["단일판매", "공급계약", "자기주식취득", "자기주식 취득", "자기주식소각", "무상증자", "현금ㆍ현물배당",
              "현금배당", "특허", "신규시설투자"]),
    ("info", ["대량보유", "특정증권등소유상황", "최대주주등소유주식변동"]),
]
SEC_RULES = [
    ("event", ["SC TO-T", "SC TO-I", "SC TO-C", "SC 14D9", "SCHEDULE TO", "DEFM14A", "PREM14A", "DEFM14C",
               "425", "SC 13E3", "SCHEDULE 13E3", "25-NSE", "15-12B", "15-12G"]),
    ("risk", ["S-1", "S-3", "F-1", "F-3", "424B", "144", "NT 10-K", "NT 10-Q"]),
    ("info", ["SC 13D", "SCHEDULE 13D", "8-K", "6-K", "SC 13G", "SCHEDULE 13G"]),
]
LABEL = {"event": "가격고정 이벤트", "risk": "물량·악재 주의", "good": "호재성", "info": "참고"}


def _classify(title: str, rules) -> str | None:
    t = title.replace(" ", "")
    for kind, words in rules:
        for w in words:
            if w.replace(" ", "") in t:
                return kind
    return None


def _cached_json(path: Path, max_age_days: float, fetch):
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ════════════════════════════════════════════════════════════
# 한국: DART 공시
# ════════════════════════════════════════════════════════════
def dart_key() -> str | None:
    k = os.getenv("DART_API_KEY")
    f = BASE / "dart_key.txt"
    if not k and f.exists():
        k = f.read_text(encoding="utf-8-sig").strip()
    return k or None


def _dart_corp_map(key: str) -> dict:
    def fetch():
        r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=60)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml = z.read(z.namelist()[0]).decode("utf-8")
        pairs = re.findall(r"<corp_code>(\d+)</corp_code>.*?<stock_code>\s*(\d{6})\s*</stock_code>", xml, re.S)
        return {stock: corp for corp, stock in pairs}
    return _cached_json(CACHE / "dart_corp.json", 7, fetch)


def kr_disclosures(code: str) -> dict:
    key = dart_key()
    if not key:
        return {"ok": False, "error": "DART API 키 미설정 (dart_key.txt)", "items": []}
    try:
        corp = _dart_corp_map(key).get(code)
        if not corp:
            return {"ok": False, "error": "DART 고유번호 없음", "items": []}
        bgn = (datetime.now() - timedelta(days=C.DISCLOSURE_DAYS)).strftime("%Y%m%d")
        r = requests.get("https://opendart.fss.or.kr/api/list.json",
                         params={"crtfc_key": key, "corp_code": corp, "bgn_de": bgn, "page_count": 100},
                         timeout=30).json()
        if r.get("status") not in ("000", "013"):     # 013 = 조회된 데이터 없음
            return {"ok": False, "error": f"DART 오류: {r.get('message')}", "items": []}
        items = []
        for it in r.get("list", []):
            title = it.get("report_nm", "").strip()
            items.append({"date": f"{it['rcept_dt'][:4]}-{it['rcept_dt'][4:6]}-{it['rcept_dt'][6:]}",
                          "title": title, "kind": _classify(title, DART_RULES),
                          "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}"})
        return {"ok": True, "items": items}
    except Exception as e:
        return {"ok": False, "error": "DART 접속 실패", "items": []}


# ════════════════════════════════════════════════════════════
# 미국: SEC EDGAR 공시
# ════════════════════════════════════════════════════════════
def _sec_get(url: str):
    r = requests.get(url, headers={"User-Agent": C.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    return r.json()


def us_disclosures(ticker: str) -> dict:
    try:
        cik_map = _cached_json(CACHE / "sec_tickers.json", 7, lambda: {
            v["ticker"].upper(): int(v["cik_str"])
            for v in _sec_get("https://www.sec.gov/files/company_tickers.json").values()})
        cik = cik_map.get(ticker.upper()) or cik_map.get(ticker.upper().replace("-", "."))
        if not cik:
            return {"ok": False, "error": "SEC 등록번호(CIK) 없음", "items": []}
        rec = _sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")["filings"]["recent"]
        since = (datetime.now() - timedelta(days=C.DISCLOSURE_DAYS)).strftime("%Y-%m-%d")
        items, insider = [], 0
        for form, date, acc, doc, desc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"],
                                              rec["primaryDocument"], rec.get("primaryDocDescription", [""] * len(rec["form"]))):
            if date < since:
                break
            if form in ("4", "4/A"):
                insider += 1
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            items.append({"date": date, "title": f"{form} {('— ' + desc) if desc else ''}".strip(),
                          "kind": _classify(form, SEC_RULES), "url": url})
        return {"ok": True, "items": items, "insider_form4": insider, "cik": cik}
    except Exception as e:
        return {"ok": False, "error": "SEC 접속 실패", "items": []}


# ════════════════════════════════════════════════════════════
# 한국: 외국인·기관 수급 (네이버 증권)
# ════════════════════════════════════════════════════════════
def _to_num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except ValueError:
        return float("nan")


def kr_investor_flow(code: str) -> dict:
    """최근 5일·20일 외국인/기관 순매매(주). 개인은 그 반대로 추정."""
    try:
        frames = []
        for page in (1, 2):
            html = requests.get(f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}",
                                headers=UA, timeout=20).content.decode("euc-kr", "replace")
            for t in pd.read_html(io.StringIO(html)):
                cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
                inst = next((i for i, c in enumerate(cols) if "기관" in c and "순매매" in c), None)
                frgn = next((i for i, c in enumerate(cols) if "외국인" in c and "순매매" in c), None)
                vol = next((i for i, c in enumerate(cols) if "거래량" in c), None)
                if inst is None or frgn is None:
                    continue
                t = t[t.iloc[:, 0].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}")]
                frames.append(pd.DataFrame({
                    "date": t.iloc[:, 0].astype(str).values,
                    "inst": [_to_num(x) for x in t.iloc[:, inst]],
                    "frgn": [_to_num(x) for x in t.iloc[:, frgn]],
                    "vol": [_to_num(x) for x in t.iloc[:, vol]] if vol is not None else float("nan"),
                }))
                break
            if frames and sum(len(f) for f in frames) >= 20:
                break
            time.sleep(0.2)
        if not frames:
            return {"ok": False, "error": "수급 표를 찾지 못함"}
        df = pd.concat(frames).drop_duplicates("date").head(20)

        def window(n):
            w = df.head(n)
            i, f, vol = w["inst"].sum(), w["frgn"].sum(), w["vol"].sum()
            return {"days": len(w), "institution": i, "foreign": f, "individual_est": -(i + f), "volume": vol}
        return {"ok": True, "d5": window(C.RECENT_DAYS), "d20": window(20)}
    except Exception:
        return {"ok": False, "error": "네이버 수급 정보 접속 실패"}


# ════════════════════════════════════════════════════════════
# 미국: 공매도 비율·기관 보유 (야후)
# ════════════════════════════════════════════════════════════
def us_ownership(ticker: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        g = lambda k: info.get(k) * 100 if isinstance(info.get(k), (int, float)) else None
        return {"ok": True, "short_pct_float": g("shortPercentOfFloat"),
                "inst_pct": g("heldPercentInstitutions"), "insider_pct": g("heldPercentInsiders")}
    except Exception as e:
        return {"ok": False, "error": "야후 정보 접속 실패"}


def summarize_disclosures(items: list[dict]) -> dict:
    return {k: [i for i in items if i["kind"] == k] for k in ("event", "risk", "good", "info")}
