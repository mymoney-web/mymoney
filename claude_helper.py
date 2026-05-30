from google import genai
from google.genai import types
import json
import os
import time
from datetime import datetime, timedelta, timezone


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
            if text.lower() == 'null':
                return None
            if text.startswith('```'):
                parts = text.split('```')
                text = parts[1]
                if text.startswith('json'):
                    text = text[4:]
            result = json.loads(text.strip())
            if result and isinstance(result, dict):
                try:
                    result['amount'] = float(str(result.get('amount', 0)).replace(',', '').replace('฿', '').strip())
                except (ValueError, TypeError):
                    result['amount'] = 0.0
            return result
        except Exception as e:
            if attempt == 0 and '429' in str(e):
                time.sleep(5)
                continue
            return None
