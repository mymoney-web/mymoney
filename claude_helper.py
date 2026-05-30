from google import genai
from google.genai import types
import json
import os
from datetime import datetime


def _get_media_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if image_bytes[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    return 'image/jpeg'


def extract_slip_data(image_bytes: bytes) -> dict | None:
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

    today = datetime.now().strftime('%Y-%m-%d')
    media_type = _get_media_type(image_bytes)

    prompt = f"""อ่านข้อมูลจากสลิปโอนเงินนี้ ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น:
{{
  "date": "YYYY-MM-DD (วันที่ในสลิป ถ้าไม่มีให้ใช้ {today})",
  "amount": 0.00,
  "sender": "ชื่อผู้โอน หรือ null",
  "receiver": "ชื่อผู้รับ หรือ null",
  "bank": "ชื่อธนาคาร หรือ null"
}}
ถ้าไม่ใช่สลิปโอนเงิน ตอบว่า: null"""

    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=media_type),
            prompt,
        ],
    )

    try:
        text = response.text.strip()
        if text.lower() == 'null':
            return None
        if text.startswith('```'):
            parts = text.split('```')
            text = parts[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return None
