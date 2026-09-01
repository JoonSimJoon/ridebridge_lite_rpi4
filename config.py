import os
from dataclasses import dataclass


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    HOST: str = os.environ.get("RIDEBRIDGE_HOST", "0.0.0.0")
    PORT: int = _env_int("RIDEBRIDGE_PORT", 5000)
    DEBUG: bool = _env_bool("RIDEBRIDGE_DEBUG", False)
    APP_VERSION: str = "1.0.0"
    MYMEMORY_URL: str = "https://api.mymemory.translated.net/get"
    SOS_NUMBER: str = "1330"
    # Raspberry Pi 4의 3.5mm 잭은 출력 전용이다. 별도의 USB/오디오
    # 입력 장치를 연결했을 때만 명시적으로 켜도록 기본값을 False로 둔다.
    AUDIO_INPUT_ENABLED: bool = _env_bool("RIDEBRIDGE_AUDIO_INPUT_ENABLED", False)
    AUDIO_OUTPUT: str = os.environ.get("RIDEBRIDGE_AUDIO_OUTPUT", "3.5mm")
    AUDIO_DEVICE: str = os.environ.get("RIDEBRIDGE_AUDIO_DEVICE", "default")
    AUDIO_SAMPLE_RATE: int = _env_int("RIDEBRIDGE_AUDIO_RATE", 16000)
    AUDIO_MAX_SECONDS: int = _env_int("RIDEBRIDGE_AUDIO_MAX_SECONDS", 30)
    AUDIO_STT_LANGUAGE: str = os.environ.get(
        "RIDEBRIDGE_AUDIO_LANGUAGE", "ko-KR"
    )
    SSL_CERT_PATH: str = os.environ.get("RIDEBRIDGE_SSL_CERT", "")
    SSL_KEY_PATH: str = os.environ.get("RIDEBRIDGE_SSL_KEY", "")


CONFIG = Config()
