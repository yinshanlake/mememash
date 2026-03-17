#!/usr/bin/env python3
"""
Daily meme rotation script.
Deletes the 5 oldest memes and uploads 5 new ones from imgflip's popular templates.
Designed to run as a GitHub Actions cron job.

Usage:
  MEMEMASH_API_URL=https://mememash-app.azurewebsites.net python daily_rotate.py
"""

import json
import os
import sys
import random
import tempfile
import urllib.request
import urllib.error

API_URL = os.environ.get("MEMEMASH_API_URL", "https://mememash-app.azurewebsites.net")
ROTATE_COUNT = int(os.environ.get("ROTATE_COUNT", "5"))


def api_get(path):
    req = urllib.request.Request(f"{API_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def api_delete(path):
    req = urllib.request.Request(f"{API_URL}{path}", method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def api_upload(name, image_data, filename):
    boundary = "----MemeMashDailyRotate"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="name"\r\n\r\n'
    body += name.encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: image/jpeg\r\n\r\n"
    body += image_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{API_URL}/api/memes",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_imgflip_templates():
    """Get popular meme templates from imgflip (free, no auth needed)."""
    req = urllib.request.Request(
        "https://api.imgflip.com/get_memes",
        headers={"User-Agent": "MemeMash/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    if not data.get("success"):
        return []
    return data.get("data", {}).get("memes", [])


def download_image(url):
    """Download an image and return its bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "MemeMash/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def main():
    print(f"MemeMash Daily Rotation: {ROTATE_COUNT} in, {ROTATE_COUNT} out")
    print(f"API: {API_URL}")
    print()

    # Step 1: Get current memes
    current_memes = api_get("/api/memes")
    print(f"Current meme count: {len(current_memes)}")

    # Step 2: Delete oldest N memes
    if current_memes:
        # Sort by createdAt ascending (oldest first); memes without createdAt go first
        sorted_by_age = sorted(current_memes, key=lambda m: m.get("createdAt") or "")
        to_delete = sorted_by_age[:ROTATE_COUNT]

        print(f"\nDeleting {len(to_delete)} oldest memes:")
        for m in to_delete:
            try:
                api_delete(f"/api/memes/{m['id']}")
                print(f"  [-] {m['name']} (ELO: {m['elo']}, id: {m['id']})")
            except Exception as e:
                print(f"  [!] Failed to delete {m['id']}: {e}")

    # Step 3: Fetch new meme templates from imgflip
    print(f"\nFetching new memes from imgflip...")
    templates = fetch_imgflip_templates()
    if not templates:
        print("Failed to fetch templates from imgflip")
        sys.exit(1)

    # Exclude memes we already have (by name match)
    current_names = {m["name"].lower().strip() for m in current_memes}
    available = [t for t in templates if t["name"].lower().strip() not in current_names]

    if len(available) < ROTATE_COUNT:
        # If we've exhausted unique ones, just pick random from all
        available = templates

    # Pick random N from available
    picks = random.sample(available, min(ROTATE_COUNT, len(available)))

    print(f"\nUploading {len(picks)} new memes:")
    for t in picks:
        try:
            image_data = download_image(t["url"])
            result = api_upload(t["name"], image_data, f"{t['id']}.jpg")
            print(f"  [+] {t['name']} -> id={result.get('id')}")
        except Exception as e:
            print(f"  [!] Failed to upload {t['name']}: {e}")

    # Final count
    final_memes = api_get("/api/memes")
    print(f"\nDone. Total memes: {len(final_memes)}")


if __name__ == "__main__":
    main()
