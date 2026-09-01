import requests


class CurrencyService:
    API_BASE = "https://api.frankfurter.dev/v2"

    def convert_from_krw(self, amount_krw, quote):
        quote = quote.upper()
        if quote == "KRW":
            return {
                "converted": amount_krw,
                "currency": "KRW",
                "rate": 1.0,
                "date": None,
            }

        r = requests.get(f"{self.API_BASE}/rate/KRW/{quote}", timeout=8)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            data = data[0]
        rate = float(data["rate"])
        return {
            "converted": amount_krw * rate,
            "currency": quote,
            "rate": rate,
            "date": data.get("date"),
        }
