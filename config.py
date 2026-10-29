"""스크리닝 조건 설정. 숫자만 바꾸면 조건이 바뀝니다."""

# ── 거래량 조건 ──────────────────────────────────────────
RECENT_DAYS = 5          # 최근 N거래일 평균 거래량
BASE_DAYS = 20           # 비교 기준: 그 직전 N거래일 평균 거래량
VOLUME_MULTIPLE = 3.0    # 최근 평균 / 기준 평균 >= 3배

# ── 주가 조건 (최근 한 달 = 20거래일 수익률) ─────────────
PRICE_LOOKBACK_DAYS = 20
MIN_RETURN_PCT = 0.0     # 0% 이상
MAX_RETURN_PCT = 5.0     # +5% 이하

# ── 잡주 제외 필터 ───────────────────────────────────────
KR_MIN_MARCAP_KRW = 100_000_000_000   # 시가총액 1,000억 원 이상
US_MIN_MARCAP_USD = 300_000_000       # 시가총액 3억 달러 이상
KR_MIN_PRICE = 1_000                  # 1,000원 미만 동전주 제외
US_MIN_PRICE = 1.0                    # 1달러 미만 페니주 제외
# 기준기간 평균 거래대금이 너무 작은 종목 제외 (거래 없던 종목의 '착시 3배' 방지)
KR_MIN_BASE_TURNOVER_KRW = 300_000_000   # 하루 평균 3억 원
US_MIN_BASE_TURNOVER_USD = 500_000       # 하루 평균 50만 달러

# ── 수집 설정 ────────────────────────────────────────────
HISTORY_PERIOD = "2y"    # 2년치: 상승 확률 계산(과거 신호 통계)과 1년 범위 위치에 사용
DOWNLOAD_CHUNK = 200     # yfinance 한 번에 받는 종목 수

# ── 분석 리포트 ──────────────────────────────────────────
FORWARD_DAYS = 20             # 신호 뒤 며칠(거래일)을 볼지 (대표 확률)
HORIZONS = [5, 7, 10, 20]     # 보유 기간별 승률을 계산할 기간(거래일)
TARGET_UP_PCT = 10.0          # "그 기간 중 +10% 도달" 확률
STOP_DOWN_PCT = 10.0          # "그 기간 중 -10% 하락" 확률
MIN_SAMPLES = 30              # 통계로 쓸 최소 표본 수
SPREAD_MAX_DAY_SHARE = 0.40   # 5일 중 하루 거래량 비중이 40% 미만이면 '고르게 증가'
PINNED_VOLATILITY_PCT = 0.3   # 20일 일간 변동성이 0.3% 미만이면 '가격 고정' 의심
DISCLOSURE_DAYS = 90          # 최근 며칠 공시를 볼지
REPORT_NEAR_COUNT = 3         # 조건 충족 종목이 없을 때 '참고' 종목 몇 개를 분석할지

# ── 세력 이탈 판단 ───────────────────────────────────────
DUMP_PUMP_PCT = 60.0          # 1년 안에 저점→고점 +60% 이상 급등 이력이 있으면 '세력 개입' 의심
DUMP_DRAWDOWN_PCT = 30.0      # 그 고점에서 −30% 이상 빠져 있으면 '급등 후 급락'
DUMP_HIGH_SCORE = 60          # 위험도 60점 이상 = 높음
DUMP_MID_SCORE = 30           # 30~59점 = 중간

# ── ★ 관심 후보 조건 (모두 만족해야 배지) ─────────────────
CAND_MIN_PROB = 55.0          # 상승 확률 55% 이상
CAND_MIN_EDGE = 5.0           # 같은 신호 전체 평균보다 5%p 이상 높을 것
CAND_MIN_SAMPLES = 50         # 표본 50건 이상

# ── 보유 종목 매도 판단 (trades.bat 으로 입력한 매수 기록) ─────
STOP_LOSS_PCT = 7.0           # 매수가 대비 −7%면 손절
TAKE_PROFIT_PCT = 10.0        # +10% 도달하면 일부 익절 고려
TRAIL_PCT = 7.0               # +10% 넘은 뒤에는 '매수 후 최고 종가'에서 −7% 빠지면 매도(이익 보호)
TIME_EXIT_MIN_RET = 3.0       # 통계상 최적 보유 기간이 지났는데 수익이 +3% 미만이면 정리 고려

# SEC(미국 공시)는 연락처가 담긴 User-Agent를 요구합니다. 본인 이메일로 바꾸면 더 안정적입니다.
SEC_USER_AGENT = "personal stock screener research (contact: screener@example.com)"
