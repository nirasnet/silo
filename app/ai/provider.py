"""AI provider — Claude primary, Gemini fallback, local OCR for images."""

import base64
import json
import logging

import requests as http_requests

from app.config import settings

log = logging.getLogger("silo.ai")

# ── Endpoints ──
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.0-flash-lite"]
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ── Prompts ──

IMAGE_ANALYSIS_PROMPT = """คุณเป็น AI OCR และวิเคราะห์รูปภาพจากแชทกลุ่มงาน

กฎสำคัญ:
- อ่านข้อความทุกตัวอักษรในรูป (ทั้งภาษาไทยและอังกฤษ) ถอดออกมาเป็น text ให้ครบ
- ถ้าเป็นลายมือเขียน ให้พยายามอ่านให้ได้มากที่สุด
- ถ้าเป็นฟอร์ม/ตาราง ให้ระบุหัวข้อและค่าแต่ละช่อง
- ถ้าเป็นรูปเอกสาร Stop time / Breakdown / Problem report ให้ดึงข้อมูล:
  - ปัญหา (Problem), สาเหตุ (Root Cause), เวลาหยุด (Duration)
  - เครื่องจักร/ไลน์ (Machine/Line), ผลกระทบ (Impact)
  - วิธีแก้ (Action), ผู้รับผิดชอบ (Person), วันที่/เวลา (Date/Time)
- ถ้าเป็นรูปถ่าย ให้อธิบายสภาพที่เห็น
- ถ้าเป็นกราฟ/ตัวเลข ให้อ่านค่าสำคัญ
- ตอบเป็นภาษาไทย ครบถ้วน ห้ามย่อ"""

DIGEST_SYSTEM_PROMPT = """คุณเป็น AI สรุปแชทกลุ่ม LINE ให้เป็นโครงสร้าง JSON
จากข้อความแชทที่ให้มา ให้สรุปเป็น JSON ตาม format นี้เท่านั้น (ไม่ต้องมี markdown):
{
  "summary": "สรุปภาพรวม 3-5 บรรทัด",
  "topics": ["topic1","topic2"],
  "key_events": [{"event":"..","detail":"..","who":".."}],
  "decisions": [{"decision":"..","by":".."}],
  "action_items": [{"task":"..","owner":"..","status":"planned|ongoing|done|pending"}],
  "problems": [{"problem":"..","impact":"..","fix":".."}],
  "numbers": [{"label":"..","value":".."}],
  "people": [{"name":"..","role":"บทบาทวันนี้"}],
  "sentiment": "normal|urgent|good"
}
ตอบเป็น JSON เท่านั้น ไม่ต้องมี ```json หรือคำอธิบาย"""

DIGEST_WITH_IMAGES_PROMPT = """คุณเป็น AI สรุปแชทกลุ่ม LINE ที่มีทั้งข้อความและข้อมูลจากรูปภาพ (OCR)
ข้อความที่ขึ้นต้นด้วย [รูปภาพ-OCR] คือข้อมูลที่ระบบอ่านได้จากรูปถ่าย เช่น ฟอร์ม, บันทึกปัญหา, Stop time report

จากข้อความแชทที่ให้มา ให้สรุปเป็น JSON ตาม format นี้เท่านั้น (ไม่ต้องมี markdown):
{
  "summary": "สรุปภาพรวม 3-5 บรรทัด (รวมข้อมูลจากรูปภาพด้วย)",
  "topics": ["topic1","topic2"],
  "key_events": [{"event":"..","detail":"..","who":".."}],
  "decisions": [{"decision":"..","by":".."}],
  "action_items": [{"task":"..","owner":"..","status":"planned|ongoing|done|pending"}],
  "problems": [{"problem":"..","machine":"เครื่องจักร/ไลน์","root_cause":"สาเหตุ","impact":"ผลกระทบ","duration":"เวลาหยุด","fix":"วิธีแก้","status":"resolved|pending|investigating"}],
  "stop_time": [{"machine":"ชื่อเครื่อง/ไลน์","problem":"ปัญหา","start":"เวลาเริ่ม","end":"เวลาจบ","duration":"ระยะเวลา","cause":"สาเหตุ","action":"การแก้ไข","responsible":"ผู้รับผิดชอบ"}],
  "numbers": [{"label":"..","value":".."}],
  "people": [{"name":"..","role":"บทบาทวันนี้"}],
  "image_data": [{"from":"ชื่อผู้ส่ง","content":"สรุปข้อมูลจากรูป","type":"form|photo|report|other"}],
  "sentiment": "normal|urgent|good"
}

กฎสำคัญ:
- ข้อมูลจากรูปภาพ [รูปภาพ-OCR] ต้องถูกวิเคราะห์และรวมในสรุป
- ถ้าเป็นฟอร์ม Stop time / Breakdown ให้ดึงข้อมูลเข้า stop_time array
- ถ้าเป็นรูปปัญหา ให้ดึงข้อมูลเข้า problems array
- ตอบเป็น JSON เท่านั้น ไม่ต้องมี ```json หรือคำอธิบาย"""

QA_SYSTEM_PROMPT = """คุณเป็น AI ผู้ช่วยวิเคราะห์ข้อมูลแชทกลุ่ม LINE
คุณมีข้อมูลสรุปแชทรายวัน (daily digests) ให้ใช้ตอบคำถาม

กฎการตอบ:
- ตอบเป็นภาษาไทย (คงศัพท์เทคนิคภาษาอังกฤษ)
- ใช้ emoji นำหน้าหัวข้อ เช่น 📋 🔑 ⚠️ ✅ 📌 📊
- ใช้ • เป็น bullet point
- ระบุชื่อคนเสมอ
- ระบุตัวเลข/วันเวลาชัดเจน
- ห้ามใช้ markdown (ห้าม ** ``` # → ใช้ plain text)
- ถ้าข้อมูลไม่พอให้บอกตรงๆ
- ถ้าถามเรื่องที่ไม่เกี่ยวกับแชท ให้บอกว่าตอบได้เฉพาะเรื่องในแชท"""

LINE_SUMMARY_NORMAL_PROMPT = """คุณเป็น AI สรุปแชทกลุ่ม LINE สำหรับส่งเป็นข้อความใน LINE
สรุปกระชับ ชัดเจน อ่านง่าย ใช้ emoji

format:
📊 สรุปกลุ่ม: [ชื่อกลุ่ม]
━━━━━━━━━━━━━━━━━━━━
📅 [ช่วงเวลา]
💬 [จำนวน] ข้อความ จาก [จำนวน] คน

📋 สรุปภาพรวม
[สรุป 3-5 บรรทัด]

🔑 เรื่องสำคัญ
• [เรื่อง + ใคร + ผลลัพธ์]

⚠️ ปัญหา (ถ้ามี)
• [ปัญหา → แก้ไข]

📌 Action Items
• [งาน] → [ผู้รับผิดชอบ]

━━━━━━━━━━━━━━━━━━━━
🤖 สรุปโดย Silo AI

ตอบเป็น plain text สำหรับ LINE เท่านั้น ห้ามใช้ markdown ห้าม ** ``` #"""

LINE_SUMMARY_DETAILED_PROMPT = """คุณเป็น AI สรุปแชทกลุ่ม LINE สำหรับส่งเป็นข้อความใน LINE
สรุปอย่างละเอียด ครบถ้วน อ่านง่าย ใช้ emoji เป็น bullet points

กฎสำคัญ:
- สรุปทุกเรื่องที่คุยกัน ห้ามตกหล่น
- ระบุชื่อคนพูดทุกครั้ง
- ระบุตัวเลข/สถิติ/ผลลัพธ์ที่แน่นอน
- แยกหัวข้อชัดเจน ง่ายต่อการอ่านบนมือถือ
- ใช้ภาษาไทย เข้าใจง่าย

format:
📊 สรุปกลุ่ม: [ชื่อกลุ่ม]
━━━━━━━━━━━━━━━━━━━━
📅 [เวลาเริ่ม] - [เวลาสิ้นสุด]
💬 [จำนวน] ข้อความ จาก [จำนวน] คน

👥 คนที่แอคทีฟ
• [ชื่อ] ([จำนวน] ข้อความ) - [บทบาท/สิ่งที่ทำ]

📋 สรุปภาพรวม
[สรุปละเอียด 5-8 บรรทัด]

🔑 เรื่องสำคัญ (เรียงตามเวลา)
1. [หัวข้อ]
   • [รายละเอียด]

⚠️ ปัญหา/อุปสรรค
• [ปัญหา]: [สาเหตุ] → [ผลกระทบ] → [วิธีแก้/สถานะ]

✅ สิ่งที่ตัดสินใจ/สรุป
• [ใคร] ตัดสินใจ [อะไร]

📌 Action Items
• [งาน] → [ผู้รับผิดชอบ] [สถานะ]

📊 ตัวเลข/KPI สำคัญ
• [ชื่อ]: [ค่า]

━━━━━━━━━━━━━━━━━━━━
🤖 สรุปโดย Silo AI

ตอบเป็น plain text สำหรับ LINE เท่านั้น ห้ามใช้ markdown"""

LINE_SUMMARY_PRODUCTION_PROMPT = """คุณเป็น AI สรุปแชทกลุ่มงาน Production/โรงงาน สำหรับส่งใน LINE
สรุปเน้นข้อมูลการผลิต อ่านง่าย ใช้ emoji plain text ห้ามใช้ markdown

format:
🏭 สรุป Production: [ชื่อกลุ่ม]
━━━━━━━━━━━━━━━━━━━━
📅 [ช่วงเวลา]

📊 ผลผลิต
• [Line/GW]: [แผน] vs [จริง] = [%] [OK/NG]

⚠️ ปัญหาการผลิต
• [เครื่องจักร/คุณภาพ]: [รายละเอียด] → [ผลกระทบ] → [แก้ไข/สถานะ]

✅ คุณภาพ
• [ปัญหาคุณภาพ]: [รายละเอียด]

📌 Action Items
• [งาน] → [ผู้รับผิดชอบ] [สถานะ]

━━━━━━━━━━━━━━━━━━━━
🤖 สรุปโดย Silo AI"""

LINE_SUMMARY_MEETING_PROMPT = """คุณเป็น AI สรุป Meeting Minutes จากแชทกลุ่ม LINE สำหรับส่งใน LINE
สรุปแบบ minutes of meeting อ่านง่าย ใช้ emoji plain text ห้ามใช้ markdown

format:
📝 Meeting Minutes: [ชื่อกลุ่ม]
━━━━━━━━━━━━━━━━━━━━
📅 [วันที่] | 👥 ผู้เข้าร่วม: [รายชื่อ]

📋 หัวข้อที่คุย
1. [หัวข้อ]
   • [ใคร] พูด/เสนอ: [อะไร]
   • สรุป: [ผลลัพธ์]

✅ มติ/สิ่งที่ตกลง
• [มติ] (เสนอโดย [ใคร])

📌 Action Items
• [งาน] → [ผู้รับผิดชอบ] → กำหนด: [เมื่อไหร่]

━━━━━━━━━━━━━━━━━━━━
🤖 สรุปโดย Silo AI"""

LINE_SUMMARY_SALES_PROMPT = """คุณเป็น AI สรุปแชทกลุ่ม Sales/Business สำหรับส่งใน LINE
สรุปเน้นยอดขาย ลูกค้า โอกาส อ่านง่าย ใช้ emoji plain text ห้ามใช้ markdown

format:
💼 สรุป Sales: [ชื่อกลุ่ม]
━━━━━━━━━━━━━━━━━━━━
📅 [ช่วงเวลา]

📊 ตัวเลข/ยอดสำคัญ
• [ยอดขาย/เป้า/จำนวนลูกค้า]: [ค่า]

🤝 ลูกค้า/โอกาสใหม่
• [ชื่อลูกค้า]: [รายละเอียด] → [สถานะ]

⚠️ ปัญหา/อุปสรรค
• [ปัญหา] → [ผลกระทบ]

📌 Follow-up
• [งาน] → [ใคร] [กำหนด]

━━━━━━━━━━━━━━━━━━━━
🤖 สรุปโดย Silo AI"""

_SUMMARY_TEMPLATES: dict[str, str] = {
    "normal": LINE_SUMMARY_NORMAL_PROMPT,
    "detailed": LINE_SUMMARY_DETAILED_PROMPT,
    "simple": "คุณเป็น AI สรุปแชทกลุ่ม LINE สรุปสั้นๆ อ่านง่าย เข้าใจเร็ว ใช้ emoji ใช้ภาษาไทย ตอบเป็น plain text สำหรับ LINE ห้ามใช้ markdown",
    "production": LINE_SUMMARY_PRODUCTION_PROMPT,
    "meeting": LINE_SUMMARY_MEETING_PROMPT,
    "sales": LINE_SUMMARY_SALES_PROMPT,
}


# ══════════════════════════════════════════════
#  Core AI call — Claude primary, Gemini fallback
# ══════════════════════════════════════════════

def _call_ai(prompt: str, system: str = "") -> str:
    """Try Claude first, fall back to Gemini. Returns text or '[ERROR]...'."""
    result = _call_claude(prompt, system)
    if result and not result.startswith("[ERROR]"):
        return result
    log.warning("Claude failed, trying Gemini fallback...")
    return _call_gemini(prompt, system)


def _call_claude(prompt: str, system: str = "") -> str:
    """Call Claude via Anthropic REST API."""
    key = settings.anthropic_api_key
    if not key:
        return "[ERROR] ANTHROPIC_API_KEY not set"
    model = settings.ai_model or "claude-haiku-4-5-20251001"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    try:
        resp = http_requests.post(CLAUDE_API_URL, headers=headers, json=body, timeout=90)
        if resp.status_code == 200:
            text = resp.json()["content"][0]["text"]
            log.info("Claude (%s): %d chars", model, len(text))
            return text
        err = resp.text[:200]
        log.error("Claude %d: %s", resp.status_code, err)
        return f"[ERROR] Claude API {resp.status_code}: {err}"
    except Exception as e:
        log.error("Claude call failed: %s", e)
        return f"[ERROR] Claude call failed: {e}"


def _call_gemini(prompt: str, system: str = "") -> str:
    """Call Gemini with model fallback on 429."""
    key = settings.gemini_api_key
    if not key:
        return "[ERROR] GEMINI_API_KEY not set"
    contents: list[dict] = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "เข้าใจแล้ว พร้อมทำงาน"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    last_err = ""
    for model in GEMINI_MODELS:
        try:
            url = f"{GEMINI_BASE}/{model}:generateContent?key={key}"
            resp = http_requests.post(url, json={"contents": contents}, timeout=60)
            if resp.status_code == 200:
                log.info("Gemini (%s)", model)
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            if resp.status_code == 429:
                log.warning("%s quota exceeded, trying next...", model)
                last_err = f"[ERROR] Gemini API {resp.status_code}: {resp.text[:200]}"
                continue
            return f"[ERROR] Gemini API {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = f"[ERROR] Gemini call failed ({model}): {e}"
            continue
    return last_err


# ══════════════════════════════════════════════
#  Image Analysis — Local OCR -> Claude Vision -> Gemini Vision
# ══════════════════════════════════════════════

def analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Analyze an image: local OCR -> Claude Vision -> Gemini Vision -> fallback."""
    # 1. Local OCR (fast, free)
    ocr_text = _local_ocr(image_bytes)
    if ocr_text and len(ocr_text.strip()) > 5:
        log.info("Local OCR: %d chars", len(ocr_text))
        return ocr_text.strip()

    # 2. Claude Vision
    claude_text = _claude_vision(image_bytes, mime_type)
    if claude_text:
        return claude_text

    # 3. Gemini Vision fallback
    gemini_text = _gemini_vision(image_bytes, mime_type)
    if gemini_text:
        return gemini_text

    size_kb = len(image_bytes) / 1024
    return f"(รูปภาพ {size_kb:.0f}KB — ไม่สามารถอ่านได้ในขณะนี้)"


def _local_ocr(image_bytes: bytes) -> str:
    """Call local OCR service."""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = http_requests.post(
            f"{settings.ocr_url}/api/ocr/base64",
            json={"image": b64},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("text", "")
        log.warning("Local OCR status %d", resp.status_code)
        return ""
    except Exception as e:
        log.debug("Local OCR error: %s", e)
        return ""


def _claude_vision(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Analyze image using Claude Vision API."""
    key = settings.anthropic_api_key
    if not key:
        return ""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        model = settings.ai_model or "claude-haiku-4-5-20251001"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": 2048,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": IMAGE_ANALYSIS_PROMPT},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": mime_type, "data": b64,
                    }},
                ],
            }],
        }
        resp = http_requests.post(CLAUDE_API_URL, headers=headers, json=body, timeout=30)
        if resp.status_code == 200:
            text = resp.json()["content"][0]["text"].strip()
            log.info("Claude Vision: %d chars", len(text))
            return text
        return ""
    except Exception as e:
        log.error("Claude Vision error: %s", e)
        return ""


def _gemini_vision(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Analyze image using Gemini Vision (fallback)."""
    key = settings.gemini_api_key
    if not key:
        return ""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        contents = [{
            "parts": [
                {"text": IMAGE_ANALYSIS_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ]
        }]
        for model in GEMINI_MODELS:
            resp = http_requests.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={key}",
                json={"contents": contents},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if resp.status_code == 429:
                continue
            return ""
        return ""
    except Exception as e:
        log.error("Gemini Vision error: %s", e)
        return ""


# ══════════════════════════════════════════════
#  Digest & Q&A
# ══════════════════════════════════════════════

def generate_digest(chat_name: str, date: str, messages: list[dict]) -> dict | None:
    """Generate a structured daily digest from chat messages. Returns parsed dict or None.

    Includes OCR-extracted text from images (prefixed with [รูปภาพ-OCR]).
    """
    if not messages:
        return None

    # Build transcript: include text messages AND OCR-extracted image text
    lines = []
    image_count = 0
    for m in messages:
        text = m.get("text", "")
        sender = m.get("sender") or m.get("sender_name", "?")
        if text:
            lines.append(f"[{sender}]: {text}")
        if m.get("content_type") == "IMAGE":
            image_count += 1
            if not text:
                lines.append(f"[{sender}]: (ส่งรูปภาพ — ยังไม่ได้ OCR)")

    transcript = "\n".join(lines)
    has_ocr = any("[รูปภาพ-OCR]" in l for l in lines)

    # Use enhanced prompt if images with OCR data are present
    system = DIGEST_SYSTEM_PROMPT
    if has_ocr:
        system = DIGEST_WITH_IMAGES_PROMPT

    prompt = (
        f"กลุ่ม: {chat_name}\nวันที่: {date}\n"
        f"จำนวนข้อความ: {len(messages)} (รูปภาพ: {image_count})\n\n"
        f"ข้อความ:\n{transcript}"
    )
    raw = _call_ai(prompt, system)
    if raw.startswith("[ERROR]"):
        log.error("Digest generation failed: %s", raw)
        return None
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.error("Digest JSON parse failed: %s", raw[:200])
        return None


def ask_question(
    question: str, digests: list[dict], chat_name: str = "",
    conversation_history: list[dict] | None = None,
) -> str:
    """Answer a question using daily digests as context."""
    context_parts = []
    for d in digests:
        ctx = f"📅 {d.get('date', '?')}:"
        digest_data = d.get("digest_json", d)
        if isinstance(digest_data, str):
            try:
                digest_data = json.loads(digest_data)
            except (json.JSONDecodeError, TypeError):
                digest_data = d
        if isinstance(digest_data, dict):
            if digest_data.get("summary"):
                ctx += f"\n  สรุป: {digest_data['summary']}"
            for field in ("key_events", "decisions", "action_items", "problems", "numbers"):
                items = digest_data.get(field)
                if items and isinstance(items, list) and len(items) > 0:
                    ctx += f"\n  {field}: {json.dumps(items, ensure_ascii=False)}"
        context_parts.append(ctx)

    context = "\n\n".join(context_parts) if context_parts else "(ไม่มีข้อมูลสรุป)"
    prompt = f"กลุ่ม: {chat_name}\n\n=== ข้อมูลสรุปรายวัน ===\n{context}\n\n=== คำถาม ===\n{question}"

    if conversation_history:
        history = "\n".join(
            f"{'User' if m['role'] == 'user' else 'AI'}: {m['content']}"
            for m in conversation_history[-6:]
        )
        prompt = f"ประวัติการสนทนา:\n{history}\n\n{prompt}"

    prompt += (
        "\n\n=== คำสั่งเพิ่มเติม ===\n"
        "หลังจากตอบคำถามแล้ว ให้เพิ่มบรรทัด ---SUGGESTIONS--- "
        "แล้วตามด้วยคำถามแนะนำ 3-4 ข้อที่เกี่ยวข้อง (ภาษาไทย สั้นๆ) คั่นด้วย | เช่น:\n"
        "---SUGGESTIONS---\n"
        "รายละเอียดเพิ่มเติม|ใครรับผิดชอบ|เทียบกับเมื่อวาน|แผนแก้ไข"
    )
    return _call_ai(prompt, QA_SYSTEM_PROMPT)


def generate_line_summary(
    chat_name: str, messages: list[dict],
    period_start: str = "", period_end: str = "",
    level: str = "normal",
) -> str:
    """Generate a LINE-friendly text summary."""
    if not messages:
        return ""
    transcript = "\n".join(
        f"[{m['sender']}]: {m['text']}" for m in messages if m.get("text")
    )
    senders = set(m["sender"] for m in messages if m.get("sender"))
    prompt = (
        f"กลุ่ม: {chat_name}\n"
        f"ช่วงเวลา: {period_start} ถึง {period_end}\n"
        f"จำนวนข้อความ: {len(messages)} จาก {len(senders)} คน\n\n"
        f"ข้อความทั้งหมด:\n{transcript}"
    )
    system = _SUMMARY_TEMPLATES.get(level, LINE_SUMMARY_NORMAL_PROMPT)
    result = _call_ai(prompt, system)
    if result.startswith("[ERROR]"):
        log.error("Summary generation failed: %s", result)
        return ""
    return result
