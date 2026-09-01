import unittest
import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import app as ridebridge
from services.audio_control import PTTAudioService
from services.gpio_control import GPIOController
from services.state import StateStore
from services.translator import Translator, TranslationError


class SuccessfulTranslator:
    def translate(self, text, source, target):
        if source == "en" and target == "ko":
            return "여기서 내려 주세요."
        return "translated: {}".format(text)


class FailingTranslator:
    def translate(self, text, source, target):
        raise TranslationError("forced test failure")


class AvailablePTTAudioStub:
    def __init__(self):
        self.available = True
        self.input_enabled = True
        self.last_error = None
        self.recording = False
        self.processing = False

    def start_recording(self):
        self.recording = True
        return True

    def stop_and_transcribe_async(self):
        self.recording = False
        self.processing = True
        return True

    def snapshot(self):
        return {
            "available": True,
            "input_enabled": True,
            "recording": self.recording,
            "processing": self.processing,
            "reason": None,
        }


class GPIOFlowTest(unittest.TestCase):
    def setUp(self):
        self.original_translator = ridebridge.translator
        self.original_ptt_audio = ridebridge.ptt_audio
        ridebridge.translator = SuccessfulTranslator()
        ridebridge.state = StateStore()
        ridebridge._base_hardware_status = "idle"
        ridebridge._ptt_active = False
        ridebridge.gpio_controller.status = "idle"
        ridebridge.app.config.update(TESTING=True, DEBUG=True)
        self.client = ridebridge.app.test_client()

    def tearDown(self):
        ridebridge.translator = self.original_translator
        ridebridge.ptt_audio = self.original_ptt_audio

    def send_passenger_text(self):
        return self.client.post(
            "/api/passenger/send",
            json={"text": "Please drop me off here.", "lang": "en"},
        )

    def test_free_text_confirm_and_replay_flow(self):
        sent = self.send_passenger_text()
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.json["translated_text"], "여기서 내려 주세요.")
        self.assertEqual(ridebridge.gpio_controller.status, "request")

        confirmed = self.client.post("/api/debug/gpio/confirm")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json["kind"], "driver_confirm")
        self.assertEqual(
            confirmed.json["translated_text"], "Driver confirmed your request."
        )
        self.assertEqual(ridebridge.gpio_controller.status, "confirmed")
        self.assertEqual(
            self.client.post("/api/debug/gpio/confirm").status_code, 409
        )

        replay = self.client.post("/api/debug/gpio/replay")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json["kind"], "hardware_replay")
        self.assertEqual(replay.json["translated_text"], "여기서 내려 주세요.")

        messages = self.client.get("/api/messages?after=0").json["messages"]
        self.assertEqual(
            [message["kind"] for message in messages],
            ["free_text", "driver_confirm", "hardware_replay"],
        )

    def test_confirmation_is_delivered_in_all_supported_languages(self):
        expected = {
            "ko": "기사가 요청을 확인했습니다.",
            "en": "Driver confirmed your request.",
            "ja": "運転手がリクエストを確認しました。",
            "zh": "司机已确认您的请求。",
        }
        for lang, message in expected.items():
            with self.subTest(lang=lang):
                ridebridge.state = StateStore()
                sent = self.client.post(
                    "/api/passenger/quick",
                    json={"phrase_id": "drop_off_here", "lang": lang},
                )
                self.assertEqual(sent.status_code, 200)
                confirmed = self.client.post("/api/driver/confirm")
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json["translated_text"], message)

    def test_output_only_ptt_keeps_led_state_and_restores_new_status(self):
        quick = self.client.post(
            "/api/passenger/quick",
            json={"phrase_id": "drop_off_here", "lang": "en"},
        )
        self.assertEqual(quick.status_code, 200)
        self.assertEqual(quick.json["mode"], "offline_safe_phrase")
        self.assertEqual(ridebridge.gpio_controller.status, "request")

        pressed = self.client.post("/api/debug/gpio/ptt/press")
        self.assertEqual(pressed.status_code, 200)
        self.assertEqual(pressed.json["action"], "pressed")
        self.assertFalse(pressed.json["recording_started"])
        self.assertFalse(pressed.json["audio_input_enabled"])
        self.assertEqual(ridebridge.gpio_controller.status, "request")

        ridebridge.set_hardware_status("translation_unavailable")
        self.assertEqual(ridebridge.gpio_controller.status, "request")

        released = self.client.post("/api/debug/gpio/ptt/release")
        self.assertEqual(released.status_code, 200)
        self.assertEqual(released.json["restore_status"], "translation_unavailable")
        self.assertEqual(
            ridebridge.gpio_controller.status, "translation_unavailable"
        )

    def test_available_usb_microphone_moves_from_recording_to_processing(self):
        ridebridge.ptt_audio = AvailablePTTAudioStub()

        pressed = self.client.post("/api/debug/gpio/ptt/press")
        self.assertEqual(pressed.status_code, 200)
        self.assertTrue(pressed.json["recording_started"])
        self.assertEqual(ridebridge.gpio_controller.status, "recording")

        released = self.client.post("/api/debug/gpio/ptt/release")
        self.assertEqual(released.status_code, 200)
        self.assertTrue(released.json["processing_started"])
        self.assertEqual(ridebridge.gpio_controller.status, "processing")

    def test_translation_failure_is_red_but_quick_phrase_stays_local(self):
        ridebridge.translator = FailingTranslator()
        sent = self.send_passenger_text()
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.json["mode"], "translation_unavailable")
        self.assertTrue(sent.json["translated_text"].startswith("[번역 연결 필요]"))
        self.assertEqual(
            ridebridge.gpio_controller.status, "translation_unavailable"
        )

        quick = self.client.post(
            "/api/passenger/quick",
            json={"phrase_id": "drop_off_here", "lang": "en"},
        )
        self.assertEqual(quick.status_code, 200)
        self.assertEqual(quick.json["translated_text"], "여기서 내려주세요.")
        self.assertEqual(ridebridge.gpio_controller.status, "request")

    def test_usb_microphone_transcript_is_translated_and_sent_to_passenger(self):
        ridebridge.handle_ptt_transcript(
            "백 미터 앞에서 세워 주세요", {"duration_seconds": 1.4}
        )
        messages = self.client.get("/api/messages?after=0").json["messages"]
        transcript = messages[-1]
        self.assertEqual(transcript["kind"], "ptt_speech")
        self.assertEqual(transcript["sender"], "driver")
        self.assertEqual(transcript["source_lang"], "ko")
        self.assertEqual(transcript["target_lang"], "en")
        self.assertEqual(transcript["input_mode"], "usb_microphone")
        self.assertTrue(transcript["translated_text"].startswith("translated:"))
        self.assertEqual(transcript["duration_seconds"], 1.4)

    def test_ptt_audio_error_is_transient_and_next_success_restores_base_led(self):
        ridebridge._base_hardware_status = "idle"
        ridebridge.handle_ptt_audio_error("forced microphone error")
        self.assertEqual(ridebridge.gpio_controller.status, "error")
        self.assertEqual(ridebridge._base_hardware_status, "idle")

        ridebridge.handle_ptt_audio_complete(True)
        self.assertEqual(ridebridge.gpio_controller.status, "idle")

    def test_debug_routes_are_hidden_when_debug_is_false(self):
        ridebridge.app.config["DEBUG"] = False
        response = self.client.post("/api/debug/gpio/ptt/press")
        self.assertEqual(response.status_code, 404)

    def test_confirm_and_replay_require_a_passenger_message(self):
        self.assertEqual(
            self.client.post("/api/debug/gpio/confirm").status_code, 409
        )
        self.assertEqual(
            self.client.post("/api/debug/gpio/replay").status_code, 409
        )

    def test_api_validation_returns_json_errors(self):
        malformed = self.client.post(
            "/api/passenger/send", data="{", content_type="application/json"
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertTrue(malformed.is_json)

        too_long = self.client.post(
            "/api/driver/send", json={"text": "가" * 501}
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("500바이트", too_long.json["error"])

        self.assertEqual(
            self.client.get("/api/currency?amount=nan&to=USD").status_code, 400
        )
        self.assertEqual(
            self.client.get("/api/currency?amount=1000&to=BTC").status_code, 400
        )

    def test_health_and_pages_expose_submission_metadata(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json["app_version"], "1.0.0")
        self.assertIn("gpio_backend", health.json)

        passenger_html = self.client.get("/passenger").get_data(as_text=True)
        driver_html = self.client.get("/driver").get_data(as_text=True)
        self.assertIn('id="languageHeading"', passenger_html)
        self.assertIn('href="tel:1330"', passenger_html)
        self.assertIn('href="tel:1330"', driver_html)


class ServerConfigTest(unittest.TestCase):
    def test_optional_https_requires_a_complete_existing_key_pair(self):
        with patch(
            "app.CONFIG",
            SimpleNamespace(SSL_CERT_PATH="", SSL_KEY_PATH=""),
        ):
            self.assertIsNone(ridebridge.server_ssl_context())

        with patch(
            "app.CONFIG",
            SimpleNamespace(SSL_CERT_PATH="cert.pem", SSL_KEY_PATH=""),
        ):
            with self.assertRaises(RuntimeError):
                ridebridge.server_ssl_context()

        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "cert.pem"
            key = Path(directory) / "key.pem"
            cert.write_text("test cert", encoding="utf-8")
            key.write_text("test key", encoding="utf-8")
            with patch(
                "app.CONFIG",
                SimpleNamespace(
                    SSL_CERT_PATH=str(cert), SSL_KEY_PATH=str(key)
                ),
            ):
                self.assertEqual(
                    ridebridge.server_ssl_context(), (str(cert), str(key))
                )


class FakeButton:
    instances = []

    def __init__(self, pin, pull_up, bounce_time):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None
        self.when_released = None
        self.closed = False
        self.instances.append(self)

    def close(self):
        self.closed = True


class FakeRGBLED:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.value = kwargs["initial_value"]
        self.closed = False

    def off(self):
        self.value = (0.0, 0.0, 0.0)

    def close(self):
        self.closed = True


class GPIOControllerTest(unittest.TestCase):
    def test_pin_mapping_debounce_active_high_and_cleanup(self):
        FakeButton.instances = []
        with patch("services.gpio_control.GPIO_LIBRARY_AVAILABLE", True), patch(
            "services.gpio_control.Button", FakeButton
        ), patch("services.gpio_control.RGBLED", FakeRGBLED):
            controller = GPIOController()
            self.assertTrue(controller.initialize())
            self.assertEqual(
                [button.pin for button in FakeButton.instances], [17, 27, 22]
            )
            self.assertTrue(all(button.pull_up for button in FakeButton.instances))
            self.assertTrue(
                all(button.bounce_time == 0.08 for button in FakeButton.instances)
            )
            self.assertEqual(
                controller.status_led.kwargs,
                {
                    "red": 5,
                    "green": 6,
                    "blue": 13,
                    "active_high": True,
                    "initial_value": (0.0, 0.0, 0.0),
                },
            )

            controller.set_status("request")
            self.assertEqual(controller.status_led.value, (0.0, 0.0, 1.0))
            led = controller.status_led
            controller.close()
            self.assertTrue(all(button.closed for button in FakeButton.instances))
            self.assertTrue(led.closed)

    def test_rpi_gpio_compatibility_backend_is_used_when_gpiozero_fails(self):
        class FailingButton:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("forced gpiozero pin factory failure")

        fake_gpio = FakeRPIGPIO()
        with patch("services.gpio_control.GPIO_LIBRARY_AVAILABLE", True), patch(
            "services.gpio_control.Button", FailingButton
        ), patch("services.gpio_control.RPI_GPIO_AVAILABLE", True), patch(
            "services.gpio_control.RPI_GPIO", fake_gpio
        ):
            controller = GPIOController()
            self.assertTrue(controller.initialize())
            self.assertEqual(controller.backend, "rpi-lgpio")
            self.assertEqual(
                fake_gpio.input_pins,
                {17: fake_gpio.PUD_UP, 27: fake_gpio.PUD_UP, 22: fake_gpio.PUD_UP},
            )
            self.assertEqual(set(fake_gpio.callbacks), {17, 22, 27})

            controller.set_status("request")
            self.assertEqual(fake_gpio.levels[13], fake_gpio.HIGH)
            self.assertEqual(fake_gpio.levels[5], fake_gpio.LOW)
            controller.close()
            self.assertTrue(fake_gpio.cleaned)


class FakeRPIGPIO:
    BCM = "BCM"
    IN = "IN"
    OUT = "OUT"
    PUD_UP = "PUD_UP"
    LOW = 0
    HIGH = 1
    FALLING = "FALLING"
    BOTH = "BOTH"

    def __init__(self):
        self.input_pins = {}
        self.levels = {}
        self.callbacks = {}
        self.cleaned = False

    def setwarnings(self, _enabled):
        pass

    def setmode(self, _mode):
        pass

    def setup(self, pin, mode, pull_up_down=None):
        if mode == self.IN:
            self.input_pins[pin] = pull_up_down

    def output(self, pin, level):
        self.levels[pin] = level

    def add_event_detect(self, pin, _edge, callback, bouncetime):
        self.callbacks[pin] = (callback, bouncetime)

    def remove_event_detect(self, pin):
        self.callbacks.pop(pin, None)

    def input(self, pin):
        return self.levels.get(pin, self.HIGH)

    def cleanup(self, _pins):
        self.cleaned = True


class FakeAudioProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, _signal):
        self.returncode = -2

    def communicate(self, timeout=None):
        return None, b""

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class FakeAudioFile:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeRecognizer:
    def record(self, source):
        return source.path

    def recognize_google(self, audio_data, language):
        self.audio_data = audio_data
        self.language = language
        return "백 미터 앞에서 세워 주세요"


class FailingRecognizer(FakeRecognizer):
    def recognize_google(self, audio_data, language):
        raise RuntimeError("forced STT failure")


class PTTAudioServiceTest(unittest.TestCase):
    def test_output_only_mode_never_starts_recording(self):
        service = PTTAudioService(
            enabled=False,
            process_factory=lambda *_args, **_kwargs: self.fail(
                "recorder must not start in output-only mode"
            ),
            recognizer_factory=FakeRecognizer,
            audio_file_factory=FakeAudioFile,
        )
        self.assertFalse(service.available)
        self.assertFalse(service.snapshot()["input_enabled"])
        self.assertIn("output-only", service.unavailable_reason)
        self.assertFalse(service.start_recording())
        service.close()


class FakeTranslationResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class TranslatorTest(unittest.TestCase):
    def test_translation_decodes_html_entities(self):
        response = FakeTranslationResponse({
            "responseStatus": 200,
            "responseData": {"translatedText": "Tom &amp; Jerry"},
        })
        with patch("services.translator.requests.get", return_value=response):
            result = Translator("https://example.invalid").translate(
                "톰과 제리", "ko", "en"
            )
        self.assertEqual(result, "Tom & Jerry")

    def test_translation_service_error_is_normalized(self):
        response = FakeTranslationResponse({
            "responseStatus": 429,
            "responseDetails": "rate limited",
            "responseData": {},
        })
        with patch("services.translator.requests.get", return_value=response):
            with self.assertRaises(TranslationError):
                Translator("https://example.invalid").translate("안녕", "ko", "en")


class PTTAudioRecordingTest(unittest.TestCase):
    def test_arecord_release_transcribes_in_background_and_cleans_up(self):
        transcript_event = Event()
        complete_event = Event()
        received = {}
        created_path = {}

        def process_factory(command, **_kwargs):
            path = Path(command[-1])
            path.write_bytes(b"RIFF" + (b"audio" * 20))
            created_path["path"] = path
            created_path["command"] = command
            return FakeAudioProcess()

        def on_transcript(text, metadata):
            received["text"] = text
            received["metadata"] = metadata
            transcript_event.set()

        def on_complete(success):
            received["success"] = success
            complete_event.set()

        service = PTTAudioService(
            on_transcript=on_transcript,
            on_complete=on_complete,
            device="plughw:1,0",
            min_seconds=0,
            process_factory=process_factory,
            recognizer_factory=FakeRecognizer,
            audio_file_factory=FakeAudioFile,
        )

        self.assertTrue(service.available)
        self.assertTrue(service.start_recording())
        self.assertEqual(created_path["command"][2:4], ["-D", "plughw:1,0"])
        self.assertTrue(service.stop_and_transcribe_async())
        self.assertTrue(transcript_event.wait(2))
        self.assertTrue(complete_event.wait(2))
        self.assertEqual(received["text"], "백 미터 앞에서 세워 주세요")
        self.assertTrue(received["success"])
        self.assertFalse(created_path["path"].parent.exists())
        service.close()

    def test_stt_failure_reports_error_and_cleans_up(self):
        error_event = Event()
        complete_event = Event()
        received = {}

        def process_factory(command, **_kwargs):
            path = Path(command[-1])
            path.write_bytes(b"RIFF" + (b"audio" * 20))
            received["path"] = path
            return FakeAudioProcess()

        def on_error(message):
            received["error"] = message
            error_event.set()

        def on_complete(success):
            received["success"] = success
            complete_event.set()

        service = PTTAudioService(
            on_error=on_error,
            on_complete=on_complete,
            min_seconds=0,
            process_factory=process_factory,
            recognizer_factory=FailingRecognizer,
            audio_file_factory=FakeAudioFile,
        )
        self.assertTrue(service.start_recording())
        self.assertTrue(service.stop_and_transcribe_async())
        self.assertTrue(error_event.wait(2))
        self.assertTrue(complete_event.wait(2))
        self.assertIn("forced STT failure", received["error"])
        self.assertFalse(received["success"])
        self.assertFalse(received["path"].parent.exists())
        service.close()


if __name__ == "__main__":
    unittest.main()
