import json
import uuid
import random
import math
import os
from flask import Flask, request, jsonify, send_from_directory
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings

app = Flask(__name__, static_folder="src", static_url_path="")

K_FACTOR = 32


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
    return {
        "id": entity["RowKey"],
        "name": entity.get("Name", ""),
        "imageUrl": entity.get("ImageUrl", ""),
        "elo": entity.get("Elo", 1200),
        "wins": entity.get("Wins", 0),
        "losses": entity.get("Losses", 0),
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
    }
    table.create_entity(entity)

    return jsonify(meme_entity_to_dict(entity)), 201


# DELETE /api/memes/<id>
@app.route("/api/memes/<meme_id>", methods=["DELETE"])
def delete_meme(meme_id):
    table = get_table_client()

    try:
        entity = table.get_entity("meme", meme_id)
        image_url = entity.get("ImageUrl", "")
        blob_name = image_url.rsplit("/", 1)[-1] if image_url else None
    except Exception:
        return jsonify({"error": "Meme not found"}), 404

    if blob_name:
        try:
            container = get_blob_container()
            container.delete_blob(blob_name)
        except Exception:
            pass

    table.delete_entity("meme", meme_id)
    return jsonify({"deleted": meme_id})


# GET /api/pair
@app.route("/api/pair", methods=["GET"])
def get_pair():
    table = get_table_client()
    all_memes = [meme_entity_to_dict(e) for e in table.list_entities()]

    if len(all_memes) < 2:
        return jsonify({"error": "Need at least 2 memes"}), 400

    all_memes.sort(key=lambda m: m["wins"] + m["losses"])
    pool_size = min(len(all_memes), max(4, len(all_memes) // 2))
    i = random.randint(0, pool_size - 1)
    meme_a = all_memes[i]

    remaining = [m for m in all_memes if m["id"] != meme_a["id"]]
    meme_b = random.choice(remaining)

    return jsonify({"left": meme_a, "right": meme_b})


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
