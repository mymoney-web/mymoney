from google import genai
from google.genai import types
import json
import os
import time
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
BKK = timezone(timedelta(hours=7))


def _get_media_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if image_bytes[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    return 'image/jpeg'


def extract_slip_data(image_bytes: bytes) -> dict | None:
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
    today = datetime.now(BKK).strftime('%Y-%m-%d')
    media_type = _get_media_type(image_bytes)

    prompt = f"""อ่านข้อมูลจากสลิปโอนเงินนี้ ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น ไม่มี markdown:
{{
  "date": "YYYY-MM-DD (วันที่ในสลิป ถ้าไม่มีให้ใช้ {today})",
  "amount": 500.00,
  "sender": "ชื่อผู้โอน หรือ null",
  "receiver": "ชื่อผู้รับ หรือ null",
  "bank": "ชื่อธนาคาร หรือ null"
}}
หมายเหตุ: amount ต้องเป็นตัวเลขเท่านั้น ไม่มีสัญลักษณ์ ไม่มีเครื่องหมาย ไม่มี comma เช่น 500.00 หรือ 1200.50
ถ้าไม่ใช่สลิปโอนเงิน ตอบว่า: null"""

    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=media_type),
                    prompt,
                ],
            )
            text = response.text.strip()
            logger.info(f"Gemini raw response: {text[:300]}")
            if text.lower() == 'null':
                return None
            if text.startswith('```'):
                parts = text.split('```')
                text = parts[1]
                if text.startswith('json'):
                    text = text[4:]
            result = json.loads(text.strip())
            logger.info(f"Parsed result: {result}")
            if result and isinstance(result, dict):
                raw_amount = result.get('amount')
                try:
                    if raw_amount is None or str(raw_amount).lower() in ('null', 'none', ''):
                        result['amount'] = None
                    else:
                        result['amount'] = float(str(raw_amount).replace(',', '').replace('฿', '').strip())
                except (ValueError, TypeError):
                    logger.warning(f"Cannot parse amount: {raw_amount!r}")
                    result['amount'] = None
            logger.info(f"Final amount: {result.get('amount') if result else 'N/A'}")
            return result
        except Exception as e:
            logger.error(f"extract_slip_data attempt {attempt} error: {e}")
            if attempt == 0 and '429' in str(e):
                time.sleep(5)
                continue
            return None
