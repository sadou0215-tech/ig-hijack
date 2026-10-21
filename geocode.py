import requests

def _gsi(lat, lng):
    """国土地理院API: 都道府県+市区町村+町名 (フォールバック用)"""
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


def _heartrails(lat, lng):
    """HeartRails Geo API: 都道府県+市区町村+町名+番地 (無料・キー不要・高精度)"""
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


def reverse_geocode(lat, lng):
    """
    高精度版:
    1. HeartRails Geo API で「都道府県+市区町村+町名」取得
    2. 国土地理院APIで補完
    """
    # まずHeartRailsで町名まで
    hr = _heartrails(lat, lng)

    # 国土地理院で都道府県+町名を取得(HeartRailsが失敗した時用)
    gsi = _gsi(lat, lng)

    if hr and gsi:
        # HeartRailsが短すぎる(町名だけ)ならGSIで補強
        if len(hr) < len(gsi):
            return gsi
        return hr
    if hr:
        return hr
    if gsi:
        return gsi
    return f"{lat:.5f}, {lng:.5f}"
