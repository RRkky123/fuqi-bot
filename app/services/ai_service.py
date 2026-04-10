"""
AI 臉部合成服務
MVP 階段使用 Replicate API（Flux / SDXL face swap）
設計書建議：成本需控制在 NT$3/張（約 USD$0.09）以內
"""
import asyncio
import uuid
from typing import Optional

import httpx
from loguru import logger

from app.config import get_settings

settings = get_settings()

# 每個主題的背景描述 Prompt（對應設計書 12 款主題）
THEME_PROMPTS = {
    "tokyo_tower":   "Realistic photo of person in front of Tokyo Tower at dusk, tourism photo, professional photography",
    "paris_tower":   "Realistic photo of person in front of Eiffel Tower Paris, golden hour, professional photography",
    "kyoto_street":  "Realistic photo of person in traditional Kyoto street, cherry blossoms, cultural tourism",
    "spring_sakura": "Realistic photo of person surrounded by blooming cherry blossoms, spring, joyful",
    "flower_bloom":  "Realistic photo of person in lush flower garden, peonies and roses, blessings and prosperity",
    "fortune_god":   "Lucky and prosperous portrait, surrounded by gold coins and fortune decorations, auspicious",
    "bamboo_forest": "Peaceful photo of person in serene bamboo forest, zen garden, tranquil",
    "hawaii_beach":  "Tropical beach photo, person relaxing on Hawaii beach, turquoise water, summer holiday",
    "new_year_fireworks": "Festive photo of person with colorful fireworks at night, New Year celebration",
    "lunar_new_year": "Traditional Chinese New Year photo, red lanterns, lucky decorations, festive",
    "mooncake_festival": "Mid-Autumn festival photo, full moon, mooncakes, family reunion atmosphere",
    "cloud_sea":     "Majestic photo of person above the clouds, ethereal sky, dreamlike landscape",
}


class AISynthesisService:

    @staticmethod
    async def start_synthesis(
        job_id: str,
        avatar_url: str,
        theme_id: str,
    ) -> str:
        """
        啟動 AI 合成，回傳 Replicate prediction ID。
        若 API 未設定，使用 Mock 模式（開發用）。
        """
        if not settings.replicate_api_token:
            logger.warning("Replicate API 未設定，使用 Mock 模式")
            return f"mock_{job_id}"

        prompt = THEME_PROMPTS.get(theme_id, THEME_PROMPTS["spring_sakura"])
        prediction_id = await _start_replicate_prediction(avatar_url, prompt)
        return prediction_id

    @staticmethod
    async def poll_result(prediction_id: str, timeout: int = 90) -> Optional[str]:
        """
        輪詢合成結果，回傳圖片 URL（或 None 若失敗）。
        """
        if prediction_id.startswith("mock_"):
            # Mock 模式：模擬 3 秒處理
            await asyncio.sleep(3)
            return "https://placehold.co/1024x1024/FF6B6B/white?text=AI+合成示意圖（開發模式）"

        return await _poll_replicate(prediction_id, timeout)


async def _start_replicate_prediction(avatar_url: str, prompt: str) -> str:
    """呼叫 Replicate API 建立 prediction"""
    headers = {
        "Authorization": f"Token {settings.replicate_api_token}",
        "Content-Type": "application/json",
    }
    # 使用 Flux Schnell + FaceSwap 模型（版本依實際填入）
    payload = {
        "version": "stability-ai/sdxl:39ed52f2319f9257f0a0b2c01d79e0534db9c5e24c3e3f6c5d0a96e1",
        "input": {
            "prompt": prompt,
            "face_image": avatar_url,
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
            "width": 1024,
            "height": 1024,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["id"]


async def _poll_replicate(prediction_id: str, timeout: int) -> Optional[str]:
    """輪詢 Replicate prediction 結果"""
    headers = {"Authorization": f"Token {settings.replicate_api_token}"}
    url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
    start = asyncio.get_event_loop().time()

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                logger.error(f"Replicate timeout: {prediction_id}")
                return None

            resp = await client.get(url, headers=headers)
            data = resp.json()
            status = data.get("status")

            if status == "succeeded":
                output = data.get("output")
                if isinstance(output, list) and output:
                    return output[0]
                elif isinstance(output, str):
                    return output
                return None
            elif status in ("failed", "canceled"):
                logger.error(f"Replicate failed: {prediction_id}, error={data.get('error')}")
                return None

            await asyncio.sleep(3)
