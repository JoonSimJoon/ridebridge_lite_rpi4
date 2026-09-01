import json


class DataService:
    def __init__(self, data_dir):
        self.phrases = json.loads(
            (data_dir / "phrases.json").read_text(encoding="utf-8")
        )
        self.guides = json.loads(
            (data_dir / "guides.json").read_text(encoding="utf-8")
        )

    def passenger_phrase(self, phrase_id):
        return next(
            (x for x in self.phrases["passenger"] if x["id"] == phrase_id), None
        )

    def driver_phrase(self, phrase_id):
        for items in self.phrases["driver_context"].values():
            found = next((x for x in items if x["id"] == phrase_id), None)
            if found:
                return found
        return None
