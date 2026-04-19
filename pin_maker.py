"""Standalone Pinterest-automation script — modelled on MemeMaker/meme_maker.py.

Runs top-to-bottom on every invocation:
  1. Generate a 1000x1500 Lumus Pinterest image via `generator.generate_image()`
     (random theme + random caption topic + purple overlay + caption text + banners).
  2. Upload the PNG to THIS repo's `images` branch as `latest.png` via the GitHub
     Contents API so Buffer can fetch it via `raw.githubusercontent.com`.
  3. Look up the psychicslumus Pinterest channel in Buffer, pick a board (either
     BUFFER_BOARD_SERVICE_ID env override, topic-name match, or first available),
     then call Buffer's `createPost` with `mode: shareNow` to publish immediately.

Designed to be triggered by GitHub Actions on a cron schedule (every 2 hours) —
no Flask server, no ngrok, no external hosting. Just `python pin_maker.py`.

Required env vars (set these as repo secrets in GitHub):
  GEMINI_API_KEY         — Google AI Studio key
  BUFFER_ACCESS_TOKEN    — https://publish.buffer.com/developers/apps

Auto-provided by GitHub Actions (no secret needed):
  GITHUB_TOKEN           — scoped write access to the running repo
  GITHUB_REPOSITORY      — e.g. "sethihardik45/Pinterest"

Optional overrides:
  GITHUB_IMAGES_BRANCH   — default "images"
  GITHUB_IMAGES_PATH     — default "latest.png"
  BUFFER_CHANNEL_NAME    — default "psychicslumus"
  BUFFER_BOARD_SERVICE_ID — force a specific Pinterest board (bypass name match)
  BUFFER_TITLE           — default "Free Chat with Psychics"
  BUFFER_LINK            — default "https://lumusapp.com/81vh/pin_social"
"""

from __future__ import annotations

import base64
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

# Make relative paths resolve against this file's dir, like meme_maker.py does.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Load .env for local runs (GitHub Actions sets env vars directly, so dotenv is a no-op there).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from generator import generate_image, OUTPUT_DIR
import buffer as buf


GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# Pool of Pinterest-native titles that rotate per-pin so Pinterest's duplicate
# detector doesn't see the same headline on every single post. Each title is
# tuned to feel like an organic editorial pin, not an ad.
TITLE_POOL = [
    "Psychic, Tarot & Astrology Insights",
    "Daily Mystical Guidance",
    "Tarot, Moon & Cosmic Wisdom",
    "Your Daily Cosmic Download",
    "Spiritual Insights for Today",
    "Lumus Psychics — Daily Reading",
    "Energy, Intuition & Signs",
    "Mystical Wisdom Worth Reading",
    "Today's Spiritual Note",
    "Astrology, Tarot & Intuition",
]


def pick_title(topic: str) -> str:
    """Return a varied title. If topic is specific, use it; else random from pool."""
    forced = _env("BUFFER_TITLE")
    if forced:
        return forced
    if topic:
        return f"{topic} — Lumus Psychics"[:100]
    return random.choice(TITLE_POOL)


def decorate_link(link: str, topic: str) -> str:
    """Append UTM params so every pin has a unique destination URL (Pinterest
    collapses pins pointing to an identical URL as duplicates)."""
    if not link:
        return link
    parsed = urlparse(link)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.setdefault("utm_source", "pinterest")
    existing.setdefault("utm_medium", "pin")
    existing.setdefault("utm_campaign", "lumus_auto")
    if topic:
        existing["utm_content"] = topic.lower().replace(" ", "_")[:40]
    existing["t"] = str(int(time.time()))
    return urlunparse(parsed._replace(query=urlencode(existing)))


def upload_to_github(image_path: Path) -> str:
    """Upload PNG bytes to the configured repo/branch and return a raw URL."""
    token = _env("GITHUB_TOKEN")
    repo = _env("GITHUB_IMAGES_REPO") or _env("GITHUB_REPOSITORY")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for GitHub image hosting.")
    if not repo or "/" not in repo:
        raise RuntimeError(
            "GITHUB_REPOSITORY (or GITHUB_IMAGES_REPO) must be set to 'owner/repo'. "
            "GitHub Actions sets GITHUB_REPOSITORY automatically; for local runs, "
            "set GITHUB_IMAGES_REPO in .env."
        )
    branch = _env("GITHUB_IMAGES_BRANCH", "images")
    file_path = _env("GITHUB_IMAGES_PATH", "latest.png")

    with open(image_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Need existing SHA if the file already exists on that branch (PUT is upsert).
    sha = None
    get_resp = requests.get(
        f"{GITHUB_API}/repos/{repo}/contents/{file_path}",
        params={"ref": branch},
        headers=headers,
        timeout=60,
    )
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    elif get_resp.status_code not in (404,):
        raise RuntimeError(f"GitHub GET contents failed: HTTP {get_resp.status_code} {get_resp.text[:300]}")

    payload = {
        "message": f"Publish Pinterest image {int(time.time())}",
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(
        f"{GITHUB_API}/repos/{repo}/contents/{file_path}",
        headers=headers,
        json=payload,
        timeout=60,
    )
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT contents failed: HTTP {put_resp.status_code} {put_resp.text[:400]}")

    # Cache-busting timestamp forces Buffer/Pinterest to re-fetch each time.
    return f"{RAW_BASE}/{repo}/{branch}/{file_path}?t={int(time.time())}"


def pick_channel(channels: list[dict]) -> dict:
    preferred = _env("BUFFER_CHANNEL_NAME", "psychicslumus").lower()
    for ch in channels:
        if (ch.get("name") or "").strip().lower() == preferred:
            return ch
    return channels[0]


def pick_board(channel: dict, topic: str) -> tuple[str, str | None]:
    """Return (board_service_id, board_name)."""
    forced = _env("BUFFER_BOARD_SERVICE_ID")
    boards = channel.get("boards") or []
    if forced:
        name = next((b.get("name") for b in boards if b.get("serviceId") == forced), None)
        return forced, name

    want = topic.strip().lower()
    if want:
        match = next((b for b in boards if (b.get("name") or "").strip().lower() == want), None)
        if match:
            return match["serviceId"], match.get("name")

    if boards:
        return boards[0]["serviceId"], boards[0].get("name")

    raise RuntimeError(
        "No boards available on the channel. Reconnect Pinterest at "
        "https://publish.buffer.com/channels or set BUFFER_BOARD_SERVICE_ID."
    )


def main() -> int:
    print("[1/4] Generating Lumus Pinterest image (Gemini + overlay + caption + banners)...")
    gen = generate_image()
    filename = gen["filename"]
    topic = gen.get("topic") or ""
    caption = gen.get("caption") or ""
    image_path = Path(OUTPUT_DIR) / filename
    print(f"       -> topic:   {topic}")
    print(f"       -> caption: {caption}")
    print(f"       -> file:    {image_path}")

    print("[2/4] Uploading PNG to GitHub raw...")
    image_url = upload_to_github(image_path)
    print(f"       -> public url: {image_url}")

    print("[3/4] Looking up Pinterest channel + board in Buffer...")
    channels = buf.list_pinterest_channels()
    if not channels:
        raise RuntimeError("No Pinterest channel found in Buffer. Connect one at publish.buffer.com/channels.")
    channel = pick_channel(channels)
    board_id, board_name = pick_board(channel, topic)
    print(f"       -> channel: {channel.get('name')} ({channel.get('id')})")
    print(f"       -> board:   {board_name or '(unknown)'} ({board_id})")

    print("[4/4] Publishing pin via Buffer (shareNow)...")
    title = pick_title(topic)
    raw_link = _env("BUFFER_LINK", "https://lumusapp.com/81vh/pin_social")
    link = decorate_link(raw_link, topic)
    print(f"       -> title: {title}")
    print(f"       -> link:  {link}")

    # buffer.create_pin() would re-upload locally; we've already got a public URL,
    # so call the GraphQL mutation directly with our GitHub-hosted URL.
    mutation = """
    mutation PublishPin(
      $channelId: ChannelId!
      $text: String!
      $imageUrl: String!
      $title: String
      $link: String
      $boardServiceId: String!
    ) {
      createPost(input: {
        channelId: $channelId
        schedulingType: automatic
        mode: shareNow
        text: $text
        assets: { images: [{ url: $imageUrl }] }
        metadata: {
          pinterest: {
            title: $title
            url: $link
            boardServiceId: $boardServiceId
          }
        }
      }) {
        __typename
        ... on PostActionSuccess { post { id text } }
        ... on MutationError { message }
      }
    }
    """
    variables = {
        "channelId": channel["id"],
        "text": (caption or title).strip() or "New pin",
        "imageUrl": image_url,
        "title": (title.strip()[:100]) or None,
        "link": link or None,
        "boardServiceId": board_id,
    }
    data = buf._graphql(mutation, variables)
    result = (data or {}).get("createPost") or {}
    if result.get("__typename") == "MutationError":
        raise RuntimeError(f"Buffer rejected the post: {result.get('message')}")

    post_id = (((result.get("post") or {}).get("id")) or "")
    print(f"       -> buffer response: {result}")
    print(f"\nSUCCESS — Pinterest pin queued via Buffer. post_id={post_id}")
    print(f"         title:  {title}")
    print(f"         link:   {link}")
    print(f"         image:  {image_url}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
