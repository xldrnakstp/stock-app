"""
가격·거래량만으로 계산하는 점검 항목 + 과거 신호 백테스트(상승 확률)

상승 확률은 '예측'이 아니라 통계입니다:
  과거 2년 동안 같은 조건의 신호가 나왔던 경우, 그 뒤 20거래일 동안 실제로 어떻게 됐는지의 비율.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

FWD = C.FORWARD_DAYS


# ════════════════════════════════════════════════════════════
# 종목 단위 지표 (시계열 전체에 대해 벡터 계산)
# ════════════════════════════════════════════════════════════
def _features(h: pd.DataFrame, region: str) -> pd.DataFrame:
    """일봉(Open/High/Low/Close/Volume) → 날짜별 신호·특징·이후 결과"""
    h = h[h["Volume"] > 0].copy()
    o, hi, lo, c, v = (h[k].astype(float) for k in ["Open", "High", "Low", "Close", "Volume"])
    R, B, P = C.RECENT_DAYS, C.BASE_DAYS, C.PRICE_LOOKBACK_DAYS

    f = pd.DataFrame(index=h.index)
    f["close"] = c
    f["vol_ratio"] = v.rolling(R).mean() / v.shift(R).rolling(B).mean()
    f["ret_pct"] = (c / c.shift(P) - 1) * 100
    f["base_turnover"] = (c * v).shift(R).rolling(B).mean()

    min_price = C.KR_MIN_PRICE if region == "KR" else C.US_MIN_PRICE
    min_turn = C.KR_MIN_BASE_TURNOVER_KRW if region == "KR" else C.US_MIN_BASE_TURNOVER_USD
    f["signal"] = ((f["vol_ratio"].round(6) >= C.VOLUME_MULTIPLE)
                   & (f["ret_pct"].round(6) >= C.MIN_RETURN_PCT)
                   & (f["ret_pct"].round(6) <= C.MAX_RETURN_PCT)
                   & (c >= min_price) & (f["base_turnover"] >= min_turn))

    # ① 거래량이 하루에 몰리지 않고 고르게 늘었나: 최근 5일 중 최대 하루 비중
    f["max_day_share"] = v.rolling(R).max() / v.rolling(R).sum()
    # ② 급증 기간 매수 우위: 거래량 가중 종가 위치 (+1 = 고가 마감, -1 = 저가 마감)
    rng = (hi - lo).replace(0, np.nan)
    clv = (((c - lo) - (hi - c)) / rng).fillna(0)
    f["buy_pressure"] = (clv * v).rolling(R).sum() / v.rolling(R).sum()
    f["up_wick"] = ((hi - np.maximum(o, c)) / rng).fillna(0).rolling(R).mean()   # 평균 위꼬리 비율
    # ③ 1년 범위 내 위치 (0% = 1년 최저, 100% = 1년 최고)
    hi250, lo250 = hi.rolling(250, min_periods=120).max(), lo.rolling(250, min_periods=120).min()
    f["pos_52w"] = ((c - lo250) / (hi250 - lo250).replace(0, np.nan)) * 100
    # ④ 가격 고정 의심(공개매수·합병 등): 20일 일간 변동성
    f["volatility_20d"] = c.pct_change().rolling(P).std() * 100
    # 박스권 (최근 60거래일)
    f["box_high"] = hi.rolling(60).max()
    f["box_low"] = lo.rolling(60).min()

    # 세력 이탈 관련 (백테스트에서도 쓰는 가격 기반 지표)
    f["pump_max"] = (c / c.shift(60) - 1).rolling(250, min_periods=120).max() * 100   # 1년 내 최대 60일 상승률
    f["drawdown"] = (c / c.rolling(250, min_periods=120).max() - 1) * 100             # 1년 고점 대비
    f["cmf20"] = (clv * v).rolling(20).sum() / v.rolling(20).sum()                   # 자금 유입(+)/유출(−)
    f["dump_flag"] = (f["pump_max"] >= C.DUMP_PUMP_PCT) & (f["drawdown"] <= -C.DUMP_DRAWDOWN_PCT)

    # 이후 결과 (백테스트용)
    f["fwd_ret"] = (c.shift(-FWD) / c - 1) * 100
    for hz in C.HORIZONS:
        f[f"fwd_{hz}"] = (c.shift(-hz) / c - 1) * 100
    f["fwd_max"] = (hi[::-1].rolling(FWD).max()[::-1].shift(-1) / c - 1) * 100
    f["fwd_min"] = (lo[::-1].rolling(FWD).min()[::-1].shift(-1) / c - 1) * 100

    f["score"] = score_series(f)
    return f


def score_series(f: pd.DataFrame) -> pd.Series:
    """긍정 점수 0~4점"""
    return ((f["max_day_share"] < C.SPREAD_MAX_DAY_SHARE).astype(int)
            + (f["buy_pressure"] > 0).astype(int)
            + (f["pos_52w"] < 50).astype(int)
            + (f["volatility_20d"] >= C.PINNED_VOLATILITY_PCT).astype(int))


SCORE_ITEMS = [
    ("거래량이 며칠에 걸쳐 고르게 증가", "max_day_share"),
    ("급증 기간 매수 우위(고가 쪽 마감)", "buy_pressure"),
    ("1년 범위의 아래쪽 절반(바닥권)", "pos_52w"),
    ("가격 고정(공개매수 등) 징후 없음", "volatility_20d"),
]


# ════════════════════════════════════════════════════════════
# 백테스트: 과거 신호 → 20거래일 뒤 결과
# ════════════════════════════════════════════════════════════
def build_feature_tables(histories: dict[str, pd.DataFrame], region: str) -> dict[str, pd.DataFrame]:
    out = {}
    for t, h in histories.items():
        try:
            if {"Open", "High", "Low", "Close", "Volume"} <= set(h.columns) and len(h) > 60:
                out[t] = _features(h, region)
        except Exception:
            pass
    return out


def collect_past_signals(feats: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """과거 신호 모음. 같은 종목의 연속 신호는 20거래일마다 1번만 셈(중복 방지)."""
    rows = []
    for t, f in feats.items():
        done = f.dropna(subset=["fwd_ret", "fwd_max", "fwd_min"])
        sig = done["signal"].astype(int)
        first = sig.astype(bool) & (sig.rolling(FWD + 1, min_periods=1).sum() == 1)
        s = done[first]
        if len(s):
            s = s[["score", "dump_flag", "fwd_ret", "fwd_max", "fwd_min"] + [f"fwd_{hz}" for hz in C.HORIZONS]].copy()
            s["ticker"] = t
            rows.append(s)
    return pd.concat(rows) if rows else pd.DataFrame(
        columns=["score", "dump_flag", "fwd_ret", "fwd_max", "fwd_min", "ticker"] + [f"fwd_{hz}" for hz in C.HORIZONS])


def _stats(df: pd.DataFrame) -> dict:
    return {
        "n": int(len(df)),
        "p_up": float((df["fwd_ret"] > 0).mean() * 100),
        "p_hit_up": float((df["fwd_max"] >= C.TARGET_UP_PCT).mean() * 100),
        "p_hit_down": float((df["fwd_min"] <= -C.STOP_DOWN_PCT).mean() * 100),
        "median_ret": float(df["fwd_ret"].median()),
        "horizons": {hz: {"p_up": float((df[f"fwd_{hz}"] > 0).mean() * 100),
                          "median": float(df[f"fwd_{hz}"].median()),
                          "avg": float(df[f"fwd_{hz}"].mean())} for hz in C.HORIZONS},
    }


def best_horizon(stats: dict | None) -> int | None:
    """중간 수익률이 가장 높았던 보유 기간 (같으면 짧은 쪽)"""
    if not stats or not stats.get("horizons"):
        return None
    return max(stats["horizons"], key=lambda hz: (round(stats["horizons"][hz]["median"], 2), -hz))


def probability_table(past: pd.DataFrame) -> dict:
    """{'all': 통계, 0..4: 점수별 통계(표본 적으면 인접 점수 합침)}"""
    table = {"all": _stats(past) if len(past) else None}
    for sc in range(5):
        sub = past[past["score"] == sc]
        if len(sub) < C.MIN_SAMPLES:          # 표본 부족 → 인접 점수까지 합침
            sub = past[(past["score"] - sc).abs() <= 1]
        table[sc] = _stats(sub) if len(sub) >= C.MIN_SAMPLES else None
    # '급등 후 급락' 이력 유무별 성과 (세력 이탈 종목이 실제로 더 나빴는지)
    if len(past):
        d = past["dump_flag"].astype(bool)
        table["dump"] = _stats(past[d]) if d.sum() >= C.MIN_SAMPLES else None
        table["nodump"] = _stats(past[~d]) if (~d).sum() >= C.MIN_SAMPLES else None
    return table


# ════════════════════════════════════════════════════════════
# 오늘 종목의 점검 결과
# ════════════════════════════════════════════════════════════
def current_checks(f: pd.DataFrame) -> dict:
    last = f.iloc[-1]
    return {
        "score": int(last["score"]),
        "max_day_share": float(last["max_day_share"]) * 100,
        "buy_pressure": float(last["buy_pressure"]),
        "up_wick": float(last["up_wick"]) * 100,
        "pos_52w": None if pd.isna(last["pos_52w"]) else float(last["pos_52w"]),
        "volatility_20d": float(last["volatility_20d"]),
        "box_high": float(last["box_high"]),
        "box_low": float(last["box_low"]),
        "close": float(last["close"]),
        "items": [bool(x) for x in [
            last["max_day_share"] < C.SPREAD_MAX_DAY_SHARE,
            last["buy_pressure"] > 0,
            (not pd.isna(last["pos_52w"])) and last["pos_52w"] < 50,
            last["volatility_20d"] >= C.PINNED_VOLATILITY_PCT,
        ]],
    }


# ════════════════════════════════════════════════════════════
# 세력 이탈(털고 나간 주식) 판단 — 가격·거래량 부분
# ════════════════════════════════════════════════════════════
def dump_price_evidence(hist: pd.DataFrame, feat: pd.DataFrame) -> dict:
    """최근 1년 일봉으로 '급등 → 고점 대량거래 → 급락 → 관심 소멸' 패턴을 찾는다."""
    h = hist[hist["Volume"] > 0].tail(250)
    hi, lo, c, v, o = h["High"], h["Low"], h["Close"], h["Volume"], h["Open"]
    peak_i = int(np.argmax(hi.values))
    peak_date, peak = h.index[peak_i], float(hi.iloc[peak_i])
    start = max(0, peak_i - 120)
    pre = lo.iloc[start:peak_i + 1]
    low_i = start + int(np.argmin(pre.values))
    base_low = float(lo.iloc[low_i])
    pump = (peak / base_low - 1) * 100 if base_low > 0 else 0.0
    pump_days = peak_i - low_i
    close = float(c.iloc[-1])
    dd = (close / peak - 1) * 100
    days_since_peak = len(h) - 1 - peak_i

    # 고점 근처(±15거래일)에 1년 중 거래량 상위 10일이 몰렸나 + 그날들 봉 모양
    top = v.nlargest(10)
    pos = [h.index.get_loc(d) for d in top.index]
    near = [p for p in pos if abs(p - peak_i) <= 15]
    rng = (hi - lo).replace(0, np.nan)
    clv = (((c - lo) - (hi - c)) / rng).fillna(0)
    wick = ((hi - np.maximum(o, c)) / rng).fillna(0)
    near_clv = float(np.average(clv.iloc[near], weights=v.iloc[near])) if near else 0.0
    near_wick = float(np.average(wick.iloc[near], weights=v.iloc[near])) if near else 0.0

    # 관심 소멸: 고점 구간 평균 거래량 대비 최근(급증 직전 60일) 거래량
    peak_vol = float(v.iloc[max(0, peak_i - 15): peak_i + 16].mean())
    quiet = v.iloc[-(60 + C.RECENT_DAYS):-C.RECENT_DAYS]
    dry = float(quiet.mean() / peak_vol) if peak_vol > 0 and len(quiet) else float("nan")

    last = feat.iloc[-1]
    return {
        "peak_date": peak_date.date(), "peak": peak, "base_low": base_low, "pump_pct": pump,
        "pump_days": pump_days, "drawdown_pct": dd, "days_since_peak": days_since_peak,
        "peak_top_vol_days": len(near), "peak_clv": near_clv, "peak_wick": near_wick * 100,
        "dry_ratio": dry, "cmf20": float(last["cmf20"]), "buy_pressure": float(last["buy_pressure"]),
        "peak_is_recent": days_since_peak <= C.RECENT_DAYS + C.BASE_DAYS,
    }
