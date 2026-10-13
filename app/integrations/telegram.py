"""Telegram Bot API. Токен — только из ENV/настроек (не в коде и не в открытом хранилище БД)."""
import json
from urllib.request import Request, urlopen

def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, str, str]:
    """Возвращает (ok, message_id, error). Успех — только при реальном ответе Telegram API."""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            j = json.loads(resp.read().decode())
        if j.get("ok"):
            return True, str(j["result"]["message_id"]), ""
        return False, "", j.get("description", "unknown")
    except Exception as e:
        return False, "", str(e)

def get_setting_value(db, key: str, default=None):
    from ..models import Setting
    s = db.query(Setting).filter_by(key=key).first()
    if s and isinstance(s.value, dict):
        v = s.value.get("value")
        return v if v is not None else default
    return default
