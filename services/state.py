from threading import Lock
from datetime import datetime, timezone


class StateStore:
    def __init__(self):
        self._lock = Lock()
        self._messages = []
        self._next_id = 1
        self._passenger_lang = "en"
        self._ride_state = "START"
        self._latest_passenger_message_id = 0
        self._last_confirmed_passenger_message_id = 0

    def set_passenger_lang(self, lang):
        with self._lock:
            self._passenger_lang = lang

    def get_passenger_lang(self):
        with self._lock:
            return self._passenger_lang

    def set_ride_state(self, state):
        with self._lock:
            self._ride_state = state

    def get_ride_state(self):
        with self._lock:
            return self._ride_state

    def add_message(self, **payload):
        with self._lock:
            msg = {
                "id": self._next_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
            self._next_id += 1
            self._messages.append(msg)
            if payload.get("sender") == "passenger":
                self._latest_passenger_message_id = msg["id"]
            self._messages = self._messages[-300:]
            return msg.copy()

    def messages_after(self, message_id):
        with self._lock:
            return [m.copy() for m in self._messages if m["id"] > message_id]

    def latest_passenger_message(self):
        with self._lock:
            for message in reversed(self._messages):
                if message.get("sender") == "passenger":
                    return message.copy()
            return None

    def mark_passenger_message_confirmed(self, message_id):
        with self._lock:
            self._last_confirmed_passenger_message_id = max(
                self._last_confirmed_passenger_message_id, message_id
            )

    def has_pending_passenger_request(self):
        with self._lock:
            return (
                self._latest_passenger_message_id
                > self._last_confirmed_passenger_message_id
            )
