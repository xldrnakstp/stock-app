"""
폰 알림(웹 푸시). 처음 실행 때 알림용 키(VAPID)를 자동으로 만들어
  - 공개키  → docs/push.json   (앱이 알림을 구독할 때 사용)
  - 비밀키  → vapid.enc        (앱 비밀번호로 암호화해서 저장)
알림 받을 기기 목록은 앱이 subs.enc(암호화)에 저장합니다.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import cloud_crypto as X

BASE = Path(__file__).parent
PUB_FILE = BASE / "docs" / "push.json"
PRIV_FILE = BASE / "vapid.enc"
SUBS_FILE = BASE / "subs.enc"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def ensure_keys(password: str) -> str:
    """비밀키 PEM 반환 (없으면 새로 만듦)"""
    if PRIV_FILE.exists() and PUB_FILE.exists():
        try:
            return X.decrypt_obj(PRIV_FILE.read_text(), password)["pem"]
        except Exception:
            print("[알림] 기존 알림 키를 풀 수 없어 새로 만듭니다 (앱 비밀번호가 바뀐 경우). 폰에서 알림을 다시 켜주세요.")
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    PUB_FILE.parent.mkdir(exist_ok=True)
    PUB_FILE.write_text(json.dumps({"publicKey": _b64url(pub)}))
    PRIV_FILE.write_text(X.encrypt_obj({"pem": pem}, password))
    if SUBS_FILE.exists():
        SUBS_FILE.unlink()        # 키가 바뀌면 기존 구독은 못 씀
    return pem


def load_subs(password: str) -> list[dict]:
    if not SUBS_FILE.exists():
        return []
    try:
        return X.decrypt_obj(SUBS_FILE.read_text(), password).get("subs", [])
    except Exception as e:
        print(f"[알림] 구독 목록을 풀 수 없음: {e}")
        return []


def send_all(password: str, title: str, body: str, url: str = "./") -> int:
    subs = load_subs(password)
    if not subs:
        print("[알림] 알림을 켠 기기가 없습니다")
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        print("[알림] pywebpush 미설치")
        return 0
    pem = ensure_keys(password)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pem)
        key_path = f.name
    sent = 0
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    for s in subs:
        try:
            webpush(subscription_info=s, data=payload, vapid_private_key=key_path,
                    vapid_claims={"sub": "mailto:stock-app@example.com"}, ttl=12 * 3600)
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            print(f"[알림] 전송 실패({code}): 기기 구독이 만료됐을 수 있음 — 앱 설정에서 알림을 다시 켜주세요")
        except Exception as e:
            print(f"[알림] 전송 실패: {e}")
    os.unlink(key_path)
    print(f"[알림] {sent}/{len(subs)}개 기기로 전송")
    return sent
