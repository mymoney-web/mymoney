import os
import logging
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from dotenv import load_dotenv
from database import init_db, add_transaction, get_weekly_summary, get_monthly_summary
from claude_helper import extract_slip_data

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
configuration = Configuration(access_token=os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])

init_db()


def _format_summary(label: str, rows, total: float, start: str, end: str) -> str:
    if not rows:
        return f"ไม่มีรายการใน{label}\n({start} ถึง {end})"
    lines = [f"📊 สรุป{label} ({start} ถึง {end})\n"]
    for date, amount, sender in rows:
        sender_text = f" จาก {sender}" if sender else ''
        lines.append(f"💸 {date} | ฿{amount:,.2f}{sender_text}")
    lines.append(f"\n💵 รวมทั้งหมด: ฿{total:,.2f}")
    return '\n'.join(lines)


@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning('Invalid LINE signature')
        abort(400)
    return 'OK'


@app.route('/health', methods=['GET'])
def health():
    return 'OK'


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_blob_api = MessagingApiBlob(api_client)

        try:
            image_bytes = line_blob_api.get_message_content(message_id=event.message.id)
        except Exception as e:
            logger.error(f'Failed to download image: {e}')
            _reply(line_bot_api, event.reply_token, '❌ ไม่สามารถดาวน์โหลดรูปได้ กรุณาลองใหม่')
            return

        slip_data = extract_slip_data(image_bytes)

        if slip_data and slip_data.get('amount'):
            add_transaction(slip_data)
            reply_text = (
                f"✅ บันทึกแล้ว!\n"
                f"📅 วันที่: {slip_data.get('date', 'ไม่ระบุ')}\n"
                f"👤 ผู้โอน: {slip_data.get('sender') or 'ไม่ระบุ'}\n"
                f"👤 ผู้รับ: {slip_data.get('receiver') or 'ไม่ระบุ'}\n"
                f"💰 ยอด: ฿{slip_data['amount']:,.2f}\n"
                f"🏦 ธนาคาร: {slip_data.get('bank') or 'ไม่ระบุ'}"
            )
        else:
            reply_text = '❌ ไม่สามารถอ่านข้อมูลจากสลิปได้\nกรุณาส่งรูปสลิปที่ชัดเจน'

        _reply(line_bot_api, event.reply_token, reply_text)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if any(kw in text for kw in ['สรุปอาทิตย์', 'สรุปสัปดาห์', 'สัปดาห์นี้', 'อาทิตย์นี้', 'รายอาทิตย์', 'รายสัปดาห์', 'weekly']):
            rows, total, start, end = get_weekly_summary()
            reply_text = _format_summary('สัปดาห์นี้', rows, total, start, end)

        elif any(kw in text for kw in ['สรุปเดือน', 'เดือนนี้', 'รายเดือน', 'monthly']):
            rows, total, start, end = get_monthly_summary()
            reply_text = _format_summary('เดือนนี้', rows, total, start, end)

        elif any(kw in text for kw in ['ช่วยเหลือ', 'help', 'วิธีใช้', 'menu']):
            reply_text = (
                "📱 วิธีใช้งาน MyMoney Bot\n\n"
                "📸 ส่งรูปสลิปโอนเงิน → บันทึกอัตโนมัติ\n\n"
                "📊 คำสั่งสรุป:\n"
                "• สรุปอาทิตย์นี้\n"
                "• สรุปเดือนนี้\n\n"
                "พิมพ์ help เพื่อดูเมนูนี้อีกครั้ง"
            )
        else:
            reply_text = 'ส่งรูปสลิปโอนเงินมาเลย 📸\nหรือพิมพ์ help เพื่อดูคำสั่งทั้งหมด'

        _reply(line_bot_api, event.reply_token, reply_text)


def _reply(api: MessagingApi, token: str, text: str):
    api.reply_message(
        ReplyMessageRequest(reply_token=token, messages=[TextMessage(text=text)])
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
