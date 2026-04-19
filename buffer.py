"""Buffer API helper (GraphQL) for publishing Pinterest pins.

Buffer's classic REST API is deprecated; modern tokens must use the GraphQL
endpoint at https://api.buffer.com.

Flow:
  1. User generates a Buffer token at https://publish.buffer.com/settings/api
     and connects at least one Pinterest channel inside Buffer.
  2. We query `organizations` to find the user's orgs.
  3. For each org we query `channels(input: {organizationId})` and keep only
     the Pinterest ones, extracting their boards from `metadata.boards`.
  4. On publish we upload the PNG to catbox.moe (anonymous, no auth) to obtain
     a public URL, then call `createPost` with mode=shareNow so the pin goes
     live immediately on the chosen board.
"""

from __future__ import annotations

import os
from pathlib import Path
import requests

BUFFER_GRAPHQL = "https://api.buffer.com"
CATBOX_URL = "https://catbox.moe/user/api.php"
TIMEOUT = 45


class BufferError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("BUFFER_ACCESS_TOKEN", "").strip()
    if not token:
        raise BufferError(
            "BUFFER_ACCESS_TOKEN is not set. Generate one at "
            "https://publish.buffer.com/settings/api and add it to .env"
        )
    return token


def is_configured() -> bool:
    return bool(os.environ.get("BUFFER_ACCESS_TOKEN", "").strip())


def _graphql(query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        BUFFER_GRAPHQL,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=TIMEOUT,
    )
    try:
        payload = resp.json()
    except Exception:
        raise BufferError(f"Buffer returned non-JSON (HTTP {resp.status_code}): {resp.text[:300]}")
    if resp.status_code != 200:
        raise BufferError(f"Buffer HTTP {resp.status_code}: {payload}")
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise BufferError(f"Buffer GraphQL errors: {msgs}")
    return payload.get("data", {})


def list_organizations() -> list[dict]:
    data = _graphql("query { account { organizations { id name } } }")
    acct = data.get("account") or {}
    return acct.get("organizations") or []


def list_pinterest_channels() -> list[dict]:
    """Return flattened list of Pinterest channels across all orgs, each with boards."""
    orgs = list_organizations()
    if not orgs:
        return []
    query = """
    query PinterestChannels($orgId: OrganizationId!) {
      channels(input: { organizationId: $orgId }) {
        id
        name
        service
        serviceId
        avatar
        metadata {
          ... on PinterestMetadata {
            boards { serviceId name }
          }
        }
      }
    }
    """
    out: list[dict] = []
    for org in orgs:
        data = _graphql(query, {"orgId": org["id"]})
        for ch in data.get("channels", []) or []:
            if (ch.get("service") or "").lower() != "pinterest":
                continue
            meta = ch.get("metadata") or {}
            boards = meta.get("boards") or []
            out.append({
                "id": ch.get("id"),
                "name": ch.get("name"),
                "serviceId": ch.get("serviceId"),
                "avatar": ch.get("avatar"),
                "organizationId": org["id"],
                "organizationName": org.get("name"),
                "boards": [
                    {"serviceId": b.get("serviceId"), "name": b.get("name")}
                    for b in boards if b.get("serviceId")
                ],
            })
    return out


def upload_to_catbox(path: Path) -> str:
    with open(path, "rb") as f:
        r = requests.post(
            CATBOX_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (path.name, f, "image/png")},
            timeout=TIMEOUT,
        )
    if r.status_code != 200:
        raise BufferError(f"catbox upload failed: HTTP {r.status_code} {r.text[:300]}")
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise BufferError(f"catbox returned unexpected response: {url[:200]}")
    return url


def _public_image_url(path: Path, filename: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base and not base.startswith("http://localhost") and not base.startswith("http://127."):
        return f"{base}/static/generated/{filename}"
    return upload_to_catbox(path)


def create_pin(
    channel_id: str,
    board_service_id: str,
    title: str,
    description: str,
    image_path: Path,
    filename: str,
    link: str | None = None,
) -> dict:
    if not channel_id:
        raise BufferError("channel_id is required")
    if not board_service_id:
        raise BufferError("board_service_id is required — pick a Pinterest board")

    image_url = _public_image_url(image_path, filename)

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
        "channelId": channel_id,
        "text": (description or title or "").strip() or "New pin",
        "imageUrl": image_url,
        "title": ((title or "").strip()[:100]) or None,
        "link": link or None,
        "boardServiceId": board_service_id,
    }
    data = _graphql(mutation, variables)
    result = data.get("createPost") or {}
    if result.get("__typename") == "MutationError":
        raise BufferError(f"Buffer rejected the post: {result.get('message')}")
    return {
        "buffer_response": result,
        "image_url": image_url,
    }
