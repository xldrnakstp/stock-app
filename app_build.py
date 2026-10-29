"""
클라우드(GitHub Actions)에서 실행: 분석 → 앱용 데이터 암호화 저장 → 폰 알림

  python app_build.py --mode full       평일 아침: 전체 스크리닝 + 보유 종목 점검
  python app_build.py --mode holdings   보유 종목만 다시 점검 (앱에서 매수·매도 입력 시, 평일 16:10)

필요한 환경변수: APP_PASSWORD (앱 비밀번호), DART_API_KEY (선택)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import cloud_crypto as X
import cloud_push as P
import config as C

BASE = Path(__file__).parent
DOCS = BASE / "docs"
DATA_FILE = DOCS / "data.enc"
STATUS_FILE = DOCS / "status.json"
TRADES_ENC = BASE / "trades.enc"
KST = timezone(timedelta(hours=9))
STATUS_ORDER = {"sell": 0, "warn": 1, "take": 2, "hold": 3}


def log(msg):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def _clean(v):
    """JSON으로 보낼 수 있게 (NaN → None, 날짜 → 문자열)"""
    if isinstance(v, float):
        return None if v != v else round(v, 4)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    try:
        import numpy as np
        if isinstance(v, np.generic):
            return _clean(v.item())
    except Exception:
        pass
    return v


# ════════════════════════════════════════════════════════════
# 매매 기록 (앱이 trades.enc 에 암호화해서 저장)
# ════════════════════════════════════════════════════════════
def load_app_trades(pw: str) -> list[dict]:
    if not TRADES_ENC.exists():
        return []
    return X.decrypt_obj(TRADES_ENC.read_text(), pw).get("trades", [])


def trades_to_csv(trades: list[dict]) -> Path:
    import trades as TR
    rows = [[t.get("code", ""), t.get("name", ""), t.get("buy_date", ""), t.get("buy_price", ""), t.get("qty", ""),
             t.get("sell_date", ""), t.get("sell_price", ""), t.get("memo", "")] for t in trades]
    path = Path(tempfile.mkdtemp()) / "my_trades.csv"
    pd.DataFrame(rows, columns=TR.COLS).astype(str).replace({"None": ""}).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def evaluate_holdings(pw: str, probs: dict, hist: dict | None = None, marcaps: dict | None = None) -> dict:
    import trades as TR
    trades = load_app_trades(pw)
    TR.TRADES_FILE = trades_to_csv(trades)
    open_, closed = TR.check_all(probs)
    detail = TR.analyze_holdings(open_, probs, hist or {}, marcaps or {})
    html = TR.holdings_html(open_, closed) + detail
    ids = [t.get("id") for t in trades]
    hold = []
    for o in open_:
        item = {"id": ids[o["row"]] if o.get("row") is not None and o["row"] < len(ids) else None,
                "name": o.get("name"), "ticker": o.get("ticker"), "region": o.get("region"), "ok": o.get("ok"),
                "error": o.get("error"), "buy_date": o.get("buy_date"), "buy_price": o.get("buy_price"),
                "qty": o.get("qty"), "etf": o.get("etf", False)}
        if o.get("ok"):
            st, label = o["status"]
            item.update({"close": o["close"], "last_date": o["last_date"], "ret": o["ret"], "pnl": o["pnl"],
                         "status": st, "label": label, "reasons": o["reasons"], "stop": o["stop"],
                         "target": o["target"], "trail": o["trail"], "support": o["support"], "held": o["held"],
                         "best": o["best"], "max_ret": o["max_ret"],
                         "dump_level": (o.get("dump") or {}).get("level"), "dump_score": (o.get("dump") or {}).get("score")})
        hold.append(item)
    hold.sort(key=lambda h: STATUS_ORDER.get(h.get("status"), -1) if h.get("ok") else -1)
    closed_out = [{"id": ids[c["row"]] if c["row"] < len(ids) else None, **{k: c[k] for k in
                   ("name", "code", "buy_date", "sell_date", "buy", "sell", "ret", "days", "region")}} for c in closed]
    return {"holdings": _clean(hold), "closed": _clean(closed_out), "hold_html": html}


# ════════════════════════════════════════════════════════════
# 추천 종목 목록 (앱 목록 화면용)
# ════════════════════════════════════════════════════════════
def picks_from_sections(sections: list[dict]) -> list[dict]:
    out = []
    for s in sections:
        carded = "".join(s.get("cards", []))
        for kind, df in (("hit", s["hits"]), ("near", s["near"])):
            if not len(df):
                continue
            for _, r in df.iterrows():
                out.append(_clean({
                    "region": s["region"], "kind": kind, "name": str(r["name"]), "ticker": r["ticker"],
                    "market": str(r.get("market", "")), "close": float(r["close"]),
                    "ret": float(r["return_1m_pct"]), "vol_ratio": float(r["vol_ratio"]),
                    "marcap": float(r["marcap"]) if r.get("marcap") == r.get("marcap") else None,
                    "prob": r.get("prob_txt", "") or "", "dump": r.get("dump_txt", "") or "",
                    "star": bool(r.get("cand_star", False)),
                    "has_card": f'id="r-{r["ticker"]}"' in carded}))
    return out


# ════════════════════════════════════════════════════════════
# 저장 · 알림
# ════════════════════════════════════════════════════════════
def load_previous(pw: str) -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return X.decrypt_obj(DATA_FILE.read_text(), pw)
    except Exception as e:
        log(f"이전 데이터를 풀 수 없음 (비밀번호 변경?): {e}")
        return {}


def save(data: dict, pw: str) -> None:
    DOCS.mkdir(exist_ok=True)
    DATA_FILE.write_text(X.encrypt_obj(data, pw))
    STATUS_FILE.write_text(json.dumps({"updated": data["generated_at"], "mode": data["mode"],
                                       "run_date": data.get("run_date")}))
    log(f"저장: {DATA_FILE} ({DATA_FILE.stat().st_size // 1024}KB, 암호화)")


def changed_files() -> list[str]:
    try:
        out = subprocess.run(["git", "diff", "--name-only", "HEAD~1", "HEAD"], capture_output=True, text=True, cwd=BASE)
        return out.stdout.split()
    except Exception:
        return []


def holding_alerts(hold: list[dict]) -> list[str]:
    return [f"{h['name']}({h['label'].split('(')[-1].rstrip(')') if '(' in h['label'] else h['label']})"
            for h in hold if h.get("ok") and h.get("status") in ("sell", "warn")]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "holdings"], default="full")
    args = ap.parse_args()
    pw = os.environ.get("APP_PASSWORD", "")
    if len(pw) < 4:
        print("APP_PASSWORD(앱 비밀번호)가 설정되지 않았습니다. GitHub 저장소 Settings → Secrets 에서 추가하세요.")
        return 1

    import report as RP
    import trades as TR
    from trades import HOLD_CSS

    P.ensure_keys(pw)
    prev = load_previous(pw)
    mode = args.mode
    if mode == "holdings" and not prev.get("main_html"):
        log("이전 스크리닝 결과가 없어 전체 분석으로 진행합니다")
        mode = "full"

    now = datetime.now(KST)
    data = {"v": 1, "generated_at": now.isoformat(timespec="seconds"), "mode": mode,
            "css": RP.CSS + HOLD_CSS}
    if mode == "full":
        import screener as S
        r = S.run_screen("ALL")
        TR.save_caches(r["names"], r["probs"])
        stats = {reg: (p or {}).get("all") for reg, p in r["probs"].items()}
        data.update({"run_date": r["run_date"], "screened_at": data["generated_at"], "cond": S._cond_text(),
                     "main_html": RP.build_main_body(r["sections"]), "picks": picks_from_sections(r["sections"]),
                     "stats": _clean(stats)})
        h = evaluate_holdings(pw, r["probs"], r["hist"], r["marcaps"])
    else:
        for k in ("run_date", "screened_at", "cond", "main_html", "picks", "stats"):
            data[k] = prev.get(k)
        h = evaluate_holdings(pw, TR._stats_cache())
    data.update(h)
    log(f"보유 {len(h['holdings'])}건 · 매도 완료 {len(h['closed'])}건")
    save(data, pw)

    # ── 알림 ──
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    changed = changed_files() if event == "push" else []
    alerts = holding_alerts(h["holdings"])
    try:
        if "subs.enc" in changed:
            P.send_all(pw, "🔔 알림이 연결됐습니다", "매일 아침 분석 결과와 보유 종목 매도 신호를 이 기기로 알려드립니다.")
        elif mode == "full":
            stars = [p["name"] for p in data["picks"] if p["star"]]
            hits = {reg: sum(1 for p in data["picks"] if p["kind"] == "hit" and p["region"] == reg) for reg in ("KR", "US")}
            body = (f"★ 관심 후보: {', '.join(stars[:3])}" if stars else "★ 관심 후보 없음") + \
                   f" · 조건 충족 한국 {hits['KR']} / 미국 {hits['US']}"
            if alerts:
                body += f"\n⚠️ 보유 종목: {', '.join(alerts[:3])}"
            P.send_all(pw, f"📈 {now:%m/%d} 오늘의 분석", body)
        elif event == "schedule":
            old = {(x.get("id"), x.get("status")) for x in prev.get("holdings", []) if x.get("status") in ("sell", "warn")}
            new = {(x.get("id"), x.get("status")) for x in h["holdings"] if x.get("status") in ("sell", "warn")}
            if new - old:
                P.send_all(pw, "⚠️ 보유 종목 매도 신호", ", ".join(alerts[:4]))
    except Exception as e:
        log(f"알림 실패: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
