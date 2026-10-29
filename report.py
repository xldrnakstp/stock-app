"""종목별 분석 리포트 생성 (HTML)"""
from __future__ import annotations

from html import escape

import pandas as pd

import analysis as A
import config as C
import evidence as E
from trades import HOLD_CSS

STATUS_ICON = {"ok": "✅", "warn": "⚠️", "bad": "⛔", "info": "ℹ️", "unknown": "❔"}


# ════════════════════════════════════════════════════════════
# 1. 종목 하나 분석
# ════════════════════════════════════════════════════════════
def analyze(row: pd.Series, feat: pd.DataFrame, hist: pd.DataFrame, prob: dict) -> dict:
    region, t = row["region"], row["ticker"]
    code = t.split(".")[0]
    ck = A.current_checks(feat)

    disc = E.kr_disclosures(code) if region == "KR" else E.us_disclosures(t)
    groups = E.summarize_disclosures(disc["items"])
    flow = E.kr_investor_flow(code) if region == "KR" else E.us_ownership(t)

    checks = []
    # ① 가격고정 이벤트 공시
    if not disc["ok"]:
        checks.append(("unknown", "가격고정 이벤트 공시 (공개매수·합병 등)", disc["error"] + " → 아래 링크로 직접 확인"))
    elif groups["event"]:
        checks.append(("bad", "가격고정 이벤트 공시 (공개매수·합병 등)",
                       " / ".join(f"{i['date']} {i['title']}" for i in groups["event"][:3])))
    else:
        checks.append(("ok", "가격고정 이벤트 공시 (공개매수·합병 등)", f"최근 {C.DISCLOSURE_DAYS}일 없음"))
    # ② 물량·악재 공시
    if not disc["ok"]:
        checks.append(("unknown", "매도 물량·악재 공시 (증자·CB·대주주 매도 등)", "확인 불가"))
    elif groups["risk"]:
        checks.append(("warn", "매도 물량·악재 공시 (증자·CB·대주주 매도 등)",
                       " / ".join(f"{i['date']} {i['title']}" for i in groups["risk"][:3])))
    else:
        checks.append(("ok", "매도 물량·악재 공시 (증자·CB·대주주 매도 등)", f"최근 {C.DISCLOSURE_DAYS}일 없음"))
    # ③ 수급
    if region == "KR":
        if flow.get("ok"):
            f5 = flow["d5"]
            smart = f5["institution"] + f5["foreign"]
            share = smart / f5["volume"] * 100 if f5.get("volume") else float("nan")
            detail = (f"최근 {f5['days']}일 외국인 {f5['foreign']:+,.0f}주 · 기관 {f5['institution']:+,.0f}주 · "
                      f"개인(추정) {f5['individual_est']:+,.0f}주")
            if smart > 0:
                checks.append(("ok", "누가 샀나 (외국인·기관 순매수)", detail + f" → 외국인·기관이 거래량의 {share:.0f}% 순매수"))
            elif f5["foreign"] > 0 or f5["institution"] > 0:
                checks.append(("info", "누가 샀나 (외국인·기관 순매수)", detail + " → 한쪽만 순매수"))
            else:
                checks.append(("warn", "누가 샀나 (외국인·기관 순매수)", detail + " → 개인 위주 매수"))
        else:
            checks.append(("unknown", "누가 샀나 (외국인·기관 순매수)", flow.get("error", "")))
    else:
        if flow.get("ok") and flow.get("short_pct_float") is not None:
            sp = flow["short_pct_float"]
            inst = f" · 기관 보유 {flow['inst_pct']:.0f}%" if flow.get("inst_pct") is not None else ""
            st = "warn" if sp >= 10 else "ok"
            checks.append((st, "공매도 비율 (유통주식 대비)", f"{sp:.1f}%{inst}"
                           + (" → 공매도가 많아 주가가 눌릴 수 있음" if st == "warn" else "")))
        else:
            checks.append(("unknown", "공매도 비율 (유통주식 대비)", flow.get("error", "데이터 없음")))
        if disc.get("ok") and disc.get("insider_form4"):
            checks.append(("info", "내부자 거래 신고 (Form 4)",
                           f"최근 {C.DISCLOSURE_DAYS}일 {disc['insider_form4']}건 — 매수/매도 여부는 SEC 링크에서 확인"))
    # ④ 거래량 분포
    checks.append(("ok" if ck["items"][0] else "warn", "거래량이 며칠에 걸쳐 고르게 늘었나",
                   f"최근 {C.RECENT_DAYS}일 중 가장 많은 하루가 {ck['max_day_share']:.0f}% 차지"
                   + ("" if ck["items"][0] else " → 하루 이벤트성 거래일 가능성")))
    # ⑤ 급증 기간 봉
    bp = ck["buy_pressure"]
    checks.append(("ok" if bp > 0 else "warn", "급증 기간 매수/매도 우위 (봉 모양)",
                   f"종가 위치 지수 {bp:+.2f} (−1 저가 마감 ~ +1 고가 마감), 평균 위꼬리 {ck['up_wick']:.0f}%"
                   + (" → 매수 우위" if bp > 0 else " → 위에서 파는 물량 우위")))
    # ⑥ 주가 위치
    pos = ck["pos_52w"]
    if pos is None:
        checks.append(("unknown", "1년 범위 중 현재 위치", "상장 기간이 짧아 계산 불가"))
    else:
        st = "ok" if pos < 50 else ("warn" if pos >= 80 else "info")
        msg = {"ok": "바닥권 — 의미 있는 위치", "warn": "고점권 — 차익 실현 물량 주의", "info": "중간 위치"}[st]
        checks.append((st, "1년 범위 중 현재 위치", f"1년 최저 0% ~ 최고 100% 중 {pos:.0f}% → {msg}"))
    # ⑦ 가격 고정
    checks.append(("ok" if ck["items"][3] else "bad", "주가가 특정 가격에 묶여 있지 않은가",
                   f"20일 하루 평균 변동 {ck['volatility_20d']:.2f}%"
                   + ("" if ck["items"][3] else " → 공개매수·합병 등으로 가격이 고정됐을 가능성 큼")))

    # 세력 이탈 판단
    pe = A.dump_price_evidence(hist, feat)
    dump = assess_dump(region, pe, flow, groups, disc)

    # 매매 기준가
    levels = {"breakout": ck["box_high"], "stop": ck["box_low"], "close": ck["close"]}

    # 상승 확률
    p_score = prob.get(ck["score"]) if prob else None
    p_all = prob.get("all") if prob else None

    # 종합 판정
    statuses = [s for s, _, _ in checks]
    if groups["event"]:
        verdict = ("bad", "공개매수·합병 등 이벤트 공시가 있습니다 — 가격이 묶여 있어 일반적인 매집 신호로 보기 어렵습니다.")
    elif dump["level"] == "high":
        verdict = ("bad", "세력이 털고 나간 주식일 가능성이 높습니다 — 이번 거래량 급증은 남은 물량 처분일 수 있어 매수 비추천.")
    elif not ck["items"][3]:
        verdict = ("bad", "주가가 특정 가격에 묶여 있습니다 — 공개매수·합병 같은 이벤트 가능성. 공시 확인이 먼저입니다.")
    elif dump["level"] == "mid":
        verdict = ("warn", "세력 이탈 흔적이 일부 보입니다 — 아래 '세력 이탈 판단' 근거를 먼저 확인하세요.")
    elif groups["risk"]:
        verdict = ("warn", "물량 부담 공시가 있습니다 — 매도 물량을 소화하는 중일 수 있어 보수적으로 보세요.")
    elif ck["score"] >= 3 and statuses.count("warn") <= 1:
        verdict = ("ok", "매집형 패턴에 가깝습니다 — 박스 상단을 거래량과 함께 돌파하는지 확인 후 관심.")
    else:
        verdict = ("info", "중립 — 긍정·주의 신호가 섞여 있습니다. 아래 체크리스트를 확인하세요.")

    # ★ 관심 후보 5가지 조건
    has_bad = verdict[0] == "bad" or any(st == "bad" for st, _, _ in checks) or any(st == "bad" for _, st, _, _ in dump["lines"])
    p, pa = p_score, p_all
    cand = [
        (not has_bad, "위험 신호(⛔) 없음", "세력 이탈 '높음'·가격고정·위험 항목 없음" if not has_bad else "⛔ 항목이 있음"),
        (bool(p and p["p_up"] >= C.CAND_MIN_PROB and pa and p["p_up"] - pa["p_up"] >= C.CAND_MIN_EDGE),
         f"상승 확률 {C.CAND_MIN_PROB:g}% 이상 & 평균보다 {C.CAND_MIN_EDGE:g}%p 이상 높음",
         f"{p['p_up']:.0f}% (평균 {pa['p_up']:.0f}%, 차이 {p['p_up'] - pa['p_up']:+.0f}%p)" if p and pa else "통계 없음"),
        (bool(p and p["p_hit_up"] > p["p_hit_down"]), f"+{C.TARGET_UP_PCT:g}% 도달 > −{C.STOP_DOWN_PCT:g}% 하락",
         f"{p['p_hit_up']:.0f}% vs {p['p_hit_down']:.0f}%" if p else "통계 없음"),
        (bool(p and p["median_ret"] > 0), "중간 수익률 플러스", f"{p['median_ret']:+.1f}%" if p else "통계 없음"),
        (bool(p and p["n"] >= C.CAND_MIN_SAMPLES), f"표본 {C.CAND_MIN_SAMPLES}건 이상", f"{p['n']}건" if p else "통계 없음"),
    ]
    candidate = all(ok for ok, _, _ in cand)

    return {"row": row, "checks": checks, "cand": cand, "candidate": candidate, "score": ck["score"], "score_items": ck["items"],
            "p": p_score, "p_all": p_all, "levels": levels, "verdict": verdict,
            "disc": disc, "feat": feat, "dump": dump, "pe": pe,
            "p_dump": prob.get("dump") if prob else None, "p_nodump": prob.get("nodump") if prob else None}


def assess_dump(region: str, pe: dict, flow: dict, groups: dict, disc: dict) -> dict:
    """세력 이탈(털고 나간 주식) 위험도 0~100점과 근거"""
    L = []   # (점수, 상태, 항목, 설명)
    pump, dd = pe["pump_pct"], pe["drawdown_pct"]
    pumped = pump >= C.DUMP_PUMP_PCT and not pe["peak_is_recent"]

    # ① 급등 이력
    move = (f"{pe['base_low']:,.{0 if region == 'KR' else 2}f} → {pe['peak']:,.{0 if region == 'KR' else 2}f} "
            f"(+{pump:.0f}%, {pe['pump_days']}거래일, 고점 {pe['peak_date']})")
    if pe["peak_is_recent"]:
        L.append((0, "info", "1년 내 급등 이력", f"{move} — 고점이 최근이라 '이탈 후' 패턴은 아님"))
    elif pump >= 100:
        L.append((30, "bad", "1년 내 급등 이력", f"{move} — 2배 이상 급등: 세력 개입 가능성 큼"))
    elif pump >= C.DUMP_PUMP_PCT:
        L.append((20, "warn", "1년 내 급등 이력", f"{move} — 단기 급등 이력 있음"))
    else:
        L.append((0, "ok", "1년 내 급등 이력", f"최대 상승 +{pump:.0f}% — 뚜렷한 급등(작전성) 이력 없음"))

    # ② 고점 대비 하락
    if pumped and dd <= -50:
        L.append((25, "bad", "고점 대비 현재 위치", f"고점보다 {dd:.0f}% — 급등 후 반토막 이상: 전형적인 '털고 나간' 모양"))
    elif pumped and dd <= -C.DUMP_DRAWDOWN_PCT:
        L.append((15, "warn", "고점 대비 현재 위치", f"고점보다 {dd:.0f}% — 급등분 상당 부분 반납"))
    else:
        L.append((0, "ok" if not pumped else "info", "고점 대비 현재 위치", f"1년 고점보다 {dd:.0f}%"))

    # ③ 고점에서 대량 매도 흔적
    if pump >= 30 and pe["peak_top_vol_days"] >= 4 and (pe["peak_clv"] < 0 or pe["peak_wick"] >= 40):
        L.append((15, "bad", "고점 부근 대량 매도 흔적",
                  f"1년 중 거래량 상위 10일 중 {pe['peak_top_vol_days']}일이 고점 근처, 그날들 저가 쪽 마감·위꼬리 {pe['peak_wick']:.0f}% "
                  "— 고점에서 물량을 넘긴 모양"))
    elif pump >= 30 and pe["peak_top_vol_days"] >= 4:
        L.append((7, "warn", "고점 부근 대량 매도 흔적",
                  f"거래량 상위 10일 중 {pe['peak_top_vol_days']}일이 고점 근처 — 고점에서 손바뀜이 컸음"))
    else:
        L.append((0, "ok", "고점 부근 대량 매도 흔적", "고점 근처에 대량 거래가 몰린 흔적 없음"))

    # ④ 관심 소멸
    dry = pe["dry_ratio"]
    if pumped and dry == dry and dry < 0.35:
        L.append((10, "warn", "고점 이후 거래 말라붙음",
                  f"이번 급증 전 60일 거래량이 고점 때의 {dry * 100:.0f}% — 세력이 빠진 뒤 관심이 사라진 전형"))
    elif pumped and dry == dry:
        L.append((0, "info", "고점 이후 거래 말라붙음", f"고점 때 대비 {dry * 100:.0f}% 수준"))

    # ⑤ 자금 흐름 (20일 CMF)
    cmf = pe["cmf20"]
    if cmf < -0.05:
        L.append((10, "warn", "20일 자금 흐름", f"{cmf:+.2f} — 주가는 버티는데 자금은 빠지는 중(분산)"))
    elif cmf > 0.05:
        L.append((0, "ok", "20일 자금 흐름", f"{cmf:+.2f} — 자금 유입(매집 쪽 흔적)"))
    else:
        L.append((0, "info", "20일 자금 흐름", f"{cmf:+.2f} — 뚜렷한 방향 없음"))

    # ⑥ 이번 거래량 급증의 성격
    bp = pe["buy_pressure"]
    if bp < -0.1:
        L.append((10, "bad", "이번 급증 거래의 성격", f"종가 위치 {bp:+.2f} — 거래 터진 날 저가 쪽 마감: 남은 물량 처분 가능성"))
    elif bp < 0:
        L.append((5, "warn", "이번 급증 거래의 성격", f"종가 위치 {bp:+.2f} — 매도 약간 우위"))
    else:
        L.append((0, "ok", "이번 급증 거래의 성격", f"종가 위치 {bp:+.2f} — 매수 우위"))

    # ⑦ 수급 (한국)
    if region == "KR":
        if flow.get("ok"):
            f20, f5 = flow["d20"], flow["d5"]
            s20, s5 = f20["institution"] + f20["foreign"], f5["institution"] + f5["foreign"]
            det = (f"20일 외국인 {f20['foreign']:+,.0f} · 기관 {f20['institution']:+,.0f} · 개인(추정) "
                   f"{f20['individual_est']:+,.0f}주 / 5일 외국인+기관 {s5:+,.0f}주")
            if s20 < 0 and s5 < 0:
                L.append((20, "bad", "누가 팔고 누가 샀나", det + " — 큰손은 계속 팔고 개인이 받는 중"))
            elif s20 < 0:
                L.append((10, "warn", "누가 팔고 누가 샀나", det + " — 20일간 큰손 순매도"))
            elif s5 < 0:
                L.append((5, "warn", "누가 팔고 누가 샀나", det + " — 최근 5일 큰손 순매도"))
            else:
                L.append((0, "ok", "누가 팔고 누가 샀나", det + " — 외국인·기관 순매수"))
        else:
            L.append((0, "unknown", "누가 팔고 누가 샀나", flow.get("error", "") + " — 네이버 '투자자별 매매동향' 직접 확인"))
    else:
        sp = flow.get("short_pct_float") if flow.get("ok") else None
        if sp is not None and sp >= 15:
            L.append((5, "warn", "공매도 비율", f"유통주식의 {sp:.0f}% — 하락에 베팅한 물량 많음"))

    # ⑧ 공시
    if disc.get("ok"):
        supply = [i for i in groups["risk"] if any(k in i["title"].replace(" ", "") for k in
                  ["전환", "추가상장", "신주인수권", "교환", "처분", "424B", "S-1", "S-3", "144"])]
        stake = [i for i in groups["info"] if any(k in i["title"] for k in ["대량보유", "특정증권", "소유주식변동", "13D", "13G"])]
        if supply:
            L.append((10, "bad", "물량 출회 공시", " / ".join(f"{i['date']} {i['title']}" for i in supply[:2])
                      + " — 전환·추가상장·처분 물량이 시장에 나오는 중"))
        if len(stake) >= 3:
            L.append((5, "warn", "지분 변동 보고 잦음", f"최근 {C.DISCLOSURE_DAYS}일 {len(stake)}건 — 대주주·큰손이 팔았는지 공시 본문 확인"))
        if not supply and len(stake) < 3:
            L.append((0, "ok", "물량 출회 공시", "전환사채 전환·추가상장·지분 대량 변동 공시 없음"))
    else:
        L.append((0, "unknown", "물량 출회 공시", "공시 확인 불가 — DART/SEC 링크로 직접 확인"))

    score = min(100, sum(p for p, *_ in L))
    level = "high" if score >= C.DUMP_HIGH_SCORE else ("mid" if score >= C.DUMP_MID_SCORE else "low")
    return {"score": score, "level": level, "lines": L}


# ════════════════════════════════════════════════════════════
# 2. 차트 (SVG, 외부 라이브러리 없음)
# ════════════════════════════════════════════════════════════
def chart_svg(feat: pd.DataFrame, hist: pd.DataFrame, levels: dict, region: str, days: int = 250, peak_date=None) -> str:
    h = hist[hist["Volume"] > 0].tail(days)
    if len(h) < 10:
        return ""
    W, PH, VT, VH, L, R = 900, 190, 205, 60, 8, 70
    c, v = h["Close"].astype(float).values, h["Volume"].astype(float).values
    lo = min(c.min(), levels["stop"]) * 0.98
    hi = max(c.max(), levels["breakout"]) * 1.02
    n = len(c)
    x = lambda i: L + (W - L - R) * i / (n - 1)
    y = lambda p: 10 + (PH - 20) * (1 - (p - lo) / (hi - lo))
    pts = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(c))
    vmax = v.max() or 1
    bw = max((W - L - R) / n * 0.7, 1)
    bars = []
    for i, vol in enumerate(v):
        bh = VH * vol / vmax
        cls = "vr" if i >= n - C.RECENT_DAYS else ("vb" if i >= n - C.RECENT_DAYS - C.BASE_DAYS else "vo")
        bars.append(f'<rect class="{cls}" x="{x(i) - bw / 2:.1f}" y="{VT + VH - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}"/>')
    fmt = (lambda p: f"{p:,.0f}") if region == "KR" else (lambda p: f"{p:,.2f}")
    lines = ""
    for key, cls, label in [("breakout", "lb", "돌파 기준"), ("stop", "ls", "손절 참고")]:
        yy = y(levels[key])
        lines += (f'<line class="{cls}" x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
                  f'<text class="tl" x="{W - R + 4}" y="{yy + 4:.1f}">{label}</text>'
                  f'<text class="tv" x="{W - R + 4}" y="{yy + 16:.1f}">{fmt(levels[key])}</text>')
    if peak_date is not None:
        pi = [i for i, d in enumerate(h.index) if d.date() == peak_date]
        if pi:
            px, py = x(pi[0]), y(c[pi[0]])
            lines += (f'<circle class="pk" cx="{px:.1f}" cy="{py:.1f}" r="4"/>'
                      f'<text class="tk" x="{px:.1f}" y="{max(py - 8, 10):.1f}" text-anchor="middle">1년 고점</text>')
    d0, d1 = h.index[0].strftime("%Y-%m-%d"), h.index[-1].strftime("%Y-%m-%d")
    return f"""<svg viewBox="0 0 {W} {VT + VH + 22}" class="chart" role="img" aria-label="최근 {days}거래일 주가와 거래량">
<polyline class="pl" points="{pts}"/>{lines}
<circle class="pd" cx="{x(n - 1):.1f}" cy="{y(c[-1]):.1f}" r="3.5"/>
{''.join(bars)}
<text class="tx" x="{L}" y="{VT + VH + 16}">{d0}</text><text class="tx" x="{W - R}" y="{VT + VH + 16}" text-anchor="end">{d1}</text>
<text class="tx" x="{L}" y="{VT - 4}">거래량 — <tspan class="tr">■ 최근 {C.RECENT_DAYS}일</tspan> <tspan class="tb">■ 비교 기준 {C.BASE_DAYS}일</tspan></text>
</svg>"""


# ════════════════════════════════════════════════════════════
# 3. HTML
# ════════════════════════════════════════════════════════════
def _price(v, region):
    return f"{v:,.0f}원" if region == "KR" else f"${v:,.2f}"


def _cap(v, region):
    if v is None or v != v:
        return "-"
    return f"{v / 1e8:,.0f}억" if region == "KR" else f"${v / 1e9:,.2f}B"


def _links(row) -> str:
    t, region = row["ticker"], row["region"]
    code = t.split(".")[0]
    if region == "KR":
        q = escape(str(row["name"]))
        L = [("네이버 종목", f"https://finance.naver.com/item/main.naver?code={code}"),
             ("투자자별 매매동향", f"https://finance.naver.com/item/frgn.naver?code={code}"),
             ("최신 뉴스", f"https://search.naver.com/search.naver?where=news&sort=1&query={q}"),
             ("DART 공시", f"https://dart.fss.or.kr/dsab007/main.do?option=corp&textCrpNm={q}")]
    else:
        L = [("Yahoo 시세", f"https://finance.yahoo.com/quote/{t}"),
             ("최신 뉴스", f"https://finance.yahoo.com/quote/{t}/news"),
             ("SEC 공시", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}&owner=include"),
             ("Finviz", f"https://finviz.com/quote.ashx?t={t}"),
             ("내부자 거래", f"http://openinsider.com/search?q={t}")]
    return " · ".join(f"<a href='{u}' target='_blank'>{n}</a>" for n, u in L)


def _horizon_table(p: dict) -> str:
    hs = p.get("horizons") or {}
    if not hs:
        return ""
    best = A.best_horizon(p)
    cells = "".join(
        f"<td class='{'best' if hz == best else ''}'><b>{v['p_up']:.0f}%</b><span>{v['median']:+.1f}%</span></td>"
        for hz, v in hs.items())
    heads = "".join(f"<th class='{'best' if hz == best else ''}'>{hz}일 뒤{' ★' if hz == best else ''}</th>" for hz in hs)
    return (f"<table class='hz'><tr><th></th>{heads}</tr><tr><th>이익 비율<br><small>중간 수익률</small></th>{cells}</tr></table>"
            f"<div class='cmp'>★ 과거 기준 가장 유리했던 보유 기간: <b>{best}거래일</b></div>")


def _prob_block(a: dict) -> str:
    p, pa = a["p"], a["p_all"]
    if not p:
        return ("<div class='prob none'><div class='big'>표본 부족</div>"
                "<div>과거 2년간 같은 신호가 충분히 나오지 않아 확률을 계산하지 않았습니다.</div></div>")
    cls = "up" if p["p_up"] >= 55 else ("down" if p["p_up"] < 45 else "mid")
    base = (f"<div class='cmp'>같은 신호 전체 평균(점수 무관, {pa['n']}건): 상승 {pa['p_up']:.0f}% · "
            f"+{C.TARGET_UP_PCT:g}% 도달 {pa['p_hit_up']:.0f}%</div>") if pa else ""
    return f"""<div class='prob {cls}'>
<div class='lab'>상승 확률 <small>(과거 통계 · 점수 {a['score']}/4인 신호 {p['n']}건)</small></div>
<div class='big'>{p['p_up']:.0f}%</div>
<div class='sub'>{C.FORWARD_DAYS}거래일 뒤 매수가보다 높았던 비율</div>
<div class='grid'>
 <div><b>{p['p_hit_up']:.0f}%</b><span>기간 중 +{C.TARGET_UP_PCT:g}% 도달</span></div>
 <div><b>{p['p_hit_down']:.0f}%</b><span>기간 중 −{C.STOP_DOWN_PCT:g}% 하락</span></div>
 <div><b>{p['median_ret']:+.1f}%</b><span>{C.FORWARD_DAYS}거래일 뒤 중간 수익률</span></div>
</div>{_horizon_table(p)}{base}</div>"""


DUMP_TXT = {"high": "높음", "mid": "중간", "low": "낮음"}


def _cand_block(a: dict) -> str:
    n_ok = sum(ok for ok, _, _ in a["cand"])
    items = "".join(f"<li class='{'yes' if ok else 'no'}'>{'✅' if ok else '❌'} <b>{escape(t)}</b> <span>{escape(d)}</span></li>"
                    for ok, t, d in a["cand"])
    head = ("★ 관심 후보 — 5가지 조건을 모두 만족했습니다. 돌파 기준가를 거래량과 함께 넘는지 지켜보세요."
            if a["candidate"] else f"관심 후보 아님 — 5가지 중 {n_ok}가지 만족")
    return f"<div class='cand {'on' if a['candidate'] else 'off'}'><div class='ch'>{head}</div><ul>{items}</ul></div>"


def _dump_block(a: dict) -> str:
    d = a["dump"]
    rows = "".join(
        f"<tr class='{st}'><td class='ic'>{STATUS_ICON[st]}</td><td><b>{escape(t)}</b><div class='dt'>{escape(det)}</div></td>"
        f"<td class='pt'>{'+' + str(p) if p else '0'}</td></tr>" for p, st, t, det in d["lines"])
    pd_, pn = a.get("p_dump"), a.get("p_nodump")
    hist_txt = ""
    if pd_ and pn:
        hist_txt = (f"<p class='muted'>과거 2년 같은 신호 중 <b>'급등 후 급락' 이력 종목</b>은 {C.FORWARD_DAYS}거래일 뒤 상승 "
                    f"<b>{pd_['p_up']:.0f}%</b> · 중간 수익률 {pd_['median_ret']:+.1f}% ({pd_['n']}건), "
                    f"이력 없는 종목은 상승 <b>{pn['p_up']:.0f}%</b> · {pn['median_ret']:+.1f}% ({pn['n']}건)</p>")
    msg = {"high": "세력이 주가를 올린 뒤 물량을 넘기고 빠져나간 주식의 특징이 많습니다. 이번 거래량은 '재매집'보다 남은 물량 처분일 가능성을 먼저 의심하세요.",
           "mid": "이탈 흔적이 일부 있습니다. 표에서 +점수 항목을 확인하고, 외국인·기관이 다시 사는지 지켜본 뒤 판단하세요.",
           "low": "세력이 털고 나간 주식의 전형적인 흔적은 적습니다."}[d["level"]]
    return f"""<div class="dump {d['level']}"><div class="dh"><div><div class="lab">세력 이탈(털고 나간 주식) 위험도</div>
<div class="big">{DUMP_TXT[d['level']]} <span>{d['score']}/100</span></div></div>
<div class="bar"><i style="width:{d['score']}%"></i></div></div>
<p class="dm">{msg}</p><table class="ck">{rows}</table>{hist_txt}</div>"""


def _card(a: dict, hist: pd.DataFrame, tag: str) -> str:
    r = a["row"]
    region = r["region"]
    vs, vtext = a["verdict"]
    checks = "".join(f"<tr class='{s}'><td class='ic'>{STATUS_ICON[s]}</td><td><b>{escape(t)}</b>"
                     f"<div class='dt'>{escape(d)}</div></td></tr>" for s, t, d in a["checks"])
    score_list = "".join(f"<li class='{'yes' if ok else 'no'}'>{'●' if ok else '○'} {escape(lbl)}</li>"
                         for (lbl, _), ok in zip(A.SCORE_ITEMS, a["score_items"]))
    lv = a["levels"]
    lv_html = (f"<div class='lv'><div><span>현재가</span><b>{_price(lv['close'], region)}</b></div>"
               f"<div><span>돌파 기준 (60일 고가)</span><b>{_price(lv['breakout'], region)}</b>"
               f"<em>{(lv['breakout'] / lv['close'] - 1) * 100:+.1f}%</em></div>"
               f"<div><span>손절 참고 (60일 저가)</span><b>{_price(lv['stop'], region)}</b>"
               f"<em>{(lv['stop'] / lv['close'] - 1) * 100:+.1f}%</em></div></div>")
    disc_items = a["disc"]["items"][:12]
    if disc_items:
        disc_html = "<table class='disc'>" + "".join(
            f"<tr><td class='d'>{i['date']}</td><td>"
            + (f"<span class='tag {i['kind']}'>{E.LABEL[i['kind']]}</span> " if i["kind"] else "")
            + f"<a href='{i['url']}' target='_blank'>{escape(i['title'])}</a></td></tr>" for i in disc_items) + "</table>"
    elif a["disc"]["ok"]:
        disc_html = f"<p class='muted'>최근 {C.DISCLOSURE_DAYS}일 공시 없음</p>"
    else:
        disc_html = f"<p class='muted'>{escape(a['disc']['error'])} — 아래 공시 링크에서 직접 확인하세요.</p>"

    return f"""<section class="card" id="r-{escape(r['ticker'])}">
<div class="hd"><div><span class="tagx">{tag}</span>{"<span class='star'>★ 관심 후보</span>" if a["candidate"] else ""}<h3>{escape(str(r['name']))} <small>{escape(r['ticker'])} · {escape(str(r['market']))}</small></h3>
<div class="meta">{_price(r['close'], region)} · 1개월 {r['return_1m_pct']:+.2f}% · 거래량 <b>{r['vol_ratio']:.1f}배</b> · 시총 {_cap(r['marcap'], region)}</div></div></div>
<div class="verdict {vs}">{STATUS_ICON[vs]} {escape(vtext)}</div>
{_cand_block(a) if tag != "내 보유 종목" else ""}
{_dump_block(a)}
<div class="two">{_prob_block(a)}
<div class="score"><div class="lab">패턴 점수 <b>{a['score']}/4</b></div><ul>{score_list}</ul>
<p class="muted">점수가 같은 과거 신호들로 왼쪽 확률을 계산했습니다.</p></div></div>
{chart_svg(a['feat'], hist, lv, region, peak_date=a['pe']['peak_date'])}
{lv_html}
<h4>꼭 확인해야 할 것</h4><table class="ck">{checks}</table>
<h4>최근 {C.DISCLOSURE_DAYS}일 공시</h4>{disc_html}
<p class="links">{_links(r)}</p>
</section>"""


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e6e6ea;--soft:#f6f6f8;--acc:#0b57d0;--ok:#137333;--okb:#e6f4ea;
--warn:#a35a00;--warnb:#fff4e0;--bad:#b3261e;--badb:#fce8e6;--info:#3c4a5c;--infob:#eef1f6}
body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:var(--fg);background:var(--bg);line-height:1.5}
h1{font-size:23px;margin-bottom:6px} h2{margin-top:36px;font-size:19px;border-bottom:2px solid var(--fg);padding-bottom:6px}
h3{margin:2px 0;font-size:19px} h4{margin:22px 0 8px;font-size:15px} small{color:var(--mut);font-weight:normal}
a{color:var(--acc);text-decoration:none} a:hover{text-decoration:underline}
.cond{color:#444;background:var(--soft);padding:10px 14px;border-radius:8px;font-size:14px}
.muted{color:var(--mut);font-size:13px}
table{border-collapse:collapse;width:100%} th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#fafafa;font-weight:600} .n{text-align:right;white-space:nowrap} table.near{color:#555;font-size:14px}
.card{border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin:22px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.tagx{font-size:12px;background:var(--fg);color:#fff;border-radius:4px;padding:2px 7px}
.meta{color:#444;margin-top:4px}
.verdict{margin:14px 0;padding:11px 14px;border-radius:8px;font-weight:600}
.verdict.ok{background:var(--okb);color:var(--ok)} .verdict.warn{background:var(--warnb);color:var(--warn)}
.verdict.bad{background:var(--badb);color:var(--bad)} .verdict.info{background:var(--infob);color:var(--info)}
.two{display:grid;grid-template-columns:1.25fr 1fr;gap:14px} @media(max-width:700px){.two{grid-template-columns:1fr}}
.prob{border-radius:10px;padding:14px 16px;background:var(--soft)} .prob .lab{font-size:14px}
.prob .big{font-size:40px;font-weight:800;line-height:1.1;margin-top:4px} .prob .sub{color:var(--mut);font-size:13px}
.prob.up .big{color:var(--ok)} .prob.down .big{color:var(--bad)} .prob.mid .big{color:var(--warn)} .prob.none .big{font-size:22px;color:var(--mut)}
.prob .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.prob .grid div{background:#fff;border-radius:8px;padding:7px 8px} .prob .grid b{display:block;font-size:17px} .prob .grid span{font-size:12px;color:var(--mut)}
.prob .cmp{font-size:12px;color:var(--mut);margin-top:8px}
.score{border:1px solid var(--line);border-radius:10px;padding:14px 16px} .score ul{list-style:none;padding:0;margin:8px 0}
.score li{font-size:14px;padding:2px 0} .score li.yes{color:var(--ok)} .score li.no{color:var(--mut)}
.chart{width:100%;height:auto;margin-top:16px} .pl{fill:none;stroke:var(--fg);stroke-width:1.8} .pd{fill:var(--acc)}
.lb{stroke:var(--ok);stroke-dasharray:5 4} .ls{stroke:var(--bad);stroke-dasharray:5 4}
.tl{font-size:11px;fill:var(--mut)} .tv{font-size:12px;font-weight:700;fill:var(--fg)} .tx{font-size:11px;fill:var(--mut)}
.pk{fill:var(--bad)} .tk{font-size:11px;fill:var(--bad);font-weight:700} .vr{fill:var(--acc)} .vb{fill:#b9c7e6} .vo{fill:#dcdce2} .tr{fill:var(--acc)} .tb{fill:#8fa3cf}
.lv{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:6px}
.lv div{background:var(--soft);border-radius:8px;padding:8px 10px} .lv span{display:block;font-size:12px;color:var(--mut)}
.lv b{font-size:16px} .lv em{font-style:normal;font-size:13px;color:var(--mut);margin-left:6px}
.ck td.ic{width:28px;font-size:16px} .ck .dt{color:#555;font-size:13px}
.ck tr.bad td{background:var(--badb)} .ck tr.warn td{background:#fffaf0}
.disc td{font-size:13px} .disc td.d{white-space:nowrap;color:var(--mut);width:90px}
.tag{font-size:11px;border-radius:4px;padding:1px 6px;font-weight:600}
.tag.event{background:var(--badb);color:var(--bad)} .tag.risk{background:var(--warnb);color:var(--warn)}
.tag.good{background:var(--okb);color:var(--ok)} .tag.info{background:var(--infob);color:var(--info)}
.links{margin-top:14px;font-size:14px}
.dump{border-radius:12px;padding:16px 18px;margin:14px 0;border:2px solid var(--line)}
.dump.high{border-color:var(--bad);background:#fff8f7} .dump.mid{border-color:#f0b35a;background:#fffcf5} .dump.low{border-color:#9fd3ae;background:#f7fcf8}
.dump .dh{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.dump .lab{font-size:14px;font-weight:600} .dump .big{font-size:30px;font-weight:800} .dump .big span{font-size:15px;color:var(--mut);font-weight:600}
.dump.high .big{color:var(--bad)} .dump.mid .big{color:var(--warn)} .dump.low .big{color:var(--ok)}
.dump .bar{flex:1;min-width:200px;height:10px;background:#ececf0;border-radius:6px;overflow:hidden}
.dump .bar i{display:block;height:100%;background:linear-gradient(90deg,#34a853,#f9ab00,#d93025)}
.dump .dm{font-size:14px;margin:10px 0} .ck td.pt{width:44px;text-align:right;font-weight:700;color:var(--mut)}
.star{font-size:12px;background:#f9ab00;color:#fff;border-radius:4px;padding:2px 8px;margin-left:6px;font-weight:700}
.st{color:#e8a200;font-weight:800}
.cand{border-radius:10px;padding:12px 16px;margin:12px 0} .cand.on{background:#fff7e0;border:2px solid #f9ab00} .cand.off{background:var(--soft)}
.cand .ch{font-weight:700} .cand.on .ch{color:#9a6700} .cand ul{list-style:none;padding:0;margin:8px 0 0}
.cand li{font-size:14px;padding:2px 0} .cand li span{color:var(--mut);margin-left:4px} .cand li.no b{color:var(--mut);font-weight:600}
.today{border-radius:10px;padding:12px 16px;margin:16px 0;font-size:15px} .today.on{background:#fff7e0;border:2px solid #f9ab00} .today.off{background:var(--soft);color:#444}
.hz{margin-top:10px;background:#fff;border-radius:8px;font-size:13px} .hz th,.hz td{text-align:center;padding:5px 4px;border-bottom:none}
.hz th{background:none;color:var(--mut);font-weight:600} .hz td b{display:block;font-size:16px} .hz td span{font-size:12px;color:var(--mut)}
.hz .best{background:#fff3cd} .hz th.best{color:#9a6700}
.guide{background:var(--soft);border-radius:10px;padding:14px 18px;font-size:14px;margin-top:40px}
.guide li{margin:3px 0}
"""


def summary_table(df: pd.DataFrame, region: str, cls: str = "") -> str:
    rows = []
    for _, r in df.iterrows():
        star = "<span class='st'>★</span> " if r.get("cand_star") else ""
        rows.append(f"<tr><td>{star}<a href='#r-{escape(r['ticker'])}'>{escape(str(r['name']))}</a></td>"
                    f"<td>{escape(r['ticker'])}</td><td class=n>{_price(r['close'], region)}</td>"
                    f"<td class=n>{r['return_1m_pct']:+.2f}%</td><td class=n><b>{r['vol_ratio']:.1f}배</b></td>"
                    f"<td class=n>{_cap(r['marcap'], region)}</td><td class=n>{r.get('dump_txt', '')}</td><td class=n>{r.get('prob_txt', '')}</td></tr>")
    return (f"<table class='{cls}'><tr><th>종목</th><th>코드</th><th>종가</th><th>1개월</th><th>거래량 배수</th>"
            "<th>시가총액</th><th>세력 이탈 위험</th><th>상승 확률*</th></tr>" + "".join(rows) + "</table>")


def build_page(run_date: str, sections: list[dict], cond_text: str, top_html: str = "") -> str:
    """sections: [{region, title, hits, near, cards(html list), stats(dict|None)}]"""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>거래량 급증 종목 리포트 {run_date}</title><style>{CSS}{HOLD_CSS}</style></head><body>
<h1>거래량 급증 · 주가 정체 종목 리포트 — {run_date}</h1>
<p class="cond">{cond_text}</p>
{top_html}
{build_main_body(sections)}</body></html>"""


def build_main_body(sections: list[dict]) -> str:
    """스크리닝 결과 본문(관심 후보 띠 + 시장별 표 + 종목 카드 + 읽는 법)"""
    body = []
    stars = [(s["region"], r) for s in sections for df in (s["hits"], s["near"]) if len(df) and "cand_star" in df
             for _, r in df[df["cand_star"] == True].iterrows()]
    if stars:
        body.append("<div class='today on'><b>★ 오늘의 관심 후보</b> — " + " · ".join(
            f"<a href='#r-{escape(r['ticker'])}'>{escape(str(r['name']))}</a> ({r.get('prob_txt', '')})" for _, r in stars) + "</div>")
    else:
        body.append("<div class='today off'><b>오늘은 ★ 관심 후보가 없습니다.</b> 5가지 조건을 모두 만족한 종목이 없어요. "
                    "무리해서 사지 않는 것도 전략입니다.</div>")
    for s in sections:
        region = s["region"]
        body.append(f"<h2>{s['title']} <small>{len(s['hits'])}종목</small></h2>")
        if len(s["hits"]):
            body.append(summary_table(s["hits"], region))
        else:
            body.append("<p class='muted'>오늘은 조건을 모두 만족하는 종목이 없습니다.</p>")
        if len(s["near"]):
            body.append(f"<h4>참고: 수익률 조건은 맞고 거래량 배수가 높은 순 <small>(거래량 {C.VOLUME_MULTIPLE:g}배 미달)</small></h4>")
            body.append(summary_table(s["near"], region, "near"))
        st = s.get("stats")
        if st:
            body.append(f"<p class='muted'>과거 2년 이 시장에서 같은 신호 {st['n']}건: {C.FORWARD_DAYS}거래일 뒤 상승 {st['p_up']:.0f}%, "
                        f"+{C.TARGET_UP_PCT:g}% 도달 {st['p_hit_up']:.0f}%, −{C.STOP_DOWN_PCT:g}% 하락 {st['p_hit_down']:.0f}%</p>")
        body.extend(s["cards"])

    guide = f"""<div class="guide"><b>이 리포트 읽는 법</b><ul>
<li><b>상승 확률*</b>은 예측이 아니라 <b>과거 통계</b>입니다. 지난 2년 동안 같은 조건(거래량 {C.VOLUME_MULTIPLE:g}배·수익률 {C.MIN_RETURN_PCT:g}~{C.MAX_RETURN_PCT:g}%)과 같은 패턴 점수의 신호가 나왔을 때, 그 날 종가에 샀다면 {C.FORWARD_DAYS}거래일 뒤 이익이었던 비율입니다.</li>
<li>현재 상장된 종목만으로 계산해서(상장폐지 종목 제외) 실제보다 약간 좋게 나올 수 있습니다. 시장 전체 분위기에 따라서도 크게 달라집니다.</li>
<li>⛔·⚠️ 항목이 있으면 확률보다 그 내용을 먼저 확인하세요. 특히 공개매수·합병 공시가 있으면 확률은 의미가 없습니다.</li>
<li><b>★ 관심 후보</b>는 ① 위험 신호(⛔) 없음 ② 상승 확률 {C.CAND_MIN_PROB:g}% 이상이면서 같은 신호 평균보다 {C.CAND_MIN_EDGE:g}%p 이상 높음 ③ +{C.TARGET_UP_PCT:g}% 도달 확률 > −{C.STOP_DOWN_PCT:g}% 하락 확률 ④ 중간 수익률 플러스 ⑤ 표본 {C.CAND_MIN_SAMPLES}건 이상 — 다섯 가지를 모두 만족한 종목입니다. 매수 신호가 아니라 '지켜볼 종목'입니다.</li>
<li>매수는 '돌파 기준' 가격을 거래량과 함께 넘는지 확인한 뒤, 손절은 '손절 참고' 가격 이탈을 기준으로 미리 정해두는 방식을 권합니다.</li>
<li>투자 판단의 참고 자료이며, 투자 결과의 책임은 본인에게 있습니다.</li></ul></div>"""

    return "".join(body) + guide
