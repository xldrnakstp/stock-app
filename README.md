# 주식 레이더 — 거래량 급증 · 주가 정체 종목 스크리너 (한국 · 미국)

GitHub 클라우드에서 매일 자동으로 분석하고, 결과를 폰 앱(홈 화면 웹앱)으로 봅니다.
PC를 켜 둘 필요가 없고, 모두 무료입니다.

> **이 저장소를 복사해서 쓰는 분은** 아래 [내 앱 만들기](#내-앱-만들기-약-20분)부터 따라 하세요.
> 복사본은 완전히 내 것입니다. 내 비밀번호·보유 종목·알림은 원래 주인을 포함해 아무도 볼 수 없습니다.

## 매일 하는 일 (자동)

| 시각 (평일, 한국시간) | 내용 |
|---|---|
| 07:00 | 전체 분석: 한국·미국 스크리닝, 종목 상세 분석, 보유 종목 매도 점검, 알림 |
| 16:10 | 보유 종목 점검 (한국장 마감 반영). 새 매도 신호가 있으면 알림 |
| 앱에서 매수·매도를 입력하면 | 보유 종목만 1~3분 안에 다시 점검 |

GitHub 일정 실행은 서버 사정에 따라 10~60분 늦게 시작할 수 있습니다.

## 내 앱 만들기 (약 20분)

**준비물:** GitHub 계정(무료), 앱 비밀번호로 쓸 문장(8자 이상)

1. **복사:** 이 저장소 오른쪽 위 초록 **Use this template** → **Create a new repository**를 누릅니다.
   - Repository name: `stock-app`
   - **Public** 선택 (데이터는 암호화되어서 공개여도 안전합니다)
   - **Create repository**를 누릅니다.
2. **비밀번호 등록:** 내 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 등록합니다.
   - Name `APP_PASSWORD`, Secret에는 내 앱 비밀번호
   - (선택) Name `DART_API_KEY`, Secret에는 [DART 인증키](https://opendart.fss.or.kr) — 한국 공시 확인용. 없어도 동작합니다.
3. **첫 분석:** **Actions** 탭을 누릅니다. 안내 버튼이 나오면 *enable*을 누르고, **Stock app → Run workflow → Run workflow**를 누릅니다. 10~15분 뒤 ✅가 뜨면 성공입니다.
4. **앱 주소 켜기:** **Settings → Pages**에서 Branch를 **`data`**, 폴더를 **`/ (root)`**로 정하고 **Save**를 누릅니다. 3번이 끝나야 `data`가 목록에 나옵니다.
5. **열기:** `https://내아이디.github.io/stock-app/`에 접속해 앱 비밀번호를 입력합니다.
6. **토큰(매수 입력·알림용):** [토큰 만들기](https://github.com/settings/personal-access-tokens/new)에서 만듭니다.
   - 이름 `stock-app`, 기간은 가장 길게
   - Repository access: **Only select repositories → stock-app**
   - Permissions: **Contents**와 **Actions**를 모두 **Read and write**로
   - **Generate token**을 누르고, 나온 값을 앱 **설정 → GitHub 토큰**에 붙여넣습니다.
7. **폰에 설치:** 앱 설정의 **📱 폰으로 연결 옮기기(QR)**를 폰 카메라로 찍습니다. 비밀번호를 입력한 뒤 **홈 화면에 추가**하고 **🔔 알림 켜기**를 누릅니다.
   - 아이폰은 Safari, 안드로이드는 Chrome을 쓰세요.

## 보안

- 보유 종목·리포트·알림 정보는 모두 **앱 비밀번호로 암호화**(AES-256)되어 `data` 브랜치에 저장됩니다.
- `main` 브랜치에는 프로그램 코드만 있어서, 템플릿으로 복사해도 데이터는 따라가지 않습니다.
- 앱 비밀번호는 GitHub Secrets와 내 기기에만 있습니다. **잊어버리면 되찾을 수 없으니** 따로 적어두세요.
- 토큰과 QR은 다른 사람에게 보여주지 마세요.

## 조건 바꾸기

`config.py`의 숫자를 고치면 됩니다. GitHub 웹에서 연필 아이콘 → 수정 → Commit을 누르세요.

- 거래량: 최근 5일 평균 ≥ 직전 20일 평균 × 3
- 주가: 최근 20거래일 수익률 0~+5%
- 잡주 제외: 시가총액·주가·거래대금 하한, 스팩·ETF·리츠 제외
- 매도 판단: 손절 −7%, +10%에서 일부 익절, 이후 최고가 대비 −7% 하락 시 이익 보호 매도

## 파일

| 파일 | 역할 |
|---|---|
| `screener.py` | 종목 목록·시세 수집, 조건 판정 |
| `analysis.py` | 과거 신호 통계(상승 확률·보유 기간별 승률), 세력 이탈 지표 |
| `evidence.py` | 공시(DART/SEC)·수급(네이버)·공매도 |
| `report.py` | 종목 분석 카드·차트 |
| `trades.py` | 보유 종목 매도 시점 판단 |
| `app_build.py` | 클라우드 실행: 분석 → 암호화 저장 → 알림 |
| `cloud_crypto.py`, `cloud_push.py` | 암호화, 폰 알림 |
| `docs/` | 폰 앱 화면 (실행 때마다 `data` 브랜치로 복사되어 GitHub Pages로 공개) |
| `.github/workflows/app.yml` | 자동 실행 일정 |

투자 판단의 참고 자료이며, 투자 결과의 책임은 본인에게 있습니다.
