import requests

NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "ig-hijack/1.0"}


# ============================================================
# 追加① IPアドレス → 市区町村・郵便番号・ISP・緯度経度
#        位置情報を拒否された人の住所を出すため（APIキー不要）
# ============================================================
def ip_geocode(ip):
    if not ip or ip.startswith(("10.", "127.", "192.168.", "172.")):
        return {}
    for url in (f"https://ipwho.is/{ip}", f"https://ipapi.co/{ip}/json/"):
        try:
            r = requests.get(url, timeout=6).json()
            if r.get("success") is False or r.get("error"):
                continue
            c = r.get("connection") or {}
            return {
                "city": r.get("city") or "",
                "postal": r.get("postal") or "",
                "lat": r.get("latitude"),
                "lng": r.get("longitude"),
                "isp": c.get("isp") or c.get("org") or r.get("org") or "",
            }
        except Exception as e:
            print(f"[ipgeo] error: {e}")
    return {}


# ============================================================
# 追加② OpenStreetMap(Nominatim) → 番地レベルまで狙う
# ============================================================
def _nominatim(lat, lng):
    try:
        r = requests.get(
            NOMINATIM,
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lng,
                "zoom": 18,
                "addressdetails": 1,
                "accept-language": "ja",
            },
            headers=HEADERS,
            timeout=8,
        ).json()
        a = r.get("address", {})
        parts = [
            a.get("province") or a.get("state"),
            a.get("city") or a.get("town") or a.get("village"),
            a.get("suburb") or a.get("quarter") or a.get("neighbourhood"),
            a.get("road"),
            a.get("house_number"),
        ]
        return " ".join([p for p in parts if p]) or r.get("display_name", "")
    except Exception as e:
        print(f"[nominatim] error: {e}")
        return ""


# ============================================================
# 既存① 国土地理院API: 都道府県+市区町村+町名 (フォールバック用)
# ============================================================
def _gsi(lat, lng):
    url = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
    try:
        r = requests.get(url, params={"lat": lat, "lon": lng}, timeout=8)
        data = r.json()
        results = data.get("results", {})
        lv1 = results.get("lv01Nm", "")
        muni = results.get("muniCd", "")

        pref_map = {
            "01":"北海道","02":"青森県","03":"岩手県","04":"宮城県","05":"秋田県",
            "06":"山形県","07":"福島県","08":"茨城県","09":"栃木県","10":"群馬県",
            "11":"埼玉県","12":"千葉県","13":"東京都","14":"神奈川県","15":"新潟県",
            "16":"富山県","17":"石川県","18":"福井県","19":"山梨県","20":"長野県",
            "21":"岐阜県","22":"静岡県","23":"愛知県","24":"三重県","25":"滋賀県",
            "26":"京都府","27":"大阪府","28":"兵庫県","29":"奈良県","30":"和歌山県",
            "31":"鳥取県","32":"島根県","33":"岡山県","34":"広島県","35":"山口県",
            "36":"徳島県","37":"香川県","38":"愛媛県","39":"高知県","40":"福岡県",
            "41":"佐賀県","42":"長崎県","43":"熊本県","44":"大分県","45":"宮崎県",
            "46":"鹿児島県","47":"沖縄県",
        }
        pref = pref_map.get(muni[:2], "") if muni else ""
        if pref and lv1:
            return f"{pref}{lv1}"
        return lv1 or ""
    except Exception as e:
        print(f"[gsi] error: {e}")
        return ""


# ============================================================
# 既存② HeartRails Geo API: 都道府県+市区町村+町名
# ============================================================
def _heartrails(lat, lng):
    url = "https://geoapi.heartrails.com/api/json"
    params = {
        "method": "searchByGeoLocation",
        "x": lng,   # 経度
        "y": lat,   # 緯度
    }
    try:
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        items = data.get("response", {}).get("location", [])
        if not items:
            return ""
        loc = items[0]
        parts = [
            loc.get("prefecture", ""),
            loc.get("city", ""),
            loc.get("town", ""),
        ]
        address = "".join(p for p in parts if p)
        return address
    except Exception as e:
        print(f"[heartrails] error: {e}")
        return ""


# ============================================================
# 逆ジオコーディング本体
#   1. Nominatim で「都道府県+市区町村+町名+番地」を狙う
#   2. ダメなら HeartRails / 国土地理院で町名まで
# ============================================================
def reverse_geocode(lat, lng):
    nomi = _nominatim(lat, lng)
    if nomi and len(nomi) >= 8:
        return nomi

    hr = _heartrails(lat, lng)
    gsi = _gsi(lat, lng)

    for cand in (nomi, hr, gsi):
        if cand and len(cand) >= 6:
            return cand

    return nomi or hr or gsi or f"{lat:.5f}, {lng:.5f}"
