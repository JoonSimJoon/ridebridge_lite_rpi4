"""Raspberry Pi GPIO input and status LED control.

The module deliberately keeps GPIO optional.  Import failures and pin-factory
initialization failures disable only the physical controls; web features keep
working on development machines.
"""

from threading import Event, Lock, Thread

try:
    from gpiozero import Button, RGBLED

    GPIO_LIBRARY_AVAILABLE = True
    GPIO_IMPORT_ERROR = None
except (ImportError, OSError) as exc:  # pragma: no cover - host dependent
    Button = None
    RGBLED = None
    GPIO_LIBRARY_AVAILABLE = False
    GPIO_IMPORT_ERROR = exc

try:
    import RPi.GPIO as RPI_GPIO

    RPI_GPIO_AVAILABLE = True
    RPI_GPIO_IMPORT_ERROR = None
except (ImportError, OSError, RuntimeError) as exc:  # pragma: no cover
    RPI_GPIO = None
    RPI_GPIO_AVAILABLE = False
    RPI_GPIO_IMPORT_ERROR = exc


CONFIRM_PIN = 17
PTT_PIN = 27
REPLAY_PIN = 22
RED_PIN = 5
GREEN_PIN = 6
BLUE_PIN = 13
BUTTON_BOUNCE_SECONDS = 0.08

STATUS_COLORS = {
    "idle": (0.0, 0.25, 0.0),
    "request": (0.0, 0.0, 1.0),
    "recording": (0.0, 0.0, 1.0),
    "processing": (0.0, 0.0, 0.6),
    "confirmed": (0.0, 1.0, 0.0),
    "offline": (1.0, 0.0, 0.0),
    "translation_unavailable": (1.0, 0.0, 0.0),
    "error": (1.0, 0.0, 0.0),
}

BLINKING_STATUSES = {"recording", "error"}
STATUS_COLOR_NAMES = {
    "idle": "GREEN",
    "request": "BLUE",
    "recording": "BLUE BLINK",
    "processing": "BLUE",
    "confirmed": "GREEN",
    "offline": "RED",
    "translation_unavailable": "RED",
    "error": "RED BLINK",
}


class GPIOController:
    """Owns all GPIO devices and safely dispatches physical button events."""

    def __init__(
        self,
        on_confirm=None,
        on_ptt_pressed=None,
        on_ptt_released=None,
        on_replay=None,
    ):
        self._callbacks = {
            "confirm": on_confirm,
            "ptt_pressed": on_ptt_pressed,
            "ptt_released": on_ptt_released,
            "replay": on_replay,
        }
        self.confirm_button = None
        self.ptt_button = None
        self.replay_button = None
        self.status_led = None
        self.available = False
        self.backend = "disabled"
        self.initialization_error = GPIO_IMPORT_ERROR
        self.status = "idle"
        self._lock = Lock()
        self._blink_stop = None
        self._closed = False

    def initialize(self):
        """Try gpiozero first, then the RPi.GPIO/rpi-lgpio compatibility API."""
        errors = []
        if GPIO_LIBRARY_AVAILABLE:
            try:
                self._initialize_gpiozero()
                return True
            except Exception as exc:
                errors.append("gpiozero: {}".format(exc))
                self._close_gpiozero_devices()
        else:
            errors.append("gpiozero import: {}".format(GPIO_IMPORT_ERROR))

        if RPI_GPIO_AVAILABLE:
            try:
                self._initialize_rpi_gpio()
                return True
            except Exception as exc:
                errors.append("rpi-lgpio/RPi.GPIO: {}".format(exc))
                self._cleanup_rpi_gpio()
        else:
            errors.append("RPi.GPIO import: {}".format(RPI_GPIO_IMPORT_ERROR))

        self.initialization_error = "; ".join(errors)
        self.available = False
        self.backend = "disabled"
        print("[GPIO] disabled: {}".format(self.initialization_error), flush=True)
        return False

    def _initialize_gpiozero(self):
        created_devices = []
        try:
            confirm_button = Button(
                CONFIRM_PIN, pull_up=True, bounce_time=BUTTON_BOUNCE_SECONDS
            )
            created_devices.append(confirm_button)
            ptt_button = Button(
                PTT_PIN, pull_up=True, bounce_time=BUTTON_BOUNCE_SECONDS
            )
            created_devices.append(ptt_button)
            replay_button = Button(
                REPLAY_PIN, pull_up=True, bounce_time=BUTTON_BOUNCE_SECONDS
            )
            created_devices.append(replay_button)
            status_led = RGBLED(
                red=RED_PIN,
                green=GREEN_PIN,
                blue=BLUE_PIN,
                active_high=True,
                initial_value=(0.0, 0.0, 0.0),
            )
            created_devices.append(status_led)

            self.confirm_button = confirm_button
            self.ptt_button = ptt_button
            self.replay_button = replay_button
            self.status_led = status_led

            self.confirm_button.when_pressed = lambda: self._dispatch(
                "confirm", "confirm pressed"
            )
            self.ptt_button.when_pressed = lambda: self._dispatch(
                "ptt_pressed", "PTT pressed"
            )
            self.ptt_button.when_released = lambda: self._dispatch(
                "ptt_released", "PTT released"
            )
            self.replay_button.when_pressed = lambda: self._dispatch(
                "replay", "replay pressed"
            )

            self.available = True
            self.backend = "gpiozero"
            self.initialization_error = None
            self.set_status("idle")
            print("[GPIO] initialized (gpiozero)", flush=True)
        except Exception:
            for device in reversed(created_devices):
                try:
                    device.close()
                except Exception:
                    pass
            self.confirm_button = None
            self.ptt_button = None
            self.replay_button = None
            self.status_led = None
            raise

    def _initialize_rpi_gpio(self):
        gpio = RPI_GPIO
        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)

        for pin in (CONFIRM_PIN, PTT_PIN, REPLAY_PIN):
            gpio.setup(pin, gpio.IN, pull_up_down=gpio.PUD_UP)
        for pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
            gpio.setup(pin, gpio.OUT)
            gpio.output(pin, gpio.LOW)

        gpio.add_event_detect(
            CONFIRM_PIN,
            gpio.FALLING,
            callback=lambda _channel: self._dispatch("confirm", "confirm pressed"),
            bouncetime=int(BUTTON_BOUNCE_SECONDS * 1000),
        )
        gpio.add_event_detect(
            REPLAY_PIN,
            gpio.FALLING,
            callback=lambda _channel: self._dispatch("replay", "replay pressed"),
            bouncetime=int(BUTTON_BOUNCE_SECONDS * 1000),
        )
        gpio.add_event_detect(
            PTT_PIN,
            gpio.BOTH,
            callback=self._handle_rpi_ptt_edge,
            bouncetime=int(BUTTON_BOUNCE_SECONDS * 1000),
        )

        self.available = True
        self.backend = "rpi-lgpio"
        self.initialization_error = None
        self.set_status("idle")
        print("[GPIO] initialized (rpi-lgpio/RPi.GPIO)", flush=True)

    def _handle_rpi_ptt_edge(self, _channel):
        if RPI_GPIO.input(PTT_PIN) == RPI_GPIO.LOW:
            self._dispatch("ptt_pressed", "PTT pressed")
        else:
            self._dispatch("ptt_released", "PTT released")

    def _close_gpiozero_devices(self):
        for device in (
            self.confirm_button,
            self.ptt_button,
            self.replay_button,
            self.status_led,
        ):
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
        self.confirm_button = None
        self.ptt_button = None
        self.replay_button = None
        self.status_led = None

    @staticmethod
    def _cleanup_rpi_gpio():
        if not RPI_GPIO_AVAILABLE:
            return
        try:
            RPI_GPIO.cleanup(
                [CONFIRM_PIN, PTT_PIN, REPLAY_PIN, RED_PIN, GREEN_PIN, BLUE_PIN]
            )
        except Exception:
            pass

    def _dispatch(self, callback_name, log_message):
        print("[GPIO] {}".format(log_message), flush=True)
        callback = self._callbacks.get(callback_name)
        if callback is None:
            return None
        try:
            return callback()
        except Exception as exc:
            print("[GPIO] callback error: {}".format(exc), flush=True)
            self.set_status("error")
            return None

    def simulate_confirm(self):
        return self._dispatch("confirm", "confirm pressed (debug)")

    def simulate_ptt_pressed(self):
        return self._dispatch("ptt_pressed", "PTT pressed (debug)")

    def simulate_ptt_released(self):
        return self._dispatch("ptt_released", "PTT released (debug)")

    def simulate_replay(self):
        return self._dispatch("replay", "replay pressed (debug)")

    def set_status(self, status):
        """Set a named LED state; blinking never blocks the caller."""
        if status not in STATUS_COLORS:
            raise ValueError("Unknown GPIO status: {}".format(status))

        with self._lock:
            if self._closed:
                return
            self.status = status
            if self._blink_stop is not None:
                self._blink_stop.set()
                self._blink_stop = None

            if self.available:
                self._write_led_locked(STATUS_COLORS[status])
                if status in BLINKING_STATUSES:
                    stop_event = Event()
                    self._blink_stop = stop_event
                    thread = Thread(
                        target=self._blink_worker,
                        args=(status, stop_event),
                        name="ridebridge-led-blink",
                    )
                    thread.daemon = True
                    thread.start()

        print(
            "[LED] {} -> {}".format(status, STATUS_COLOR_NAMES[status]),
            flush=True,
        )

    def _blink_worker(self, status, stop_event):
        led_on = True
        while not stop_event.wait(0.45):
            led_on = not led_on
            with self._lock:
                if self._closed or self._blink_stop is not stop_event:
                    return
                color = STATUS_COLORS[status] if led_on else (0.0, 0.0, 0.0)
                self._write_led_locked(color)

    def _write_led_locked(self, color):
        try:
            if self.backend == "gpiozero":
                self.status_led.value = color
            elif self.backend == "rpi-lgpio":
                RPI_GPIO.output(RED_PIN, RPI_GPIO.HIGH if color[0] else RPI_GPIO.LOW)
                RPI_GPIO.output(
                    GREEN_PIN, RPI_GPIO.HIGH if color[1] else RPI_GPIO.LOW
                )
                RPI_GPIO.output(BLUE_PIN, RPI_GPIO.HIGH if color[2] else RPI_GPIO.LOW)
        except Exception as exc:
            # A runtime GPIO failure should not take down a Flask request thread.
            self.initialization_error = exc
            print("[LED] write failed: {}".format(exc), flush=True)

    def close(self):
        """Stop workers, turn the LED off, and release every GPIO device."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._blink_stop is not None:
                self._blink_stop.set()
                self._blink_stop = None

            if self.backend == "gpiozero" and self.status_led is not None:
                try:
                    self.status_led.off()
                except Exception:
                    pass

            if self.backend == "gpiozero":
                self._close_gpiozero_devices()
            elif self.backend == "rpi-lgpio":
                for pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
                    try:
                        RPI_GPIO.output(pin, RPI_GPIO.LOW)
                    except Exception:
                        pass
                for pin in (CONFIRM_PIN, PTT_PIN, REPLAY_PIN):
                    try:
                        RPI_GPIO.remove_event_detect(pin)
                    except Exception:
                        pass
                self._cleanup_rpi_gpio()

            self.available = False
            self.backend = "disabled"
        print("[GPIO] closed", flush=True)
