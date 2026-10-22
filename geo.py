import aiohttp

async def ip_to_address(ip: str) -> dict:
    """IPからおおよその現在地を取得（ip-api.com・日本語）"""
    url = f"http://ip-api.com/json/{ip}"
    params = {"lang": "ja",
              "fields": "status,country,regionName,city,district,zip,lat,lon,isp,query"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, timeout=15) as r:
            d = await r.json(content_type=None)
    if d.get("status") != "success":
        return {"address": "不明"}
    addr = "".join(x for x in [d.get("country"), d.get("regionName"),
                               d.get("city"), d.get("district")] if x)
    return {"address": addr or "不明", "zip": d.get("zip", ""),
            "lat": d.get("lat"), "lon": d.get("lon"), "isp": d.get("isp", "")}
