import hashlib
import random
import time
from config import settings


class CaptchaService:
    _store: dict[str, dict] = {}

    @classmethod
    def generate(cls) -> dict:
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        op = random.choice(["+", "-", "*"])
        if op == "+":
            answer = a + b
        elif op == "-":
            if a < b:
                a, b = b, a
            answer = a - b
        else:
            answer = a * b
        cid = hashlib.md5(f"{a}{op}{b}{time.time()}{random.random()}".encode()).hexdigest()[:12]
        cls._store[cid] = {"answer": answer, "ts": time.time()}
        return {"captcha_id": cid, "question": f"{a} {op} {b} = ?"}

    @classmethod
    def verify(cls, captcha_id: str, answer: str) -> bool:
        record = cls._store.pop(captcha_id, None)
        if not record:
            return False
        if time.time() - record["ts"] > 120:
            return False
        try:
            return int(answer) == record["answer"]
        except (ValueError, TypeError):
            return False
