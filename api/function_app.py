import azure.functions as func
import json
import uuid
import random
import math
import os
from azure.data.tables import TableServiceClient, TableClient
from azure.storage.blob import BlobServiceClient, ContentSettings

app = func.FunctionApp()

K_FACTOR = 32


def get_connection_string():
    return os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def get_table_client() -> TableClient:
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


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def json_response(data, status_code=200):
    return func.HttpResponse(
        json.dumps(data),
        status_code=status_code,
        mimetype="application/json",
        headers=cors_headers(),
    )


def options_response():
    return func.HttpResponse(status_code=204, headers=cors_headers())


# GET /api/memes - List all memes
@app.route(route="memes", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def list_memes(req: func.HttpRequest) -> func.HttpResponse:
    table = get_table_client()
    entities = table.list_entities()
    memes = [meme_entity_to_dict(e) for e in entities]
    memes.sort(key=lambda m: m["elo"], reverse=True)
    return json_response(memes)


# POST /api/memes - Upload a new meme
@app.route(route="memes", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def upload_meme(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Parse multipart form data
        files = req.files
        if "image" not in files:
            return json_response({"error": "No image file provided"}, 400)

        image_file = files["image"]
        name = req.form.get("name", image_file.filename or "unnamed")
        name = name.rsplit(".", 1)[0] if "." in name else name

        meme_id = str(uuid.uuid4())[:8]
        ext = (image_file.filename or "img.png").rsplit(".", 1)[-1].lower()
        if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
            ext = "png"
        blob_name = f"{meme_id}.{ext}"

        # Upload image to Blob Storage
        container = get_blob_container()
        content_type = image_file.content_type or f"image/{ext}"
        container.upload_blob(
            blob_name,
            image_file.read(),
            content_settings=ContentSettings(content_type=content_type),
            overwrite=True,
        )

        # Build public URL
        blob_url = f"{container.url}/{blob_name}"

        # Save metadata to Table Storage
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

        return json_response(meme_entity_to_dict(entity), 201)
    except Exception as e:
        return json_response({"error": str(e)}, 500)


# DELETE /api/memes/{id}
@app.route(
    route="memes/{id}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS
)
def delete_meme(req: func.HttpRequest) -> func.HttpResponse:
    meme_id = req.route_params.get("id")
    if not meme_id:
        return json_response({"error": "Missing meme id"}, 400)

    try:
        table = get_table_client()

        # Get entity to find blob name
        try:
            entity = table.get_entity("meme", meme_id)
            image_url = entity.get("ImageUrl", "")
            blob_name = image_url.rsplit("/", 1)[-1] if image_url else None
        except Exception:
            return json_response({"error": "Meme not found"}, 404)

        # Delete blob
        if blob_name:
            try:
                container = get_blob_container()
                container.delete_blob(blob_name)
            except Exception:
                pass  # Blob may already be gone

        # Delete table entity
        table.delete_entity("meme", meme_id)

        return json_response({"deleted": meme_id})
    except Exception as e:
        return json_response({"error": str(e)}, 500)


# GET /api/pair - Get a random pair for battle
@app.route(route="pair", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_pair(req: func.HttpRequest) -> func.HttpResponse:
    table = get_table_client()
    all_memes = [meme_entity_to_dict(e) for e in table.list_entities()]

    if len(all_memes) < 2:
        return json_response({"error": "Need at least 2 memes"}, 400)

    # Prefer memes with fewer battles (same logic as original)
    all_memes.sort(key=lambda m: m["wins"] + m["losses"])
    pool_size = min(len(all_memes), max(4, len(all_memes) // 2))
    i = random.randint(0, pool_size - 1)
    meme_a = all_memes[i]

    remaining = [m for m in all_memes if m["id"] != meme_a["id"]]
    meme_b = random.choice(remaining)

    return json_response({"left": meme_a, "right": meme_b})


# POST /api/vote - Submit a vote
@app.route(route="vote", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def submit_vote(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return json_response({"error": "Invalid JSON"}, 400)

    winner_id = body.get("winnerId")
    loser_id = body.get("loserId")

    if not winner_id or not loser_id:
        return json_response({"error": "winnerId and loserId required"}, 400)

    table = get_table_client()

    try:
        winner = table.get_entity("meme", winner_id)
        loser = table.get_entity("meme", loser_id)
    except Exception:
        return json_response({"error": "Meme not found"}, 404)

    w_elo = winner.get("Elo", 1200)
    l_elo = loser.get("Elo", 1200)

    e_w = expected_score(w_elo, l_elo)
    e_l = expected_score(l_elo, w_elo)

    new_w_elo = round(w_elo + K_FACTOR * (1 - e_w))
    new_l_elo = round(l_elo + K_FACTOR * (0 - e_l))

    winner["Elo"] = new_w_elo
    winner["Wins"] = winner.get("Wins", 0) + 1
    table.update_entity(winner, mode="merge")

    loser["Elo"] = new_l_elo
    loser["Losses"] = loser.get("Losses", 0) + 1
    table.update_entity(loser, mode="merge")

    return json_response(
        {
            "winner": meme_entity_to_dict(winner),
            "loser": meme_entity_to_dict(loser),
        }
    )
