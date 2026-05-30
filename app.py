import os
import uuid
import logging
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage, FlexMessage,
    FlexBubble, FlexBox, FlexText, FlexButton, FlexSeparator,
    PostbackAction,
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent, PostbackEvent
from linebot.v3.exceptions import InvalidSignatureError
from dotenv import load_dotenv
from database import (
    init_db, add_transaction, update_category, get_transaction,
    get_all_transactions, get_daily_summary, get_weekly_summary, get_monthly_summary,
)
from claude_helper import extract_slip_data

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
configuration = Configuration(access_token=os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])

DB_PATH = os.environ.get('DB_PATH', 'mymoney.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CATEGORIES = ['ค่ารถ', 'ค่าอาหาร', 'ค่าสินค้า', 'ค่าออฟฟิศ', 'ค่าเหล้า', 'เซเว่น', 'เสื้อผ้า', 'อื่นๆ']

init_db()


# ─── Flex Message builders ────────────────────────────────────────

def _make_row(label: str, value: str) -> FlexBox:
    return FlexBox(
        layout='horizontal',
        contents=[
            FlexText(text=label, color='#888888', size='sm', flex=2),
            FlexText(text=value, size='sm', flex=3, wrap=True),
        ],
    )


def make_slip_flex(tx_id: int, slip_data: dict) -> FlexMessage:
    info_rows = []
    if slip_data.get('date'):
        info_rows.append(_make_row('📅 วันที่', slip_data['date']))
    if slip_data.get('amount'):
        info_rows.append(_make_row('💰 ยอด', f"฿{slip_data['amount']:,.2f}"))
    if slip_data.get('sender'):
        info_rows.append(_make_row('👤 ผู้โอน', slip_data['sender']))
    if slip_data.get('bank'):
        info_rows.append(_make_row('🏦 ธนาคาร', slip_data['bank']))

    # category buttons — 2 per row
    btn_rows = []
    for i in range(0, len(CATEGORIES), 2):
        chunk = CATEGORIES[i:i + 2]
        btn_rows.append(
            FlexBox(
                layout='horizontal',
                spacing='sm',
                contents=[
                    FlexButton(
                        action=PostbackAction(
                            label=cat,
                            data=f"cat:{tx_id}:{cat}",
                            display_text=cat,
                        ),
                        style='primary',
                        height='sm',
                        flex=1,
                    )
                    for cat in chunk
                ],
            )
        )

    bubble = FlexBubble(
        body=FlexBox(
            layout='vertical',
            spacing='sm',
            contents=[
                FlexText(text='✅ อ่านสลิปได้แล้ว', weight='bold', size='md'),
                FlexSeparator(margin='sm'),
                *info_rows,
                FlexSeparator(margin='md'),
                FlexText(text='เลือกหมวดหมู่:', weight='bold', size='sm', margin='md'),
                *btn_rows,
            ],
        ),
    )
    return FlexMessage(alt_text='เลือกหมวดหมู่ค่าใช้จ่าย', contents=bubble)


def make_menu_flex() -> FlexMessage:
    bubble = FlexBubble(
        body=FlexBox(
            layout='vertical',
            spacing='md',
            contents=[
                FlexText(text='📊 สรุปค่าใช้จ่าย', weight='bold', size='lg'),
                FlexText(text='เลือกช่วงเวลาที่ต้องการ', size='sm', color='#888888'),
                FlexSeparator(margin='md'),
                FlexButton(
                    action=PostbackAction(label='📅 รายวัน', data='summary:daily', display_text='สรุปรายวัน'),
                    style='primary', height='sm', margin='md',
                ),
                FlexButton(
                    action=PostbackAction(label='📆 รายอาทิตย์', data='summary:weekly', display_text='สรุปรายอาทิตย์'),
                    style='primary', height='sm',
                ),
                FlexButton(
                    action=PostbackAction(label='🗓️ รายเดือน', data='summary:monthly', display_text='สรุปรายเดือน'),
                    style='primary', height='sm',
                ),
            ],
        )
    )
    return FlexMessage(alt_text='เลือกช่วงเวลาสรุปค่าใช้จ่าย', contents=bubble)


def make_summary_flex(label: str, rows, total: float, start: str, end: str) -> FlexMessage:
    if not rows:
        bubble = FlexBubble(
            body=FlexBox(
                layout='vertical',
                contents=[
                    FlexText(text=f'📊 สรุป{label}', weight='bold', size='md'),
                    FlexText(text=f'{start} ถึง {end}', size='xs', color='#888888'),
                    FlexSeparator(margin='md'),
                    FlexText(text='ไม่มีรายการ', color='#aaaaaa', margin='md'),
                ],
            )
        )
        return FlexMessage(alt_text=f'สรุป{label}', contents=bubble)

    item_rows = []
    for date, amount, sender, category in rows:
        sender_text = sender or 'ไม่ระบุ'
        cat_text = f'[{category}]' if category else '[ยังไม่ระบุ]'
        item_rows.append(
            FlexBox(
                layout='horizontal',
                contents=[
                    FlexText(text=date or '-', size='xs', color='#888888', flex=2),
                    FlexText(text=cat_text, size='xs', color='#0066cc', flex=3, wrap=True),
                    FlexText(text=f'฿{amount:,.0f}', size='xs', align='end', flex=2, weight='bold'),
                ],
            )
        )

    bubble = FlexBubble(
        body=FlexBox(
            layout='vertical',
            spacing='sm',
            contents=[
                FlexText(text=f'📊 สรุป{label}', weight='bold', size='md'),
                FlexText(text=f'{start} ถึง {end}', size='xs', color='#888888'),
                FlexSeparator(margin='md'),
                *item_rows,
                FlexSeparator(margin='md'),
                FlexBox(
                    layout='horizontal',
                    contents=[
                        FlexText(text='💵 รวมทั้งหมด', weight='bold', size='sm', flex=4),
                        FlexText(
                            text=f'฿{total:,.2f}',
                            weight='bold', size='sm', align='end', color='#dd0000', flex=3,
                        ),
                    ],
                ),
            ],
        )
    )
    return FlexMessage(alt_text=f'สรุป{label}: ฿{total:,.2f}', contents=bubble)


# ─── Routes ───────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return 'OK'


@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# ─── LINE Handlers ─────────────────────────────────────────────────

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

        try:
            filename = f"{uuid.uuid4().hex}.jpg"
            with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as f:
                f.write(image_bytes)

            slip_data = extract_slip_data(image_bytes) or {}
            tx_id = add_transaction(slip_data, image_path=filename)
            msg = make_slip_flex(tx_id, slip_data)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )
        except Exception as e:
            logger.error(f'handle_image error: {e}')
            _reply(line_bot_api, event.reply_token, '❌ เกิดข้อผิดพลาด กรุณาลองส่งสลิปใหม่อีกครั้ง')


@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if data.startswith('cat:'):
            _, tx_id_str, category = data.split(':', 2)
            tx_id = int(tx_id_str)
            update_category(tx_id, category)
            tx = get_transaction(tx_id)
            amount = tx[2] if tx else 0
            _reply(line_bot_api, event.reply_token,
                   f"✅ บันทึกเรียบร้อยแล้ว\n💰 ยอด: ฿{amount:,.2f}\n🏷️ หมวดหมู่: {category}")

        elif data == 'summary:daily':
            rows, total, start, end = get_daily_summary()
            msg = make_summary_flex('วันนี้', rows, total, start, end)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )

        elif data == 'summary:weekly':
            rows, total, start, end = get_weekly_summary()
            msg = make_summary_flex('สัปดาห์นี้', rows, total, start, end)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )

        elif data == 'summary:monthly':
            rows, total, start, end = get_monthly_summary()
            msg = make_summary_flex('เดือนนี้', rows, total, start, end)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    text = event.message.text.strip()
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if text == '.':
            msg = make_menu_flex()
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )

        elif any(kw in text for kw in ['สรุปอาทิตย์', 'สรุปสัปดาห์', 'สัปดาห์นี้', 'อาทิตย์นี้', 'รายอาทิตย์', 'รายสัปดาห์', 'weekly']):
            rows, total, start, end = get_weekly_summary()
            msg = make_summary_flex('สัปดาห์นี้', rows, total, start, end)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )

        elif any(kw in text for kw in ['สรุปเดือน', 'เดือนนี้', 'รายเดือน', 'monthly']):
            rows, total, start, end = get_monthly_summary()
            msg = make_summary_flex('เดือนนี้', rows, total, start, end)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )

        elif any(kw in text for kw in ['ช่วยเหลือ', 'help', 'วิธีใช้', 'menu']):
            _reply(line_bot_api, event.reply_token,
                   "📱 วิธีใช้งาน MyMoney Bot\n\n"
                   "📸 ส่งรูปสลิปโอนเงิน\n"
                   "→ บันทึกอัตโนมัติ + เลือกหมวดหมู่\n\n"
                   "📊 คำสั่งสรุป:\n"
                   "• สรุปอาทิตย์นี้\n"
                   "• สรุปเดือนนี้")
        else:
            _reply(line_bot_api, event.reply_token,
                   'ส่งรูปสลิปโอนเงินมาเลย 📸\nหรือพิมพ์ help เพื่อดูคำสั่ง')


def _reply(api: MessagingApi, token: str, text: str):
    api.reply_message(
        ReplyMessageRequest(reply_token=token, messages=[TextMessage(text=text)])
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
