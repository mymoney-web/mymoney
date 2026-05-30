import os
import uuid
import logging
from flask import (
    Flask, request, abort, redirect, url_for,
    render_template_string, send_from_directory,
)
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from dotenv import load_dotenv
from database import (
    init_db, add_transaction, update_category, get_transaction,
    get_all_transactions, get_weekly_summary, get_monthly_summary,
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

CATEGORIES = ['ค่ารถ', 'ค่าอาหาร', 'ค่าสินค้า', 'ค่าออฟฟิศ', 'อื่นๆ']

init_db()

# ─── HTML Templates ───────────────────────────────────────────────

_BASE = """
<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyMoney</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #333; }
  .wrap { max-width: 480px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 1.4rem; margin-bottom: 16px; }
  .card { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }
  .row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: .95rem; }
  .row:last-child { border-bottom: none; }
  .label { color: #888; }
  .amount { font-size: 1.3rem; font-weight: 700; color: #e05; }
  .btn { display: inline-block; padding: 10px 18px; border-radius: 8px; border: none; cursor: pointer; font-size: .95rem; }
  .btn-primary { background: #06c; color: #fff; }
  .btn-primary:hover { background: #048; }
  .cats { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
  .cats button { flex: 1 1 calc(33% - 10px); padding: 14px 8px; border-radius: 10px; border: 2px solid #ddd; background: #fff; font-size: 1rem; cursor: pointer; }
  .cats button:hover { border-color: #06c; background: #e8f0fe; }
  .upload-box { border: 2px dashed #ccc; border-radius: 12px; padding: 40px 20px; text-align: center; cursor: pointer; }
  .upload-box:hover { border-color: #06c; background: #f0f7ff; }
  .upload-box input[type=file] { display: none; }
  .slip-img { width: 100%; border-radius: 8px; margin-top: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  th { background: #06c; color: #fff; padding: 10px 8px; text-align: left; }
  td { padding: 10px 8px; border-bottom: 1px solid #eee; vertical-align: middle; }
  tr:hover td { background: #f9f9f9; }
  .tag { display: inline-block; padding: 3px 10px; border-radius: 20px; background: #e8f0fe; color: #06c; font-size: .82rem; }
  .tag.none { background: #fff3cd; color: #856404; }
  .thumb { width: 48px; height: 48px; object-fit: cover; border-radius: 6px; }
  nav { display: flex; gap: 12px; margin-bottom: 16px; }
  nav a { color: #06c; text-decoration: none; font-size: .95rem; }
  nav a:hover { text-decoration: underline; }
  .loading { display: none; text-align: center; padding: 20px; color: #666; }
</style>
</head>
<body>
<div class="wrap">
  <nav><a href="/">🏠 อัปโหลด</a><a href="/history">📋 ประวัติ</a></nav>
  {% block content %}{% endblock %}
</div>
</body>
</html>
"""

_UPLOAD = _BASE.replace('{% block content %}{% endblock %}', """
<h1>💰 MyMoney</h1>
<div class="card">
  <form id="uploadForm" method="post" action="/upload" enctype="multipart/form-data">
    <label class="upload-box" for="slip">
      <div style="font-size:2.5rem">📎</div>
      <div style="margin-top:8px;font-size:1rem;color:#555">แตะเพื่อเลือกรูปสลิป</div>
      <div id="fname" style="margin-top:6px;font-size:.85rem;color:#06c"></div>
      <input type="file" id="slip" name="slip" accept="image/*" required
             onchange="document.getElementById('fname').textContent=this.files[0].name">
    </label>
    <div style="margin-top:16px;text-align:center">
      <button class="btn btn-primary" type="submit" style="width:100%;padding:14px;font-size:1rem">
        อัปโหลดและอ่านสลิป
      </button>
    </div>
    <div class="loading" id="loading">⏳ กำลังอ่านสลิป...</div>
  </form>
</div>
<script>
document.getElementById('uploadForm').onsubmit = function() {
  document.getElementById('loading').style.display = 'block';
};
</script>
""")

_CATEGORIZE = _BASE.replace('{% block content %}{% endblock %}', """
<h1>📂 เลือกหมวดหมู่</h1>
<div class="card">
  {% if amount %}
  <div class="row"><span class="label">💰 ยอดเงิน</span><span class="amount">฿{{ amount }}</span></div>
  {% endif %}
  {% if date %}<div class="row"><span class="label">📅 วันที่</span><span>{{ date }}</span></div>{% endif %}
  {% if sender %}<div class="row"><span class="label">👤 ผู้โอน</span><span>{{ sender }}</span></div>{% endif %}
  {% if bank %}<div class="row"><span class="label">🏦 ธนาคาร</span><span>{{ bank }}</span></div>{% endif %}
</div>
<div class="card">
  <div style="font-weight:600;margin-bottom:4px">เลือกหมวดหมู่ค่าใช้จ่าย</div>
  <form method="post">
    <div class="cats">
      {% for cat in categories %}
      <button type="submit" name="category" value="{{ cat }}">{{ cat }}</button>
      {% endfor %}
    </div>
  </form>
</div>
{% if image_path %}
<div class="card">
  <div style="font-size:.9rem;color:#888;margin-bottom:8px">รูปสลิปที่อัปโหลด</div>
  <img class="slip-img" src="/uploads/{{ image_path }}" alt="slip">
</div>
{% endif %}
""")

_HISTORY = _BASE.replace('{% block content %}{% endblock %}', """
<h1>📋 ประวัติรายจ่าย</h1>
{% if rows %}
<div class="card" style="padding:0;overflow:hidden">
<table>
  <tr>
    <th>วันที่</th>
    <th>ยอด (฿)</th>
    <th>ผู้โอน</th>
    <th>หมวดหมู่</th>
    <th>สลิป</th>
  </tr>
  {% for r in rows %}
  <tr>
    <td>{{ r[1] or '-' }}</td>
    <td style="font-weight:600">{{ '{:,.2f}'.format(r[2]) if r[2] else '-' }}</td>
    <td>{{ r[3] or '-' }}</td>
    <td>
      {% if r[5] %}
        <span class="tag">{{ r[5] }}</span>
      {% else %}
        <span class="tag none">ยังไม่ระบุ</span>
      {% endif %}
    </td>
    <td>
      {% if r[6] %}
        <a href="/uploads/{{ r[6] }}" target="_blank">
          <img class="thumb" src="/uploads/{{ r[6] }}" alt="slip">
        </a>
      {% else %}-{% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
</div>
{% else %}
<div class="card" style="text-align:center;color:#888">ยังไม่มีรายการ</div>
{% endif %}
""")

# ─── Web Routes ───────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(_UPLOAD)


@app.route('/upload', methods=['POST'])
def upload():
    file = request.files.get('slip')
    if not file or file.filename == '':
        return redirect(url_for('index'))

    ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    with open(filepath, 'rb') as f:
        image_bytes = f.read()

    slip_data = extract_slip_data(image_bytes) or {}
    tx_id = add_transaction(slip_data, image_path=filename)

    return redirect(url_for('categorize', tx_id=tx_id))


@app.route('/categorize/<int:tx_id>', methods=['GET', 'POST'])
def categorize(tx_id):
    if request.method == 'POST':
        category = request.form.get('category', 'อื่นๆ')
        update_category(tx_id, category)
        return redirect(url_for('history'))

    row = get_transaction(tx_id)
    if not row:
        return redirect(url_for('index'))

    _, date, amount, sender, _, bank, _, image_path = row
    return render_template_string(
        _CATEGORIZE,
        date=date, bank=bank, sender=sender,
        amount=f"{amount:,.2f}" if amount else None,
        image_path=image_path,
        categories=CATEGORIES,
    )


@app.route('/history')
def history():
    rows = get_all_transactions()
    return render_template_string(_HISTORY, rows=rows)


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ─── Health ───────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return 'OK'


# ─── LINE Bot ─────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
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

        slip_data = extract_slip_data(image_bytes) or {}
        filename = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as f:
            f.write(image_bytes)
        tx_id = add_transaction(slip_data, image_path=filename)

        if slip_data.get('amount'):
            reply_text = (
                f"✅ บันทึกแล้ว!\n"
                f"📅 วันที่: {slip_data.get('date', 'ไม่ระบุ')}\n"
                f"👤 ผู้โอน: {slip_data.get('sender') or 'ไม่ระบุ'}\n"
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
                "📊 คำสั่งสรุป:\n• สรุปอาทิตย์นี้\n• สรุปเดือนนี้"
            )
        else:
            reply_text = 'ส่งรูปสลิปโอนเงินมาเลย 📸\nหรือพิมพ์ help เพื่อดูคำสั่งทั้งหมด'
        _reply(line_bot_api, event.reply_token, reply_text)


def _format_summary(label, rows, total, start, end):
    if not rows:
        return f"ไม่มีรายการใน{label}\n({start} ถึง {end})"
    lines = [f"📊 สรุป{label} ({start} ถึง {end})\n"]
    for date, amount, sender in rows:
        sender_text = f" จาก {sender}" if sender else ''
        lines.append(f"💸 {date} | ฿{amount:,.2f}{sender_text}")
    lines.append(f"\n💵 รวมทั้งหมด: ฿{total:,.2f}")
    return '\n'.join(lines)


def _reply(api, token, text):
    api.reply_message(ReplyMessageRequest(reply_token=token, messages=[TextMessage(text=text)]))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
