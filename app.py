import json
import uuid
import random
import math
import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings

app = Flask(__name__, static_folder="src", static_url_path="")

K_FACTOR = 32
MAX_MEMES = 50  # Auto-rotate when exceeding this count


def get_connection_string():
    return os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def get_table_client():
    conn = get_connection_string()
    service = TableServiceClient.from_connection_string(conn)
    return service.get_table_client("MemeData")


def get_blob_container():
    conn = get_connection_string()
    service = BlobServiceClient.from_connection_string(conn)
    return service.get_container_client("memes")


def meme_entity_to_dict(entity):
    created = entity.get("CreatedAt", "")
    return {
        "id": entity["RowKey"],
        "name": entity.get("Name", ""),
        "imageUrl": entity.get("ImageUrl", ""),
        "elo": entity.get("Elo", 1200),
        "wins": entity.get("Wins", 0),
        "losses": entity.get("Losses", 0),
        "createdAt": created if isinstance(created, str) else created.isoformat() if created else "",
    }


def expected_score(rating_a, rating_b):
    return 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))


# Serve frontend
@app.route("/")
def index():
    return send_from_directory("src", "index.html")


# GET /api/memes
@app.route("/api/memes", methods=["GET"])
def list_memes():
    table = get_table_client()
    entities = table.list_entities()
    memes = [meme_entity_to_dict(e) for e in entities]
    memes.sort(key=lambda m: m["elo"], reverse=True)
    return jsonify(memes)


# POST /api/memes
@app.route("/api/memes", methods=["POST"])
def upload_meme():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    image_file = request.files["image"]
    name = request.form.get("name", image_file.filename or "unnamed")
    name = name.rsplit(".", 1)[0] if "." in name else name

    meme_id = str(uuid.uuid4())[:8]
    ext = (image_file.filename or "img.png").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        ext = "png"
    blob_name = f"{meme_id}.{ext}"

    container = get_blob_container()
    content_type = image_file.content_type or f"image/{ext}"
    container.upload_blob(
        blob_name,
        image_file.read(),
        content_settings=ContentSettings(content_type=content_type),
        overwrite=True,
    )

    blob_url = f"{container.url}/{blob_name}"

    table = get_table_client()
    entity = {
        "PartitionKey": "meme",
        "RowKey": meme_id,
        "Name": name,
        "ImageUrl": blob_url,
        "Elo": 1200,
        "Wins": 0,
        "Losses": 0,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
    }
    table.create_entity(entity)

    # Auto-rotate: if we exceed MAX_MEMES, delete the oldest
    _auto_rotate(table)

    return jsonify(meme_entity_to_dict(entity)), 201


# DELETE /api/memes/<id>
@app.route("/api/memes/<meme_id>", methods=["DELETE"])
def delete_meme(meme_id):
    table = get_table_client()

    try:
        entity = table.get_entity("meme", meme_id)
    except Exception:
        return jsonify({"error": "Meme not found"}), 404

    _delete_meme_by_entity(table, entity)
    return jsonify({"deleted": meme_id})


# GET /api/pair?winnerId=xxx
@app.route("/api/pair", methods=["GET"])
def get_pair():
    table = get_table_client()
    all_memes = [meme_entity_to_dict(e) for e in table.list_entities()]

    if len(all_memes) < 2:
        return jsonify({"error": "Need at least 2 memes"}), 400

    winner_id = request.args.get("winnerId")

    if winner_id:
        # Winner stays — find a new challenger
        winner = next((m for m in all_memes if m["id"] == winner_id), None)
        if not winner:
            winner_id = None  # winner was deleted, fall through to random pair

    if winner_id and winner:
        others = [m for m in all_memes if m["id"] != winner_id]
        # Prefer memes with fewer battles as challenger
        others.sort(key=lambda m: m["wins"] + m["losses"])
        pool_size = min(len(others), max(3, len(others) // 2))
        challenger = others[random.randint(0, pool_size - 1)]
        return jsonify({"left": winner, "right": challenger, "keepLeft": True})

    # No winner — pick a fresh random pair
    all_memes.sort(key=lambda m: m["wins"] + m["losses"])
    pool_size = min(len(all_memes), max(4, len(all_memes) // 2))
    i = random.randint(0, pool_size - 1)
    meme_a = all_memes[i]

    remaining = [m for m in all_memes if m["id"] != meme_a["id"]]
    meme_b = random.choice(remaining)

    return jsonify({"left": meme_a, "right": meme_b, "keepLeft": False})


# POST /api/vote
@app.route("/api/vote", methods=["POST"])
def submit_vote():
    body = request.get_json()
    if not body:
        return jsonify({"error": "Invalid JSON"}), 400

    winner_id = body.get("winnerId")
    loser_id = body.get("loserId")

    if not winner_id or not loser_id:
        return jsonify({"error": "winnerId and loserId required"}), 400

    table = get_table_client()

    try:
        winner = table.get_entity("meme", winner_id)
        loser = table.get_entity("meme", loser_id)
    except Exception:
        return jsonify({"error": "Meme not found"}), 404

    w_elo = winner.get("Elo", 1200)
    l_elo = loser.get("Elo", 1200)

    e_w = expected_score(w_elo, l_elo)
    e_l = expected_score(l_elo, w_elo)

    winner["Elo"] = round(w_elo + K_FACTOR * (1 - e_w))
    winner["Wins"] = winner.get("Wins", 0) + 1
    table.update_entity(winner, mode="merge")

    loser["Elo"] = round(l_elo + K_FACTOR * (0 - e_l))
    loser["Losses"] = loser.get("Losses", 0) + 1
    table.update_entity(loser, mode="merge")

    return jsonify({
        "winner": meme_entity_to_dict(winner),
        "loser": meme_entity_to_dict(loser),
    })


def _delete_meme_by_entity(table, entity):
    """Delete a meme's blob and table entity."""
    image_url = entity.get("ImageUrl", "")
    blob_name = image_url.rsplit("/", 1)[-1] if image_url else None
    if blob_name:
        try:
            container = get_blob_container()
            container.delete_blob(blob_name)
        except Exception:
            pass
    table.delete_entity(entity["PartitionKey"], entity["RowKey"])


def _auto_rotate(table):
    """If meme count exceeds MAX_MEMES, delete the oldest ones."""
    entities = list(table.list_entities())
    if len(entities) <= MAX_MEMES:
        return

    # Sort by CreatedAt ascending (oldest first), fallback to empty string
    entities.sort(key=lambda e: e.get("CreatedAt", "") or "")
    to_delete = entities[: len(entities) - MAX_MEMES]
    for entity in to_delete:
        _delete_meme_by_entity(table, entity)


# POST /api/rotate - Admin: force rotation (delete oldest N, keep total at MAX_MEMES)
@app.route("/api/rotate", methods=["POST"])
def rotate_memes():
    table = get_table_client()
    entities = list(table.list_entities())

    body = request.get_json() or {}
    target = body.get("maxCount", MAX_MEMES)

    if len(entities) <= target:
        return jsonify({"message": "No rotation needed", "count": len(entities)})

    entities.sort(key=lambda e: e.get("CreatedAt", "") or "")
    to_delete = entities[: len(entities) - target]
    deleted_ids = []
    for entity in to_delete:
        _delete_meme_by_entity(table, entity)
        deleted_ids.append(entity["RowKey"])

    return jsonify({
        "deleted": deleted_ids,
        "remaining": len(entities) - len(deleted_ids),
    })
