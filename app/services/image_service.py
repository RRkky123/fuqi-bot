"""
平安圖生成服務（規則式合成，Pillow）
設計書：天氣×時段×節氣 三維模板矩陣，不需 AI，速度 <2 秒
"""
import io
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from app.services.weather_service import WeatherData

# 路徑常數
TEMPLATES_DIR = Path("templates")
FONTS_DIR = Path("fonts")

# 預設字型（若找不到，使用 PIL 預設）
FONT_PATH = FONTS_DIR / "NotoSansCJK-Regular.ttc"

# ── 天氣條件對應 ──────────────────────────────────────────────
CONDITION_EMOJI = {
    "sunny": "☀️",
    "cloudy": "⛅",
    "rainy": "🌧️",
}

# 語錄庫（節氣、天氣對應，實際部署建議移至資料庫）
QUOTES = {
    "sunny": [
        "☀️ 陽光正好，願您今天笑顏如花！",
        "☀️ 晴空萬里，好運與您同行！",
        "🌸 美好的一天從現在開始！",
        "✨ 太陽照耀，祝您健康平安！",
    ],
    "cloudy": [
        "⛅ 雲淡風輕，願您心情舒暢！",
        "🌤️ 多雲的天，也有溫暖的心！",
        "💛 平靜的天氣，平靜的心情，一切都好！",
    ],
    "rainy": [
        "🌧️ 下雨天別忘記帶傘，保重身體！",
        "☔ 雨中有情，祝您出入平安！",
        "🌂 雨過天晴，一切順心如意！",
    ],
}

# 節日語錄
FESTIVAL_QUOTES = {
    "農曆新年": ["🧧 新年快樂！萬事如意，財源滾滾！", "🎊 恭喜發財，紅包拿來！"],
    "元宵節":   ["🏮 元宵節快樂！圓圓滿滿，幸福美滿！"],
    "端午節":   ["🎋 端午安康！龍舟競渡，祝您佳節愉快！"],
    "中秋節":   ["🌕 中秋快樂！月圓人團圓，闔家平安！"],
    "聖誕節":   ["🎄 聖誕快樂！平安夜，平安心！"],
}


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    if FONT_PATH.exists():
        return ImageFont.truetype(str(FONT_PATH), size)
    # 嘗試系統字型
    system_fonts = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Windows/Fonts/msjh.ttc",
    ]
    for path in system_fonts:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _get_quote(condition: str, display_name: str) -> str:
    quotes = QUOTES.get(condition, QUOTES["sunny"])
    import random
    base = random.choice(quotes)
    # 插入使用者名稱
    return f"祝 {display_name} 今天平安健康 🙏\n{base}"


def _choose_template(condition: str, hour: int) -> Optional[Path]:
    """
    選擇對應模板（晴/雨/陰 × 早/午/晚）
    模板命名規則：{condition}_{time}.jpg
    """
    time_slot = "morning" if 5 <= hour < 12 else ("afternoon" if 12 <= hour < 18 else "evening")
    candidates = [
        TEMPLATES_DIR / f"{condition}_{time_slot}.jpg",
        TEMPLATES_DIR / f"{condition}.jpg",
        TEMPLATES_DIR / "default.jpg",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def generate_peace_image(
    weather: WeatherData,
    display_name: str,
    output_size: tuple = (1024, 1024),
) -> bytes:
    """
    生成每日平安圖，回傳 JPEG bytes。
    """
    now = datetime.now()
    hour = now.hour

    # 1. 選模板背景
    template_path = _choose_template(weather.condition, hour)
    if template_path:
        img = Image.open(template_path).convert("RGBA").resize(output_size)
    else:
        # 無模板時動態生成漸層背景
        img = _create_gradient_bg(weather.condition, output_size)

    # 2. 疊加半透明遮罩（文字可讀性）
    overlay = Image.new("RGBA", output_size, (0, 0, 0, 80))
    img = Image.alpha_composite(img, overlay)

    # 3. 繪製文字
    draw = ImageDraw.Draw(img)
    W, H = output_size

    # 日期文字
    date_str = now.strftime("%Y年%m月%d日")
    font_date = _get_font(36)
    draw.text((W // 2, 80), date_str, font=font_date, fill="white", anchor="mm")

    # 天氣資訊
    font_weather = _get_font(48)
    weather_str = f"{CONDITION_EMOJI.get(weather.condition, '')} {weather.city}  {weather.temp}°C"
    draw.text((W // 2, H // 2 - 80), weather_str, font=font_weather, fill="#FFEC8B", anchor="mm")

    # 體感溫度 + 降雨機率
    font_sub = _get_font(32)
    sub_str = f"體感 {weather.feels_like}°C ｜ 降雨機率 {weather.rain_prob}%"
    draw.text((W // 2, H // 2), sub_str, font=font_sub, fill="#E0E0E0", anchor="mm")

    # 語錄
    font_quote = _get_font(36)
    quote = _get_quote(weather.condition, display_name)
    _draw_multiline_center(draw, quote, font_quote, W, H // 2 + 100, "white")

    # 品牌標記
    font_brand = _get_font(24)
    draw.text((W // 2, H - 50), "— 福氣天天領 —", font=font_brand, fill="rgba(255,255,255,150)", anchor="mm")

    # 4. 轉 RGB 輸出 JPEG
    rgb_img = img.convert("RGB")
    buf = io.BytesIO()
    rgb_img.save(buf, format="JPEG", quality=85)
    logger.debug(f"平安圖生成完成: {display_name}, {weather.condition}, size={len(buf.getvalue())} bytes")
    return buf.getvalue()


def _create_gradient_bg(condition: str, size: tuple) -> Image.Image:
    """動態漸層背景（無模板時備用）"""
    W, H = size
    color_map = {
        "sunny":  [(255, 183, 77), (255, 112, 67)],
        "cloudy": [(144, 164, 174), (207, 216, 220)],
        "rainy":  [(66, 99, 140), (100, 143, 188)],
    }
    colors = color_map.get(condition, color_map["sunny"])
    img = Image.new("RGBA", size)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(colors[0][0] * (1 - ratio) + colors[1][0] * ratio)
        g = int(colors[0][1] * (1 - ratio) + colors[1][1] * ratio)
        b = int(colors[0][2] * (1 - ratio) + colors[1][2] * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))
    return img


def _draw_multiline_center(draw, text: str, font, width: int, y_start: int, color: str):
    """多行文字置中"""
    lines = text.split("\n")
    line_height = font.size + 8
    for i, line in enumerate(lines):
        draw.text(
            (width // 2, y_start + i * line_height),
            line,
            font=font,
            fill=color,
            anchor="mm",
        )
