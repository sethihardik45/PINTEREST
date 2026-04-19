import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_from_directory

load_dotenv()

from generator import generate_image, OUTPUT_DIR
import buffer as buf

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    try:
        result = generate_image()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/static/generated/<path:filename>")
def generated_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


# ---------- Buffer → Pinterest ----------

@app.route("/buffer/status")
def buffer_status():
    return jsonify({"configured": buf.is_configured()})


@app.route("/buffer/channels")
def buffer_channels():
    try:
        channels = buf.list_pinterest_channels()
        return jsonify({"ok": True, "channels": channels})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/buffer/publish", methods=["POST"])
def buffer_publish():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename")
    channel_id = data.get("channel_id")
    board_service_id = data.get("board_service_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    link = (data.get("link") or "").strip() or None

    if not filename or not channel_id or not board_service_id:
        return jsonify({"ok": False, "error": "filename, channel_id and board_service_id are required"}), 400

    path = Path(OUTPUT_DIR) / filename
    if not path.exists():
        return jsonify({"ok": False, "error": "Image not found. Regenerate first."}), 404

    try:
        result = buf.create_pin(
            channel_id=channel_id,
            board_service_id=board_service_id,
            title=title,
            description=description,
            image_path=path,
            filename=filename,
            link=link,
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------- Cron: one-shot generate + publish ----------

@app.route("/cron/run", methods=["GET", "POST"])
def cron_run():
    """Single endpoint that generates an image and publishes it to Pinterest via Buffer.

    Point cron-job.org (or any scheduler) at this URL every 2 hours.

    Optional auth: if CRON_SECRET is set in .env, callers must pass the same value
    via `?token=...` query param or the `X-Cron-Token` header.

    Optional env overrides:
      CRON_BUFFER_CHANNEL_NAME  (default "psychicslumus")
      CRON_BOARD_SERVICE_ID     (if set, used directly — bypasses topic matching)
      CRON_TITLE                (default "Free Chat with Psychics")
      CRON_LINK                 (default "https://lumusapp.com/81vh/pin_social")
    """
    secret = os.environ.get("CRON_SECRET", "").strip()
    if secret:
        supplied = (request.args.get("token") or request.headers.get("X-Cron-Token") or "").strip()
        if supplied != secret:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        gen = generate_image()
    except Exception as exc:
        return jsonify({"ok": False, "stage": "generate", "error": str(exc)}), 500

    filename = gen.get("filename")
    caption = gen.get("caption") or ""
    topic = gen.get("topic") or ""

    try:
        channels = buf.list_pinterest_channels()
    except Exception as exc:
        return jsonify({"ok": False, "stage": "list_channels", "error": str(exc), "generated": gen}), 500

    if not channels:
        return jsonify({"ok": False, "stage": "list_channels", "error": "No Pinterest channel in Buffer", "generated": gen}), 500

    preferred_name = os.environ.get("CRON_BUFFER_CHANNEL_NAME", "psychicslumus").strip().lower()
    channel = next(
        (c for c in channels if (c.get("name") or "").strip().lower() == preferred_name),
        channels[0],
    )

    def _norm(s: str) -> str:
        return (s or "").strip().lower()

    boards = channel.get("boards") or []
    forced_board = os.environ.get("CRON_BOARD_SERVICE_ID", "").strip()
    board_name = None

    if forced_board:
        board_service_id = forced_board
        board_name = next((b["name"] for b in boards if b.get("serviceId") == forced_board), None)
    else:
        match = next((b for b in boards if _norm(b.get("name")) == _norm(topic)), None)
        if not match and boards:
            match = boards[0]
        if not match:
            return jsonify({"ok": False, "stage": "pick_board", "error": "No boards available on the channel. Reconnect Pinterest in Buffer or set CRON_BOARD_SERVICE_ID.", "generated": gen}), 500
        board_service_id = match.get("serviceId")
        board_name = match.get("name")

    path = Path(OUTPUT_DIR) / filename
    if not path.exists():
        return jsonify({"ok": False, "stage": "locate_image", "error": "Generated image missing on disk", "generated": gen}), 500

    title = os.environ.get("CRON_TITLE", "Free Chat with Psychics")
    link = os.environ.get("CRON_LINK", "https://lumusapp.com/81vh/pin_social")

    try:
        result = buf.create_pin(
            channel_id=channel["id"],
            board_service_id=board_service_id,
            title=title,
            description=caption,
            image_path=path,
            filename=filename,
            link=link,
        )
    except Exception as exc:
        return jsonify({"ok": False, "stage": "publish", "error": str(exc), "generated": gen}), 500

    return jsonify({
        "ok": True,
        "filename": filename,
        "topic": topic,
        "caption": caption,
        "channel": channel.get("name"),
        "board": board_name,
        "board_service_id": board_service_id,
        "title": title,
        "link": link,
        **result,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
