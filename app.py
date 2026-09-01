import atexit
import math
from pathlib import Path
from threading import Lock

from flask import Flask, abort, jsonify, render_template, request

from config import CONFIG
from services.state import StateStore
from services.translator import Translator, TranslationError
from services.corrector import LocalCorrectionEngine
from services.data_service import DataService
from services.currency import CurrencyService
from services.audio_control import PTTAudioService
from services.gpio_control import GPIOController

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

app = Flask(__name__)
app.config["DEBUG"] = CONFIG.DEBUG
state = StateStore()
translator = Translator(CONFIG.MYMEMORY_URL)
corrector = LocalCorrectionEngine(DATA_DIR)
data_service = DataService(DATA_DIR)
currency_service = CurrencyService()

SUPPORTED_LANGS = {"ko", "en", "ja", "zh"}
SUPPORTED_CURRENCIES = {"USD", "JPY", "CNY", "EUR", "KRW"}
MAX_MESSAGE_BYTES = 500
RIDE_STATES = {"START", "DRIVING", "ARRIVING", "STOPPED"}
CONFIRM_MESSAGES = {
    "ko": "기사가 요청을 확인했습니다.",
    "en": "Driver confirmed your request.",
    "ja": "運転手がリクエストを確認しました。",
    "zh": "司机已确认您的请求。",
}

gpio_controller = None
ptt_audio = None
_hardware_status_lock = Lock()
_base_hardware_status = "idle"
_ptt_active = False


def set_hardware_status(status):
    """Set the persistent LED state without interrupting an active PTT blink."""
    global _base_hardware_status
    with _hardware_status_lock:
        _base_hardware_status = status
        controller = gpio_controller
        if controller is not None and not _ptt_active:
            controller.set_status(status)


def hardware_snapshot():
    audio = ptt_audio.snapshot() if ptt_audio is not None else {
        "available": False,
        "input_enabled": False,
        "recording": False,
        "processing": False,
        "reason": "audio service is not initialized",
    }
    return {
        "gpio_available": bool(gpio_controller and gpio_controller.available),
        "gpio_backend": gpio_controller.backend if gpio_controller else "disabled",
        "gpio_error": (
            str(gpio_controller.initialization_error)
            if gpio_controller and gpio_controller.initialization_error
            else None
        ),
        "status": gpio_controller.status if gpio_controller else "idle",
        "ptt_audio_connected": audio["available"],
        "audio_output": CONFIG.AUDIO_OUTPUT,
        "audio": audio,
    }


def json_payload():
    """Return an object payload without turning malformed JSON into an HTML error."""
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def message_too_long(text):
    return len(text.encode("utf-8")) > MAX_MESSAGE_BYTES


def server_ssl_context():
    """Return a validated Flask SSL context configured through environment vars."""
    cert = CONFIG.SSL_CERT_PATH
    key = CONFIG.SSL_KEY_PATH
    if not cert and not key:
        return None
    if not cert or not key:
        raise RuntimeError(
            "RIDEBRIDGE_SSL_CERT와 RIDEBRIDGE_SSL_KEY를 함께 설정해야 합니다."
        )
    cert_path = Path(cert).expanduser()
    key_path = Path(key).expanduser()
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError("HTTPS 인증서 또는 개인키 파일을 찾을 수 없습니다.")
    return str(cert_path), str(key_path)


def handle_driver_confirm():
    """Shared implementation for the web and physical confirm buttons."""
    if not state.has_pending_passenger_request():
        return None
    passenger_message = state.latest_passenger_message()
    if passenger_message is None:
        return None

    lang = passenger_message.get("source_lang") or state.get_passenger_lang()
    if lang not in CONFIRM_MESSAGES:
        lang = "en"

    state.mark_passenger_message_confirmed(passenger_message["id"])
    next_status = (
        "request" if state.has_pending_passenger_request() else "confirmed"
    )
    confirmation = state.add_message(
        sender="system",
        audience="passenger",
        kind="driver_confirm",
        related_message_id=passenger_message["id"],
        hardware_status=next_status,
        source_lang="ko",
        target_lang=lang,
        source_text=CONFIRM_MESSAGES["ko"],
        corrected_text=CONFIRM_MESSAGES["ko"],
        correction_hits=[],
        translated_text=CONFIRM_MESSAGES[lang],
        mode="hardware_or_web_control",
    )
    set_hardware_status(next_status)
    return confirmation


def handle_replay_request():
    """Ask the driver browser to replay the latest Korean passenger message."""
    passenger_message = state.latest_passenger_message()
    if passenger_message is None:
        return None

    return state.add_message(
        sender="system",
        audience="driver",
        kind="hardware_replay",
        related_message_id=passenger_message["id"],
        source_lang="ko",
        target_lang="ko",
        source_text=passenger_message.get("translated_text", ""),
        translated_text=passenger_message.get("translated_text", ""),
        mode="hardware_or_web_control",
    )


def handle_driver_text(text, kind="free_text", **extra_payload):
    """Translate and publish driver speech/text through one shared path."""
    passenger_lang = state.get_passenger_lang()
    corrected, hits = corrector.normalize(text, "ko")

    try:
        translated = translator.translate(corrected, "ko", passenger_lang)
        mode = "online_translation"
    except TranslationError:
        translated = "[Translation unavailable] {}".format(corrected)
        mode = "translation_unavailable"

    if mode == "translation_unavailable":
        set_hardware_status("translation_unavailable")
        if "restore_status" in extra_payload:
            extra_payload["restore_status"] = _base_hardware_status

    message = state.add_message(
        sender="driver",
        kind=kind,
        source_lang="ko",
        target_lang=passenger_lang,
        source_text=text,
        corrected_text=corrected,
        correction_hits=hits,
        translated_text=translated,
        mode=mode,
        **extra_payload
    )
    return message


def handle_ptt_pressed():
    """Start an explicitly configured external input, if one is present."""
    global _ptt_active
    audio_available = bool(ptt_audio and ptt_audio.available)
    with _hardware_status_lock:
        if _ptt_active:
            return None
        _ptt_active = True
        controller = gpio_controller
        if controller is not None and audio_available:
            controller.set_status("recording")

    recording_started = bool(ptt_audio and ptt_audio.start_recording())
    if (
        ptt_audio is not None
        and ptt_audio.available
        and not recording_started
        and ptt_audio.last_error
    ):
        handle_ptt_audio_error(ptt_audio.last_error)

    return state.add_message(
        sender="system",
        audience="driver",
        kind="hardware_ptt",
        action="pressed",
        recording_started=recording_started,
        audio_available=audio_available,
        audio_input_enabled=bool(ptt_audio and ptt_audio.input_enabled),
        restore_status=_base_hardware_status,
        source_text="PTT pressed",
        translated_text=(
            "외부 마이크 녹음 중"
            if recording_started
            else (
                "외부 마이크 입력을 시작하지 못했습니다"
                if ptt_audio and ptt_audio.input_enabled
                else "3.5mm 잭은 출력 전용 · 텍스트 입력을 사용하세요"
            )
        ),
        mode="hardware_control",
    )


def handle_ptt_released():
    global _ptt_active
    with _hardware_status_lock:
        if not _ptt_active:
            return None
        _ptt_active = False
        restore_status = _base_hardware_status
        controller = gpio_controller
        if controller is not None:
            controller.set_status(
                "processing" if ptt_audio and ptt_audio.recording else restore_status
            )

    processing_started = bool(
        ptt_audio and ptt_audio.stop_and_transcribe_async()
    )
    if not processing_started and controller is not None:
        controller.set_status(restore_status)

    return state.add_message(
        sender="system",
        audience="driver",
        kind="hardware_ptt",
        action="released",
        restore_status=restore_status,
        processing_started=processing_started,
        audio_available=bool(ptt_audio and ptt_audio.available),
        audio_input_enabled=bool(ptt_audio and ptt_audio.input_enabled),
        source_text="PTT released",
        translated_text=(
            "음성 인식 중" if processing_started else "PTT 입력 종료"
        ),
        mode="hardware_control",
    )


def handle_ptt_transcript(transcript, metadata):
    handle_driver_text(
        transcript,
        kind="ptt_speech",
        input_mode="usb_microphone",
        duration_seconds=metadata.get("duration_seconds"),
        restore_status=_base_hardware_status,
    )


def handle_ptt_audio_error(message):
    state.add_message(
        sender="system",
        audience="driver",
        kind="hardware_ptt_error",
        source_lang="ko",
        target_lang="ko",
        source_text=message,
        translated_text=message,
        mode="usb_microphone_stt",
    )
    with _hardware_status_lock:
        if gpio_controller is not None:
            gpio_controller.set_status("error")


def handle_ptt_audio_complete(success):
    if not success:
        return
    with _hardware_status_lock:
        if not _ptt_active and gpio_controller is not None:
            gpio_controller.set_status(_base_hardware_status)


@app.get("/")
def index():
    return render_template("passenger.html", sos_number=CONFIG.SOS_NUMBER)


@app.get("/passenger")
def passenger():
    return render_template("passenger.html", sos_number=CONFIG.SOS_NUMBER)


@app.get("/driver")
def driver():
    return render_template("driver.html", sos_number=CONFIG.SOS_NUMBER)


@app.get("/api/bootstrap")
def bootstrap():
    return jsonify({
        "app_version": CONFIG.APP_VERSION,
        "passenger_lang": state.get_passenger_lang(),
        "ride_state": state.get_ride_state(),
        "passenger_quick_phrases": data_service.phrases["passenger"],
        "driver_context_phrases": data_service.phrases["driver_context"].get(
            state.get_ride_state(), []
        ),
        "guides": data_service.guides,
        "sos_number": CONFIG.SOS_NUMBER,
        "hardware": hardware_snapshot(),
    })


@app.post("/api/session/language")
def set_language():
    payload = json_payload()
    lang = payload.get("lang", "en")
    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": "지원하지 않는 언어입니다."}), 400
    state.set_passenger_lang(lang)
    return jsonify({"ok": True, "lang": lang})


@app.post("/api/ride/state")
def set_ride_state():
    payload = json_payload()
    ride_state = payload.get("state", "START")
    if ride_state not in RIDE_STATES:
        return jsonify({"error": "지원하지 않는 운행 상태입니다."}), 400

    state.set_ride_state(ride_state)
    return jsonify({
        "ok": True,
        "state": ride_state,
        "phrases": data_service.phrases["driver_context"].get(ride_state, []),
    })


@app.get("/api/messages")
def messages():
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        after = 0
    return jsonify({
        "messages": state.messages_after(after),
        "hardware": hardware_snapshot(),
    })


@app.post("/api/passenger/send")
def passenger_send():
    payload = json_payload()
    text = str(payload.get("text") or "").strip()
    lang = payload.get("lang") or state.get_passenger_lang()

    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": "지원하지 않는 언어입니다."}), 400
    if not text:
        return jsonify({"error": "문장을 입력해 주세요."}), 400
    if message_too_long(text):
        return jsonify({"error": "문장은 UTF-8 기준 500바이트 이하로 입력해 주세요."}), 400

    state.set_passenger_lang(lang)
    corrected, hits = corrector.normalize(text, lang)

    try:
        korean = translator.translate(corrected, lang, "ko")
        mode = "online_translation"
    except TranslationError:
        korean = f"[번역 연결 필요] {corrected}"
        mode = "translation_unavailable"

    msg = state.add_message(
        sender="passenger",
        kind="free_text",
        source_lang=lang,
        target_lang="ko",
        source_text=text,
        corrected_text=corrected,
        correction_hits=hits,
        translated_text=korean,
        mode=mode,
    )
    if mode == "translation_unavailable":
        set_hardware_status("translation_unavailable")
    else:
        set_hardware_status("request")
    return jsonify(msg)


@app.post("/api/passenger/quick")
def passenger_quick():
    payload = json_payload()
    phrase = data_service.passenger_phrase(payload.get("phrase_id"))
    lang = payload.get("lang") or state.get_passenger_lang()

    if phrase is None:
        return jsonify({"error": "빠른 문장을 찾을 수 없습니다."}), 404
    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": "지원하지 않는 언어입니다."}), 400

    state.set_passenger_lang(lang)
    t = phrase["translations"]

    msg = state.add_message(
        sender="passenger",
        kind="quick_phrase",
        phrase_id=phrase["id"],
        source_lang=lang,
        target_lang="ko",
        source_text=t[lang],
        corrected_text=t[lang],
        correction_hits=[],
        translated_text=t["ko"],
        mode="offline_safe_phrase",
    )
    set_hardware_status("request")
    return jsonify(msg)


@app.post("/api/driver/send")
def driver_send():
    payload = json_payload()
    text = str(payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "문장을 입력해 주세요."}), 400
    if message_too_long(text):
        return jsonify({"error": "문장은 UTF-8 기준 500바이트 이하로 입력해 주세요."}), 400

    return jsonify(handle_driver_text(text))


@app.post("/api/driver/confirm")
def driver_confirm():
    confirmation = handle_driver_confirm()
    if confirmation is None:
        return jsonify({"error": "확인할 승객 요청이 없습니다."}), 409
    return jsonify(confirmation)


@app.post("/api/driver/replay")
def driver_replay():
    replay_event = handle_replay_request()
    if replay_event is None:
        return jsonify({"error": "다시 들을 승객 요청이 없습니다."}), 409
    return jsonify(replay_event)


@app.post("/api/driver/quick")
def driver_quick():
    payload = json_payload()
    phrase = data_service.driver_phrase(payload.get("phrase_id"))
    passenger_lang = state.get_passenger_lang()

    if phrase is None:
        return jsonify({"error": "빠른 문장을 찾을 수 없습니다."}), 404

    t = phrase["translations"]
    msg = state.add_message(
        sender="driver",
        kind="context_quick_phrase",
        phrase_id=phrase["id"],
        source_lang="ko",
        target_lang=passenger_lang,
        source_text=t["ko"],
        corrected_text=t["ko"],
        correction_hits=[],
        translated_text=t[passenger_lang],
        mode="offline_safe_phrase",
    )
    return jsonify(msg)


@app.get("/api/currency")
def currency():
    try:
        amount = float(request.args.get("amount", "0"))
    except ValueError:
        return jsonify({"error": "금액 형식이 올바르지 않습니다."}), 400

    quote = request.args.get("to", "USD").upper()
    if not math.isfinite(amount) or amount < 0:
        return jsonify({"error": "금액은 0 이상이어야 합니다."}), 400
    if quote not in SUPPORTED_CURRENCIES:
        return jsonify({"error": "지원하지 않는 통화입니다."}), 400

    try:
        return jsonify(currency_service.convert_from_krw(amount, quote))
    except Exception as exc:
        return jsonify({"error": f"환율 조회 실패: {exc}"}), 503


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "app_version": CONFIG.APP_VERSION,
        "debug": app.debug,
        "ride_state": state.get_ride_state(),
        "passenger_lang": state.get_passenger_lang(),
        "gpio_available": bool(gpio_controller and gpio_controller.available),
        "gpio_backend": gpio_controller.backend if gpio_controller else "disabled",
        "gpio_error": (
            str(gpio_controller.initialization_error)
            if gpio_controller and gpio_controller.initialization_error
            else None
        ),
        "hardware_status": gpio_controller.status if gpio_controller else "idle",
        "audio_output": CONFIG.AUDIO_OUTPUT,
        "audio": ptt_audio.snapshot() if ptt_audio else None,
    })


def require_debug_mode():
    if not app.debug:
        abort(404)


@app.post("/api/debug/gpio/confirm")
def debug_gpio_confirm():
    require_debug_mode()
    confirmation = gpio_controller.simulate_confirm()
    if confirmation is None:
        return jsonify({"error": "확인할 승객 요청이 없습니다."}), 409
    return jsonify(confirmation)


@app.post("/api/debug/gpio/ptt/press")
def debug_gpio_ptt_press():
    require_debug_mode()
    event = gpio_controller.simulate_ptt_pressed()
    if event is None:
        return jsonify({"ok": True, "status": "recording", "duplicate": True})
    return jsonify(event)


@app.post("/api/debug/gpio/ptt/release")
def debug_gpio_ptt_release():
    require_debug_mode()
    event = gpio_controller.simulate_ptt_released()
    if event is None:
        return jsonify({"ok": True, "status": gpio_controller.status, "duplicate": True})
    return jsonify(event)


@app.post("/api/debug/gpio/replay")
def debug_gpio_replay():
    require_debug_mode()
    replay_event = gpio_controller.simulate_replay()
    if replay_event is None:
        return jsonify({"error": "다시 들을 승객 요청이 없습니다."}), 409
    return jsonify(replay_event)


ptt_audio = PTTAudioService(
    on_transcript=handle_ptt_transcript,
    on_error=handle_ptt_audio_error,
    on_complete=handle_ptt_audio_complete,
    device=CONFIG.AUDIO_DEVICE,
    sample_rate=CONFIG.AUDIO_SAMPLE_RATE,
    max_seconds=CONFIG.AUDIO_MAX_SECONDS,
    language=CONFIG.AUDIO_STT_LANGUAGE,
    enabled=CONFIG.AUDIO_INPUT_ENABLED,
)
gpio_controller = GPIOController(
    on_confirm=handle_driver_confirm,
    on_ptt_pressed=handle_ptt_pressed,
    on_ptt_released=handle_ptt_released,
    on_replay=handle_replay_request,
)
gpio_controller.initialize()


def close_hardware():
    ptt_audio.close()
    gpio_controller.close()


atexit.register(close_hardware)


if __name__ == "__main__":
    # GPIO must be initialized exactly once. The debug reloader would import the
    # module in both its supervisor and worker processes, so keep it disabled.
    app.run(
        host=CONFIG.HOST,
        port=CONFIG.PORT,
        debug=CONFIG.DEBUG,
        use_reloader=False,
        ssl_context=server_ssl_context(),
    )
