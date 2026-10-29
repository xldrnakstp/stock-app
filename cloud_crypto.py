"""
앱 데이터 암호화 (AES-256-GCM, 비밀번호 → PBKDF2-SHA256 키)
브라우저(앱)의 WebCrypto와 같은 방식이라 서로 풀 수 있습니다.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 210_000


def _key(password: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode("utf-8"))


def encrypt_obj(obj, password: str) -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    plain = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    ct = AESGCM(_key(password, salt)).encrypt(iv, plain, None)
    b = lambda x: base64.b64encode(x).decode()
    return json.dumps({"v": 1, "iter": ITERATIONS, "salt": b(salt), "iv": b(iv), "ct": b(ct)})


def decrypt_obj(text: str, password: str):
    d = json.loads(text)
    u = base64.b64decode
    salt, iv, ct = u(d["salt"]), u(d["iv"]), u(d["ct"])
    k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                   iterations=int(d.get("iter", ITERATIONS))).derive(password.encode("utf-8"))
    return json.loads(AESGCM(k).decrypt(iv, ct, None).decode("utf-8"))
