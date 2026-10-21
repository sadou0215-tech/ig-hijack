import requests
import time

_last_call = 0.0

def reverse_geocode(lat, lng):
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_call = time.time()

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lng,
        "format": "json",
        "accept-language": "ja",
        "zoom": 18,
    }
    headers = {"User-Agent": "ig-hijack/1.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        addr = data.get("address", {})
        parts = [
            addr.get("state"),
            addr.get("city") or addr.get("town") or addr.get("village"),
            addr.get("suburb") or addr.get("neighbourhood"),
            addr.get("road"),
            addr.get("house_number"),
        ]
        parts = [p for p in parts if p]
        if parts:
            return "".join(parts)
        return data.get("display_name", f"{lat:.5f}, {lng:.5f}")
    except Exception as e:
        print(f"[geocode] error: {e}")
        return f"{lat:.5f}, {lng:.5f}"
