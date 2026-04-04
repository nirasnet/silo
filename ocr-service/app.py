import io
import logging
import os
import tempfile

import easyocr
from flask import Flask, jsonify, request
from PIL import Image

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [OCR] %(message)s")
log = logging.getLogger(__name__)

PORT = int(os.getenv("OCR_PORT", "9091"))
MAX_DIMENSION = 1600  # Resize images larger than this to speed up OCR on CPU

# Load EasyOCR reader once at startup (Thai + English)
# GPU is used automatically if available, otherwise CPU
log.info("Loading EasyOCR model (th+en) — first time will download ~200MB...")
reader = easyocr.Reader(["th", "en"], gpu=False)
log.info("EasyOCR model loaded and ready")


def _resize_if_needed(img_bytes: bytes) -> bytes:
    """Resize image if too large, to keep OCR fast on CPU."""
    try:
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        if max(w, h) <= MAX_DIMENSION:
            return img_bytes
        ratio = MAX_DIMENSION / max(w, h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = img.format or "PNG"
        if fmt.upper() == "JPEG" or img.mode != "RGBA":
            if img.mode == "RGBA":
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=85)
        else:
            img.save(buf, format="PNG")
        log.info("Resized %dx%d → %dx%d (%.0f%% smaller)", w, h, new_w, new_h, (1 - len(buf.getvalue()) / len(img_bytes)) * 100)
        return buf.getvalue()
    except Exception as e:
        log.warning("Resize failed: %s — using original", e)
        return img_bytes


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ocr-service", "languages": ["th", "en"]})


@app.route("/api/ocr", methods=["POST"])
def ocr():
    """
    Accept image via:
      - multipart file upload (field: "image")
      - raw image bytes in request body (set Content-Type: image/*)
    Returns extracted text from Thai/English handwriting or print.
    """
    # Get image data
    if "image" in request.files:
        file = request.files["image"]
        img_bytes = file.read()
        filename = file.filename or "upload"
    elif request.content_type and request.content_type.startswith("image/"):
        img_bytes = request.data
        filename = "raw-upload"
    else:
        return jsonify({"error": "No image provided. Send as multipart 'image' field or raw image body"}), 400

    if not img_bytes:
        return jsonify({"error": "Empty image"}), 400

    log.info("OCR request: %s (%d bytes)", filename, len(img_bytes))

    img_bytes = _resize_if_needed(img_bytes)

    # Write to temp file for EasyOCR
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name

    try:
        results = reader.readtext(tmp_path)
    finally:
        os.unlink(tmp_path)

    # results = list of (bbox, text, confidence)
    texts = []
    full_text_parts = []
    for bbox, text, conf in results:
        texts.append({
            "text": text,
            "confidence": round(conf, 4),
            "bbox": [[int(p[0]), int(p[1])] for p in bbox],
        })
        full_text_parts.append(text)

    full_text = " ".join(full_text_parts)
    log.info("OCR result: %d segments, text=%r", len(texts), full_text[:100])

    return jsonify({
        "text": full_text,
        "segments": texts,
        "segment_count": len(texts),
    })


@app.route("/api/ocr/base64", methods=["POST"])
def ocr_base64():
    """Accept base64-encoded image in JSON body."""
    import base64

    data = request.get_json(silent=True)
    if not data or "image" not in data:
        return jsonify({"error": "JSON body with 'image' (base64) required"}), 400

    try:
        img_bytes = base64.b64decode(data["image"])
    except Exception:
        return jsonify({"error": "Invalid base64 data"}), 400

    log.info("OCR base64 request: %d bytes", len(img_bytes))

    img_bytes = _resize_if_needed(img_bytes)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name

    try:
        results = reader.readtext(tmp_path)
    finally:
        os.unlink(tmp_path)

    texts = []
    full_text_parts = []
    for bbox, text, conf in results:
        texts.append({
            "text": text,
            "confidence": round(conf, 4),
            "bbox": [[int(p[0]), int(p[1])] for p in bbox],
        })
        full_text_parts.append(text)

    full_text = " ".join(full_text_parts)
    log.info("OCR result: %d segments, text=%r", len(texts), full_text[:100])

    return jsonify({
        "text": full_text,
        "segments": texts,
        "segment_count": len(texts),
    })


if __name__ == "__main__":
    log.info("Starting OCR service on port %d (threaded)", PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
