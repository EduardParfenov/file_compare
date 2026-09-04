"""HTTP routes."""

from flask import Blueprint, current_app, jsonify, render_template, request

from app.services import jobs, llm, uploads

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@bp.errorhandler(413)
def too_large(error):
    return jsonify({"error": "Файл превышает максимально допустимый размер"}), 413


@bp.post("/api/upload")
def upload_file():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "Файл не передан (поле file)"}), 400
    try:
        result = uploads.save_upload(
            file,
            current_app.config["UPLOAD_DIR"],
            current_app.config["ALLOWED_EXTENSIONS"],
        )
    except uploads.UploadError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


def _get_chat():
    """Chat-модель LLM: из конфига (тесты) или создаётся из env-настроек."""
    chat = current_app.config.get("LLM_CHAT")
    if chat is None:
        chat = llm.create_chat_model(current_app.config)
    return chat


@bp.post("/api/compare")
def compare():
    data = request.get_json(silent=True) or {}
    upload_id_1 = data.get("upload_id_1")
    upload_id_2 = data.get("upload_id_2")
    if not upload_id_1 or not upload_id_2:
        return jsonify({"error": "Требуются upload_id_1 и upload_id_2"}), 400

    path1 = uploads.get_upload_path(upload_id_1)
    path2 = uploads.get_upload_path(upload_id_2)
    if path1 is None or path2 is None:
        return jsonify({"error": "Файл с указанным upload_id не найден"}), 404

    job_id = jobs.create_job()
    jobs.start_job(
        job_id,
        path1,
        path2,
        _get_chat(),
        synchronous=current_app.config.get("JOBS_SYNCHRONOUS", False),
    )
    return jsonify({"job_id": job_id}), 202


@bp.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get_job(job_id)
    if job is None:
        return jsonify({"error": "Задача не найдена"}), 404
    body = {
        "job_id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "stage_message": job["stage_message"],
    }
    if job["status"] == "done":
        body["result"] = job["result"]
    if job["status"] == "failed":
        body["error"] = job["error"]
    return jsonify(body)
