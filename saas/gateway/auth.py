import base64
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class AuthCredentials:
    user_id: str
    role_name: str = "GUEST"
    token_type: str = "hmac"
    created_at: float = 0.0


class BaseTokenHandler(ABC):
    """
    Абстрактный контракт для валидации и выпуска пользовательских и сервисных токенов.
    """

    @abstractmethod
    def create_token(self, user_id: str, role_name: str = "GUEST", **kwargs) -> str:
        pass

    @abstractmethod
    def verify_token(self, raw_token: str) -> AuthCredentials | None:
        pass


class HmacCookieTokenHandler(BaseTokenHandler):
    """
    Выпускает криптографически подписанные токены (HMAC-SHA256) для анонимных веб-пользователей.
    Формат: base64(json_payload).hex_signature
    """

    def __init__(self, secret_key: str = "ticketpro_saas_default_secret_key_change_me") -> None:
        self.secret_bytes = secret_key.encode("utf-8")

    def create_token(self, user_id: str, role_name: str = "GUEST", **kwargs) -> str:
        payload = {
            "uid": user_id,
            "role": role_name,
            "ts": time.time(),
        }
        raw_json = json.dumps(payload).encode("utf-8")
        b64_payload = base64.urlsafe_b64encode(raw_json).decode("utf-8")
        sig = hmac.new(self.secret_bytes, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{b64_payload}.{sig}"

    def verify_token(self, raw_token: str) -> AuthCredentials | None:
        if not raw_token or "." not in raw_token:
            return None

        parts = raw_token.split(".", 1)
        if len(parts) != 2:
            return None

        b64_payload, signature = parts
        expected_sig = hmac.new(self.secret_bytes, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None

        try:
            raw_json = base64.urlsafe_b64decode(b64_payload.encode("utf-8")).decode("utf-8")
            data = json.loads(raw_json)
            return AuthCredentials(
                user_id=data["uid"],
                role_name=data.get("role", "GUEST"),
                token_type="hmac",
                created_at=float(data.get("ts", 0.0)),
            )
        except Exception:
            return None


class ServiceSecretTokenHandler(BaseTokenHandler):
    """
    Авторизация для внутренних демонов (Telegram Bot, Microservices).
    Формат: srv:<service_name>:<secret_hash>
    """

    def __init__(self, service_secrets: dict[str, str] | None = None) -> None:
        # dict: {service_name: secret_value}
        self.service_secrets = service_secrets or {
            "telegram": "tg_default_service_secret_key_123",
            "admin": "admin_service_secret_key_456",
        }

    def create_token(self, user_id: str, role_name: str = "ADMIN", service_name: str = "telegram", **kwargs) -> str:
        secret = self.service_secrets.get(service_name, "")
        return f"srv:{service_name}:{user_id}:{role_name}:{secret}"

    def verify_token(self, raw_token: str) -> AuthCredentials | None:
        if not raw_token or not raw_token.startswith("srv:"):
            return None

        parts = raw_token.split(":")
        if len(parts) != 5:
            return None

        _, service_name, user_id, role_name, secret = parts
        expected_secret = self.service_secrets.get(service_name)
        if not expected_secret or not hmac.compare_digest(secret, expected_secret):
            return None

        return AuthCredentials(
            user_id=user_id,
            role_name=role_name,
            token_type="service",
            created_at=time.time(),
        )


class CompositeTokenHandler(BaseTokenHandler):
    """
    Универсальный фасад: проверяет токен по цепочке доступных обработчиков.
    """

    def __init__(self, handlers: list[BaseTokenHandler] | None = None) -> None:
        self.handlers = handlers or [
            ServiceSecretTokenHandler(),
            HmacCookieTokenHandler(),
        ]

    def create_token(self, user_id: str, role_name: str = "GUEST", **kwargs) -> str:
        # По умолчанию создаем HMAC-cookie токен
        return self.handlers[-1].create_token(user_id=user_id, role_name=role_name, **kwargs)

    def verify_token(self, raw_token: str) -> AuthCredentials | None:
        for handler in self.handlers:
            creds = handler.verify_token(raw_token)
            if creds:
                return creds
        return None
