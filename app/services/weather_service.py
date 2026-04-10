"""
天氣服務：中央氣象署 API（主）+ OpenWeatherMap（備援）
"""
from typing import Optional

import httpx
from loguru import logger

from app.config import get_settings
from app.utils.redis_client import cache_weather, get_cached_weather

settings = get_settings()

# 縣市名稱 → CWB LocationName 對照
CITY_CWB_MAP = {
    "台北市": "臺北市", "台中市": "臺中市", "台南市": "臺南市", "台東縣": "臺東縣",
    "臺北市": "臺北市", "臺中市": "臺中市", "臺南市": "臺南市", "臺東縣": "臺東縣",
    "新北市": "新北市", "桃園市": "桃園市", "基隆市": "基隆市", "新竹市": "新竹市",
    "新竹縣": "新竹縣", "苗栗縣": "苗栗縣", "彰化縣": "彰化縣", "南投縣": "南投縣",
    "雲林縣": "雲林縣", "嘉義市": "嘉義市", "嘉義縣": "嘉義縣", "高雄市": "高雄市",
    "屏東縣": "屏東縣", "宜蘭縣": "宜蘭縣", "花蓮縣": "花蓮縣", "澎湖縣": "澎湖縣",
    "金門縣": "金門縣", "連江縣": "連江縣",
}


class WeatherData:
    def __init__(
        self,
        city: str,
        temp: float,
        feels_like: float,
        description: str,
        rain_prob: int,       # 降雨機率 (%)
        condition: str,       # sunny / rainy / cloudy
    ):
        self.city = city
        self.temp = temp
        self.feels_like = feels_like
        self.description = description
        self.rain_prob = rain_prob
        self.condition = condition

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "temp": self.temp,
            "feels_like": self.feels_like,
            "description": self.description,
            "rain_prob": self.rain_prob,
            "condition": self.condition,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WeatherData":
        return cls(**d)


async def get_weather(lat: float = None, lon: float = None, city: str = None) -> Optional[WeatherData]:
    """
    取得天氣資料。
    優先使用 CWB API（需 city），備援使用 OpenWeatherMap（需 lat/lon）。
    """
    cache_key = city or f"{lat:.2f},{lon:.2f}" if lat else "unknown"
    cached = await get_cached_weather(cache_key)
    if cached:
        return WeatherData.from_dict(cached)

    data = None
    if city:
        data = await _fetch_cwb(city)
    if data is None and lat and lon:
        data = await _fetch_owm(lat, lon)
    if data is None:
        # 降級：回傳預設值（讓服務繼續運作）
        data = WeatherData(
            city=city or "台灣",
            temp=25.0,
            feels_like=27.0,
            description="天氣晴朗",
            rain_prob=10,
            condition="sunny",
        )
        logger.warning(f"天氣 API 失敗，使用預設值 (key={cache_key})")

    await cache_weather(cache_key, data.to_dict())
    return data


async def _fetch_cwb(city: str) -> Optional[WeatherData]:
    """中央氣象署開放資料 API"""
    if not settings.cwb_api_key:
        return None

    cwb_city = CITY_CWB_MAP.get(city, city)
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {
        "Authorization": settings.cwb_api_key,
        "locationName": cwb_city,
        "elementName": "Wx,PoP,MinT,MaxT,CI",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        records = data["records"]["location"]
        if not records:
            return None

        location = records[0]
        elements = {e["elementName"]: e["time"][0]["parameter"] for e in location["weatherElement"]}

        wx_desc = elements.get("Wx", {}).get("parameterName", "晴")
        pop = int(elements.get("PoP", {}).get("parameterName", "10"))
        min_t = float(elements.get("MinT", {}).get("parameterName", "22"))
        max_t = float(elements.get("MaxT", {}).get("parameterName", "28"))
        temp = (min_t + max_t) / 2

        condition = "rainy" if pop >= 50 else ("cloudy" if "陰" in wx_desc or "多雲" in wx_desc else "sunny")

        return WeatherData(
            city=cwb_city,
            temp=round(temp, 1),
            feels_like=round(temp + 2, 1),
            description=wx_desc,
            rain_prob=pop,
            condition=condition,
        )
    except Exception as e:
        logger.error(f"CWB API 失敗: {e}")
        return None


async def _fetch_owm(lat: float, lon: float) -> Optional[WeatherData]:
    """OpenWeatherMap 備援"""
    if not settings.openweathermap_api_key:
        return None

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": settings.openweathermap_api_key,
        "units": "metric",
        "lang": "zh_tw",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            d = resp.json()

        city = d.get("name", "")
        temp = d["main"]["temp"]
        feels_like = d["main"]["feels_like"]
        description = d["weather"][0]["description"]
        weather_id = d["weather"][0]["id"]

        if weather_id < 600:  # 雨
            condition = "rainy"
        elif weather_id < 700:  # 雪（台灣少見）
            condition = "cloudy"
        elif weather_id < 800:  # 霧
            condition = "cloudy"
        elif weather_id == 800:  # 晴
            condition = "sunny"
        else:
            condition = "cloudy"

        return WeatherData(
            city=city,
            temp=round(temp, 1),
            feels_like=round(feels_like, 1),
            description=description,
            rain_prob=60 if condition == "rainy" else 20,
            condition=condition,
        )
    except Exception as e:
        logger.error(f"OWM API 失敗: {e}")
        return None


def lat_lon_to_city(lat: float, lon: float) -> str:
    """
    簡易經緯度 → 縣市判斷（使用範圍框）
    精確版可使用 geopy 或呼叫 Google Maps Geocoding API。
    """
    # 台灣主要縣市粗略範圍
    city_boxes = [
        ("台北市",  25.05, 121.45, 25.21, 121.67),
        ("新北市",  24.85, 121.28, 25.28, 122.00),
        ("桃園市",  24.68, 121.00, 25.12, 121.47),
        ("台中市",  24.05, 120.47, 24.60, 121.17),
        ("台南市",  22.85, 120.05, 23.47, 120.62),
        ("高雄市",  22.45, 120.22, 23.15, 120.92),
        ("基隆市",  25.08, 121.67, 25.22, 121.83),
        ("新竹市",  24.74, 120.91, 24.86, 121.03),
        ("嘉義市",  23.43, 120.39, 23.52, 120.49),
        ("花蓮縣",  23.05, 121.20, 24.50, 121.85),
        ("台東縣",  22.20, 120.82, 23.25, 121.45),
        ("宜蘭縣",  24.35, 121.45, 24.99, 121.93),
        ("苗栗縣",  24.25, 120.62, 24.70, 121.07),
        ("彰化縣",  23.80, 120.32, 24.14, 120.72),
        ("南投縣",  23.45, 120.52, 24.29, 121.47),
        ("雲林縣",  23.55, 120.07, 23.85, 120.72),
        ("嘉義縣",  23.18, 120.07, 23.65, 120.70),
        ("屏東縣",  21.90, 120.42, 22.87, 120.90),
        ("澎湖縣",  23.20, 119.32, 23.82, 119.82),
    ]
    for city, lat_min, lon_min, lat_max, lon_max in city_boxes:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return city
    return "台灣"
