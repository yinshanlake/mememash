#!/usr/bin/env python3
"""
MemeMash Meme Collector - CLI tool for batch collecting and uploading memes.

Usage:
  # Download from Reddit
  python meme_collector.py --source reddit --subreddit dankmemes --count 30

  # Import from local folder
  python meme_collector.py --source folder --path ./my-memes/

  # Review staged memes (opens folder in Finder/Explorer)
  python meme_collector.py --review

  # Upload reviewed memes to Azure
  python meme_collector.py --upload

  # Upload with custom endpoint
  python meme_collector.py --upload --api-url https://mememash-app.azurestaticapps.net
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import shutil
from pathlib import Path

STAGING_DIR = Path(__file__).parent.parent / "meme_staging"
UPLOADED_LOG = STAGING_DIR / ".uploaded.json"

# Supported image extensions
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def ensure_staging():
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def download_from_reddit(subreddit: str, count: int, sort: str = "hot"):
    """Download memes from a subreddit using Reddit's public JSON API."""
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        print("Error: urllib is required (included in Python stdlib)")
        sys.exit(1)

    ensure_staging()

    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={count}"
    headers = {"User-Agent": "MemeMash Collector/1.0"}

    print(f"Fetching from r/{subreddit} ({sort}, limit={count})...")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e.code} - {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to fetch Reddit data: {e}")
        sys.exit(1)

    posts = data.get("data", {}).get("children", [])
    downloaded = 0

    for post in posts:
        post_data = post.get("data", {})
        image_url = post_data.get("url", "")
        title = post_data.get("title", "untitled")
        post_id = post_data.get("id", "unknown")

        # Only download direct image links
        if not any(image_url.lower().endswith(ext) for ext in IMAGE_EXTS):
            # Try preview image if direct URL isn't an image
            preview = post_data.get("preview", {})
            images = preview.get("images", [])
            if images:
                image_url = images[0].get("source", {}).get("url", "").replace("&amp;", "&")
                if not image_url:
                    continue
            else:
                continue

        ext = Path(image_url.split("?")[0]).suffix.lower()
        if ext not in IMAGE_EXTS:
            ext = ".jpg"

        # Sanitize filename
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:60].strip()
        filename = f"{post_id}_{safe_title}{ext}"
        filepath = STAGING_DIR / filename

        if filepath.exists():
            continue

        try:
            req = urllib.request.Request(image_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(filepath, "wb") as f:
                    f.write(resp.read())
            downloaded += 1
            print(f"  [{downloaded}] {filename}")
        except Exception as e:
            print(f"  [skip] {title}: {e}")

    print(f"\nDownloaded {downloaded} memes to {STAGING_DIR}/")
    print(f"Run: python {__file__} --review")


def import_from_folder(source_path: str):
    """Copy images from a local folder to staging."""
    source = Path(source_path)
    if not source.is_dir():
        print(f"Error: {source_path} is not a valid directory")
        sys.exit(1)

    ensure_staging()
    copied = 0

    for f in source.iterdir():
        if f.suffix.lower() in IMAGE_EXTS:
            dest = STAGING_DIR / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                copied += 1
                print(f"  [{copied}] {f.name}")

    print(f"\nCopied {copied} images to {STAGING_DIR}/")
    print(f"Run: python {__file__} --review")


def review():
    """Open staging folder for review."""
    ensure_staging()

    files = [f for f in STAGING_DIR.iterdir() if f.suffix.lower() in IMAGE_EXTS]
    print(f"Staging folder: {STAGING_DIR}")
    print(f"Contains {len(files)} images")
    print()
    print("Opening folder - delete any memes you don't want, then run:")
    print(f"  python {__file__} --upload")
    print()

    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", str(STAGING_DIR)])
    elif system == "Windows":
        subprocess.run(["explorer", str(STAGING_DIR)])
    elif system == "Linux":
        subprocess.run(["xdg-open", str(STAGING_DIR)])
    else:
        print(f"Please open manually: {STAGING_DIR}")


def upload(api_url: str):
    """Upload staged memes to MemeMash API."""
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        print("Error: urllib required")
        sys.exit(1)

    ensure_staging()

    # Load previously uploaded log
    uploaded = set()
    if UPLOADED_LOG.exists():
        try:
            uploaded = set(json.loads(UPLOADED_LOG.read_text()))
        except Exception:
            pass

    files = [
        f
        for f in STAGING_DIR.iterdir()
        if f.suffix.lower() in IMAGE_EXTS and f.name not in uploaded
    ]

    if not files:
        print("No new memes to upload.")
        return

    print(f"Uploading {len(files)} memes to {api_url}...")
    success = 0

    for i, filepath in enumerate(files, 1):
        # Build multipart form data manually
        boundary = "----MemeMashBoundary"
        body = b""

        # Add name field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="name"\r\n\r\n'
        body += filepath.stem.encode() + b"\r\n"

        # Add image file
        content_type_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        ct = content_type_map.get(filepath.suffix.lower(), "image/jpeg")

        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="image"; filename="{filepath.name}"\r\n'.encode()
        body += f"Content-Type: {ct}\r\n\r\n".encode()
        body += filepath.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{api_url}/api/memes",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                print(f"  [{i}/{len(files)}] {filepath.name} -> id={result.get('id')}")
                uploaded.add(filepath.name)
                success += 1
        except Exception as e:
            print(f"  [{i}/{len(files)}] FAILED {filepath.name}: {e}")

    # Save uploaded log
    UPLOADED_LOG.write_text(json.dumps(list(uploaded)))

    print(f"\nUploaded {success}/{len(files)} memes successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="MemeMash Meme Collector - batch collect and upload memes"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--source",
        choices=["reddit", "folder"],
        help="Source to collect memes from",
    )
    group.add_argument("--review", action="store_true", help="Open staging folder for review")
    group.add_argument("--upload", action="store_true", help="Upload staged memes to Azure")

    parser.add_argument("--subreddit", default="dankmemes", help="Subreddit to scrape (default: dankmemes)")
    parser.add_argument("--count", type=int, default=25, help="Number of posts to fetch (default: 25)")
    parser.add_argument("--sort", default="hot", choices=["hot", "top", "new"], help="Reddit sort order")
    parser.add_argument("--path", help="Local folder path (for --source folder)")
    parser.add_argument(
        "--api-url",
        default="https://mememash-app.azurewebsites.net",
        help="MemeMash API URL",
    )

    args = parser.parse_args()

    if args.source == "reddit":
        download_from_reddit(args.subreddit, args.count, args.sort)
    elif args.source == "folder":
        if not args.path:
            print("Error: --path required for folder source")
            sys.exit(1)
        import_from_folder(args.path)
    elif args.review:
        review()
    elif args.upload:
        upload(args.api_url)


if __name__ == "__main__":
    main()
