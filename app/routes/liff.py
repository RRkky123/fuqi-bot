"""
LIFF 相關端點：購買頁面、金流回呼
"""
import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Transaction
from app.services.line_service import LineService
from app.services.user_service import UserService

settings = get_settings()
router = APIRouter()


# ─────────────────────────────────────────
# 購買頁面（LIFF）
# ─────────────────────────────────────────

@router.get("/liff/purchase", response_class=HTMLResponse)
async def liff_purchase_page(line_uid: str, redirect: Optional[str] = None):
    """
    LINE 內置瀏覽器顯示的購買頁面。
    實際部署建議使用 React/Vue 框架，此處提供 HTML 骨架。
    """
    html = f"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>購買合成券 — 福氣天天領</title>
  <script charset="utf-8" src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, sans-serif; background: #FFF8E7; padding: 20px; }}
    h1 {{ color: #633806; font-size: 22px; margin-bottom: 20px; text-align: center; }}
    .package {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 16px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1); border: 2px solid transparent; }}
    .package.featured {{ border-color: #D62B2B; }}
    .package-name {{ font-size: 18px; font-weight: bold; color: #333; }}
    .package-price {{ font-size: 28px; font-weight: bold; color: #D62B2B; margin: 8px 0; }}
    .package-desc {{ font-size: 14px; color: #888; }}
    .badge {{ background: #D62B2B; color: white; font-size: 12px; padding: 2px 8px;
              border-radius: 20px; margin-left: 8px; }}
    .btn {{ display: block; width: 100%; padding: 16px; margin-top: 12px;
            background: #D62B2B; color: white; font-size: 18px; font-weight: bold;
            border: none; border-radius: 12px; cursor: pointer; text-align: center; }}
    .btn:active {{ background: #A01B1B; }}
    .footer {{ text-align: center; color: #AAA; font-size: 12px; margin-top: 20px; }}
  </style>
</head>
<body>
  <h1>🎫 購買合成券</h1>

  <div class="package featured">
    <div class="package-name">10 張套組 <span class="badge">最划算</span></div>
    <div class="package-price">NT$80</div>
    <div class="package-desc">省下 NT$20！平均每張 NT$8</div>
    <button class="btn" onclick="purchase('bundle_10', 80, 10)">購買 10 張套組</button>
  </div>

  <div class="package">
    <div class="package-name">單張購買</div>
    <div class="package-price">NT$10</div>
    <div class="package-desc">立即使用，彈性選購</div>
    <button class="btn" style="background:#8E44AD" onclick="purchase('single', 10, 1)">購買 1 張</button>
  </div>

  <div class="footer">
    購買即表示同意服務條款。<br>
    未使用的套組可在 7 日內申請退款。
  </div>

  <script>
    const lineUid = '{line_uid}';

    async function purchase(packageType, amount, credits) {{
      // 呼叫後端建立訂單
      const resp = await fetch('/liff/create-order', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ line_uid: lineUid, package_type: packageType, amount, credits }})
      }});
      const data = await resp.json();
      if (data.payment_url) {{
        window.location.href = data.payment_url;
      }} else {{
        alert('建立訂單失敗，請稍後再試。');
      }}
    }}

    // LIFF 初始化
    liff.init({{ liffId: '{settings.liff_id}' }}).catch(err => console.error(err));
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


# ─────────────────────────────────────────
# 建立藍新訂單
# ─────────────────────────────────────────

@router.post("/liff/create-order")
async def create_order(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    line_uid = body.get("line_uid")
    package_type = body.get("package_type")
    amount = body.get("amount")
    credits = body.get("credits")

    if not all([line_uid, package_type, amount, credits]):
        raise HTTPException(status_code=400, detail="缺少必要欄位")

    # 建立交易記錄
    tx = Transaction(
        line_uid=line_uid,
        amount=amount,
        credits_added=credits,
        package_type=package_type,
        status="pending",
    )
    db.add(tx)
    await db.flush()

    # 產生藍新金流付款網址
    payment_url = _build_newebpay_url(
        tx_id=str(tx.tx_id),
        amount=amount,
        product_name=f"福氣天天領合成券×{credits}",
    )

    return {"payment_url": payment_url, "tx_id": str(tx.tx_id)}


# ─────────────────────────────────────────
# 藍新金流回呼（付款完成通知）
# ─────────────────────────────────────────

@router.post("/liff/payment-notify")
async def payment_notify(
    Status: str = Form(...),
    MerchantID: str = Form(...),
    TradeInfo: str = Form(...),
    TradeSha: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    藍新金流付款成功後的 POST 回呼。
    驗章 → 更新交易狀態 → 發放點數 → 推播通知。
    """
    # 驗證 SHA256
    expected_sha = _newebpay_sha256(TradeInfo)
    if not hmac.compare_digest(expected_sha.upper(), TradeSha.upper()):
        logger.warning("藍新金流驗章失敗")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 解密 AES（略）
    # 實際需使用 AES-CBC 解密 TradeInfo
    trade_data = _decrypt_newebpay(TradeInfo)

    if Status != "SUCCESS" or trade_data.get("Status") != "SUCCESS":
        logger.warning(f"付款非成功狀態: {Status}")
        return {"result": "ignored"}

    merchant_order_no = trade_data.get("MerchantOrderNo")
    if not merchant_order_no:
        return {"result": "no_order"}

    # 更新交易
    from sqlalchemy import select, update
    result = await db.execute(
        select(Transaction).where(Transaction.tx_id == merchant_order_no)
    )
    tx = result.scalar_one_or_none()
    if not tx or tx.status != "pending":
        return {"result": "already_processed"}

    tx.status = "success"
    tx.paid_at = datetime.utcnow()
    tx.newebpay_token = trade_data.get("TradeNo", "")

    # 發放點數
    await UserService.add_credits(db, tx.line_uid, tx.credits_added, reason=f"購買 {tx.package_type}")

    # 推播通知
    user = await UserService.get_by_uid(db, tx.line_uid)
    if user:
        await LineService.push_messages(tx.line_uid, [
            LineService.build_wallet_flex.__func__  # 刷新點數卡
        ])
        await LineService.push_messages(tx.line_uid, [
            LineService._text_from_text(
                f"✅ 點數已到帳！\n"
                f"獲得 {tx.credits_added} 張合成券 🎫\n"
                f"目前共有 {user.credits} 張，快去 AI 變身吧！"
            )
        ])

    logger.info(f"付款成功: tx={tx.tx_id}, uid={tx.line_uid}, credits={tx.credits_added}")
    return {"result": "success"}


# ─────────────────────────────────────────
# 藍新金流工具函式
# ─────────────────────────────────────────

def _build_newebpay_url(tx_id: str, amount: int, product_name: str) -> str:
    """產生藍新金流付款網址（MPG 模式）"""
    from app.config import get_settings
    import base64
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    s = get_settings()
    params = {
        "MerchantID": s.newebpay_merchant_id,
        "RespondType": "JSON",
        "TimeStamp": str(int(time.time())),
        "Version": "2.0",
        "MerchantOrderNo": tx_id,
        "Amt": str(amount),
        "ItemDesc": product_name,
        "ReturnURL": f"{s.base_url}/liff/payment-return",
        "NotifyURL": f"{s.base_url}/liff/payment-notify",
        "LoginType": "0",
        "LINEPAY": "1",
        "CREDIT": "1",
    }

    trade_info_str = urllib.parse.urlencode(params)

    # AES-CBC 加密
    key = s.newebpay_hash_key.encode()
    iv = s.newebpay_hash_iv.encode()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(trade_info_str.encode(), AES.block_size))
    trade_info = encrypted.hex()

    # SHA256
    trade_sha = _newebpay_sha256(trade_info)

    payload = {
        "MerchantID": s.newebpay_merchant_id,
        "TradeInfo": trade_info,
        "TradeSha": trade_sha,
        "Version": "2.0",
    }

    qs = urllib.parse.urlencode(payload)
    return f"{s.newebpay_api_url}?{qs}"


def _newebpay_sha256(trade_info: str) -> str:
    s = settings
    raw = f"HashKey={s.newebpay_hash_key}&{trade_info}&HashIV={s.newebpay_hash_iv}"
    return hashlib.sha256(raw.encode()).hexdigest().upper()


def _decrypt_newebpay(trade_info: str) -> dict:
    """AES-CBC 解密藍新回傳資料"""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad

        key = settings.newebpay_hash_key.encode()
        iv = settings.newebpay_hash_iv.encode()
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(bytes.fromhex(trade_info)), AES.block_size)
        return dict(urllib.parse.parse_qsl(decrypted.decode()))
    except Exception as e:
        logger.error(f"藍新解密失敗: {e}")
        return {}
