import hashlib
import secrets
import time
from typing import Optional


class CaptchaStore:
    _store: dict[str, dict] = {}

    @classmethod
    def generate(cls, ttl: int = 120) -> dict:
        a = secrets.randbelow(10) + 1
        b = secrets.randbelow(10) + 1
        op = secrets.choice(["+", "-"])
        if op == "+":
            answer = a + b
        else:
            if a < b:
                a, b = b, a
            answer = a - b
        raw = f"{a}{op}{b}{time.time()}{secrets.token_hex(4)}"
        cid = hashlib.sha256(raw.encode()).hexdigest()[:12]
        cls._store[cid] = {"answer": answer, "ts": time.time(), "ttl": ttl}
        return {"captcha_id": cid, "question": f"{a} {op} {b} = ?"}

    @classmethod
    def verify(cls, captcha_id: str, answer: str) -> bool:
        record = cls._store.pop(captcha_id, None)
        if not record:
            return False
        if time.time() - record["ts"] > record["ttl"]:
            return False
        try:
            return int(answer.strip()) == record["answer"]
        except (ValueError, TypeError):
            return False

    @classmethod
    def cleanup(cls):
        now = time.time()
        expired = [k for k, v in cls._store.items() if now - v["ts"] > v["ttl"]]
        for k in expired:
            cls._store.pop(k, None)
