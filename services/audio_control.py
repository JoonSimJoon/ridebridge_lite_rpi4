"""Optional ALSA microphone recording and STT for the physical PTT button.

Raspberry Pi 4's 3.5 mm connector is an output, not a microphone input.  This
service therefore stays disabled unless the operator explicitly enables an
external input device.
"""

import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock, Thread

try:
    import speech_recognition as speech_recognition

    SPEECH_RECOGNITION_AVAILABLE = True
    SPEECH_RECOGNITION_IMPORT_ERROR = None
except (ImportError, OSError) as exc:  # Optional on development machines
    speech_recognition = None
    SPEECH_RECOGNITION_AVAILABLE = False
    SPEECH_RECOGNITION_IMPORT_ERROR = exc


class PTTAudioService:
    """Record with ALSA and transcribe without blocking a GPIO callback."""

    def __init__(
        self,
        on_transcript=None,
        on_error=None,
        on_complete=None,
        device="default",
        sample_rate=16000,
        channels=1,
        max_seconds=30,
        min_seconds=0.25,
        language="ko-KR",
        arecord_command="arecord",
        process_factory=None,
        recognizer_factory=None,
        audio_file_factory=None,
        enabled=True,
    ):
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._on_complete = on_complete
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_seconds = max_seconds
        self.min_seconds = min_seconds
        self.language = language
        self.input_enabled = bool(enabled)

        self._process_factory = process_factory or subprocess.Popen
        self._recognizer_factory = recognizer_factory
        self._audio_file_factory = audio_file_factory
        self._arecord_path = (
            arecord_command
            if process_factory is not None
            else shutil.which(arecord_command)
        )

        if self._recognizer_factory is None and SPEECH_RECOGNITION_AVAILABLE:
            self._recognizer_factory = speech_recognition.Recognizer
        if self._audio_file_factory is None and SPEECH_RECOGNITION_AVAILABLE:
            self._audio_file_factory = speech_recognition.AudioFile

        self.available = bool(
            self.input_enabled
            and self._arecord_path
            and self._recognizer_factory
            and self._audio_file_factory
        )
        self.unavailable_reason = self._build_unavailable_reason()
        self.last_error = None

        self._lock = Lock()
        self._process = None
        self._recording_path = None
        self._recording_dir = None
        self._recording_started_at = None
        self._worker = None
        self._processing = False
        self._closed = False

        if self.available:
            print(
                "[AUDIO] ready: device={} rate={}Hz".format(
                    self.device, self.sample_rate
                ),
                flush=True,
            )
        else:
            print("[AUDIO] disabled: {}".format(self.unavailable_reason), flush=True)

    def _build_unavailable_reason(self):
        if not self.input_enabled:
            return "audio input disabled (Raspberry Pi 4 3.5mm jack is output-only)"
        reasons = []
        if not self._arecord_path:
            reasons.append("arecord command not found")
        if not self._recognizer_factory or not self._audio_file_factory:
            detail = SPEECH_RECOGNITION_IMPORT_ERROR or "library unavailable"
            reasons.append("SpeechRecognition unavailable: {}".format(detail))
        return "; ".join(reasons) if reasons else None

    @property
    def recording(self):
        with self._lock:
            return self._process is not None

    @property
    def processing(self):
        with self._lock:
            return self._processing

    def snapshot(self):
        with self._lock:
            return {
                "available": self.available,
                "input_enabled": self.input_enabled,
                "device": self.device,
                "recording": self._process is not None,
                "processing": self._processing,
                "stt_language": self.language,
                "reason": self.unavailable_reason,
                "last_error": self.last_error,
            }

    def start_recording(self):
        """Start arecord and return immediately."""
        with self._lock:
            if self._closed or not self.available:
                return False
            if self._process is not None or self._processing:
                self.last_error = "audio recorder is already busy"
                return False

            recording_dir = Path(tempfile.mkdtemp(prefix="ridebridge-ptt-"))
            recording_path = recording_dir / "recording.wav"
            command = [
                self._arecord_path,
                "-q",
                "-D",
                self.device,
                "-t",
                "wav",
                "-f",
                "S16_LE",
                "-r",
                str(self.sample_rate),
                "-c",
                str(self.channels),
                "-d",
                str(self.max_seconds),
                str(recording_path),
            ]

            try:
                process = self._process_factory(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except Exception as exc:
                shutil.rmtree(str(recording_dir), ignore_errors=True)
                self.last_error = "외부 마이크 녹음 실패: {}".format(exc)
                print("[AUDIO] {}".format(self.last_error), flush=True)
                return False

            self._process = process
            self._recording_path = recording_path
            self._recording_dir = recording_dir
            self._recording_started_at = time.monotonic()
            self.last_error = None

        print("[AUDIO] recording started", flush=True)
        return True

    def stop_and_transcribe_async(self):
        """Stop the active recording and transcribe it in a daemon worker."""
        with self._lock:
            if self._process is None:
                return False

            process = self._process
            recording_path = self._recording_path
            recording_dir = self._recording_dir
            started_at = self._recording_started_at
            self._process = None
            self._recording_path = None
            self._recording_dir = None
            self._recording_started_at = None
            self._processing = True

            worker = Thread(
                target=self._finish_recording,
                args=(process, recording_path, recording_dir, started_at),
                name="ridebridge-ptt-stt",
            )
            worker.daemon = True
            self._worker = worker
            worker.start()

        print("[AUDIO] recording stopped; STT queued", flush=True)
        return True

    def _finish_recording(self, process, recording_path, recording_dir, started_at):
        success = False
        try:
            stderr = self._stop_process(process)
            duration = max(0.0, time.monotonic() - started_at)
            if duration < self.min_seconds:
                raise RuntimeError("PTT를 0.25초 이상 눌러 주세요.")
            if not recording_path.exists() or recording_path.stat().st_size <= 44:
                detail = stderr.strip() or "empty WAV file"
                raise RuntimeError("외부 마이크 녹음 실패: {}".format(detail))

            recognizer = self._recognizer_factory()
            with self._audio_file_factory(str(recording_path)) as source:
                audio_data = recognizer.record(source)
            transcript = recognizer.recognize_google(
                audio_data, language=self.language
            ).strip()
            if not transcript:
                raise RuntimeError("음성을 인식하지 못했습니다.")

            print("[AUDIO] STT result: {}".format(transcript), flush=True)
            self._safe_callback(
                self._on_transcript,
                transcript,
                {"duration_seconds": round(duration, 2)},
            )
            success = True
        except Exception as exc:
            if (
                SPEECH_RECOGNITION_AVAILABLE
                and isinstance(exc, speech_recognition.UnknownValueError)
            ):
                message = "음성을 인식하지 못했습니다. 다시 시도해 주세요."
            elif (
                SPEECH_RECOGNITION_AVAILABLE
                and isinstance(exc, speech_recognition.RequestError)
            ):
                message = "STT 연결 오류: {}".format(exc)
            else:
                message = str(exc)
            with self._lock:
                self.last_error = message
            print("[AUDIO] error: {}".format(message), flush=True)
            self._safe_callback(self._on_error, message)
        finally:
            shutil.rmtree(str(recording_dir), ignore_errors=True)
            with self._lock:
                self._processing = False
                self._worker = None
            self._safe_callback(self._on_complete, success)

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            _, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
        if isinstance(stderr, bytes):
            return stderr.decode("utf-8", errors="replace")
        return stderr or ""

    @staticmethod
    def _safe_callback(callback, *args):
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            print("[AUDIO] callback error: {}".format(exc), flush=True)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            recording_dir = self._recording_dir
            self._process = None
            self._recording_path = None
            self._recording_dir = None

        if process is not None:
            try:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=1)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if recording_dir is not None:
            shutil.rmtree(str(recording_dir), ignore_errors=True)
        print("[AUDIO] closed", flush=True)
