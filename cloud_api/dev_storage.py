"""Local development stand-in for S3's presigned links (resume/storage.py).

Registered only in dev mode. Accepts an upload or serves a download only when
the request carries a valid, unexpired signature for exactly that key, the way
S3 does for presigned requests. Not used in production.
"""

from flask import Blueprint, Response, current_app, jsonify, request

dev_storage = Blueprint("dev_storage", __name__)


def _storage():
    return current_app.config["STORAGE"]


def _expires(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dev_storage.post("/dev-storage/upload")
def upload():
    form, file = request.form, request.files.get("file")
    if not _storage().verify("upload", form.get("key", ""), _expires(form.get("expires")), form.get("signature", "")):
        return jsonify({"error": "invalid or expired upload signature"}), 403
    if file is None:
        return jsonify({"error": "file required"}), 400
    try:
        _storage().write(form["key"], file.read())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return "", 204


@dev_storage.get("/dev-storage/file")
def download():
    args = request.args
    key = args.get("key", "")
    if not _storage().verify("download", key, _expires(args.get("expires")), args.get("signature", "")):
        return jsonify({"error": "invalid or expired download signature"}), 403
    data = _storage().read(key)
    if data is None:
        return jsonify({"error": "no such file"}), 404
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{args.get("filename", "resume")}"'})
