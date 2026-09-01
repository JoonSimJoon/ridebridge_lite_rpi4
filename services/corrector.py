import json
import re


class LocalCorrectionEngine:
    def __init__(self, data_dir):
        self.entities = json.loads(
            (data_dir / "entities.json").read_text(encoding="utf-8")
        )
        self.corrections = json.loads(
            (data_dir / "corrections.json").read_text(encoding="utf-8")
        )

    def _replace(self, text, old, new):
        return re.subn(re.escape(old), new, text, flags=re.IGNORECASE)

    def normalize(self, text, lang):
        text = re.sub(r"\s+", " ", text.strip())
        hits = []
        for source in sorted(self.corrections.get(lang, {}), key=len, reverse=True):
            target = self.corrections[lang][source]
            text, count = self._replace(text, source, target)
            if count:
                hits.append(f"{source} → {target}")

        for entity in self.entities:
            preferred = entity.get("preferred", {}).get(lang) or entity["canonical"]
            aliases = entity.get("aliases", {}).get(lang, []) + entity.get(
                "aliases", {}
            ).get("all", [])
            for alias in sorted(set(aliases), key=len, reverse=True):
                text, count = self._replace(text, alias, preferred)
                if count:
                    hits.append(f"{alias} → {preferred}")
        return text, hits
