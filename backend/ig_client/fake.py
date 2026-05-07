"""Fake IG client for offline development.

Why this exists
---------------
The real `IgClient` (instagrapi-backed) cannot be exercised while the
operator's account is in a soft-block. The fake mirrors the surface
`with_session_retry()` consumes in `backend/ingest/run.py`:

    list_collections()                 -> list[FakeCollection]
    list_collection_items(name: str)   -> list[FakeMedia]
    media_to_dict(m)                   -> dict (for posts_raw)

It generates ~40 deterministic posts spread across four collections, with
multi-membership wired in so `post_collections` is non-trivial.

Media routing
-------------
We avoid mocking httpx end-to-end. Every `FakeResource.url` is a real
`file://...` pointing at a placeholder JPEG/MP4 on disk. `media/store.py`
recognises the `file://` scheme and reads the bytes directly with the
correct Content-Length. This is the smaller of the two options the brief
suggested (the alternative — injecting a callable into the store — would
have required threading a fetcher through more layers).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import pathname2url

from backend.config import settings
from backend.ig_client.placeholder import ensure_placeholders


# ---------- data classes ----------------------------------------------------


@dataclass
class FakeUser:
    pk: str
    username: str
    full_name: str
    is_private: bool = False
    profile_pic_url: str | None = None


@dataclass
class FakeResource:
    """One slide of a carousel, or the single resource of an image/video post."""

    media_type: int  # 1=image, 2=video
    url: str
    thumbnail_url: str | None = None

    @property
    def kind(self) -> str:
        return "video" if self.media_type == 2 else "image"


@dataclass
class FakeMedia:
    pk: str
    code: str  # IG "shortcode"
    media_type: int  # 1=image, 2=video, 8=carousel
    user: FakeUser
    caption_text: str | None
    taken_at: datetime
    # Top-level URLs for non-carousels:
    thumbnail_url: str | None = None
    video_url: str | None = None
    # Carousel slides:
    resources: list[FakeResource] = field(default_factory=list)

    # ---- compatibility shims so ingest can serialize via media_to_dict ----

    def model_dump_json(self) -> str:
        return json.dumps(self.dict(), default=str)

    def dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["taken_at"] = self.taken_at.isoformat()
        return d


@dataclass
class FakeCollection:
    id: str
    name: str


# ---------- fixture builder -------------------------------------------------


def _file_url(path: Path) -> str:
    """Build a file:// URL that backend.media.store recognises."""
    return "file://" + pathname2url(str(path.resolve()))


def _make_fixture(media_root: Path | None = None) -> tuple[
    list[FakeCollection],
    dict[str, list[FakeMedia]],
]:
    """Build deterministic collections + items.

    40 posts:
      - 10 single-image (idx 0..9)
      - 10 carousel x 3 slides (idx 10..19)
      - 10 video (idx 20..29)
      - 10 single-image (idx 30..39)

    Collections (locked counts; 'All Posts' has all 40):
      All Posts (40), Inspiration (8), Recipes (5), Travel (12).
      6 posts appear in 2+ named collections.
    """
    media_root = media_root or (settings.data_dir / "fake_media")
    jpg_path, mp4_path = ensure_placeholders(media_root)
    jpg_url = _file_url(jpg_path)
    mp4_url = _file_url(mp4_path)

    base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    users = [
        FakeUser(pk=f"u{i}", username=f"fake_user_{i}", full_name=f"Fake User {i}")
        for i in range(5)
    ]

    posts: list[FakeMedia] = []
    for i in range(40):
        user = users[i % len(users)]
        taken = base_time + timedelta(hours=i)
        if i < 10:
            kind = 1  # image
            resources: list[FakeResource] = []
            thumb = jpg_url
            video = None
        elif i < 20:
            kind = 8  # carousel of 3
            resources = [
                FakeResource(media_type=1, url=jpg_url, thumbnail_url=jpg_url)
                for _ in range(3)
            ]
            thumb = jpg_url
            video = None
        elif i < 30:
            kind = 2  # video
            resources = []
            thumb = jpg_url
            video = mp4_url
        else:
            kind = 1  # image
            resources = []
            thumb = jpg_url
            video = None

        posts.append(
            FakeMedia(
                pk=f"p{i:04d}",
                code=f"shortcode{i:04d}",
                media_type=kind,
                user=user,
                caption_text=f"Caption for fake post #{i}" if i % 4 != 0 else None,
                taken_at=taken,
                thumbnail_url=thumb,
                video_url=video,
                resources=resources,
            )
        )

    collections = [
        FakeCollection(id="all_posts", name="All Posts"),
        FakeCollection(id="col_insp", name="Inspiration"),
        FakeCollection(id="col_rec", name="Recipes"),
        FakeCollection(id="col_trav", name="Travel"),
    ]

    # Membership map. 6 posts appear in 2+ named collections (the M:M
    # exercise). Index choices are deterministic.
    inspiration_idx = [0, 5, 11, 18, 22, 28, 33, 38]            # 8 posts
    recipes_idx = [3, 11, 18, 25, 33]                            # 5 posts
    travel_idx = [1, 4, 7, 12, 15, 18, 22, 26, 28, 30, 33, 38]   # 12 posts

    items_by_name: dict[str, list[FakeMedia]] = {
        "All Posts": list(posts),
        "Inspiration": [posts[i] for i in inspiration_idx],
        "Recipes": [posts[i] for i in recipes_idx],
        "Travel": [posts[i] for i in travel_idx],
    }

    # Sanity: posts in 2+ named collections must be exactly 6.
    membership_counts: dict[str, int] = {}
    for name in ("Inspiration", "Recipes", "Travel"):
        for m in items_by_name[name]:
            membership_counts[m.pk] = membership_counts.get(m.pk, 0) + 1
    multi = [pid for pid, c in membership_counts.items() if c >= 2]
    assert len(multi) == 6, f"expected 6 multi-membership posts, got {len(multi)}"

    return collections, items_by_name


# ---------- the fake client itself -----------------------------------------


class FakeIgClient:
    """Drop-in stand-in for `IgClient` for offline tests + dev."""

    def __init__(self, media_root: Path | None = None) -> None:
        self._media_root = media_root
        self._collections, self._items = _make_fixture(media_root)
        self._removed_post_ids: set[str] = set()
        self._removed_memberships: dict[str, set[str]] = {}

    # ---- session lifecycle (no-ops) ----------------------------------

    def has_settings(self) -> bool:
        return True

    def save_settings(self) -> None:
        pass

    def relogin(self) -> None:
        pass

    # ---- ingest call surface ----------------------------------------

    def list_collections(self) -> list[FakeCollection]:
        # Return only NAMED collections — "All Posts" is enumerated
        # separately by the runner just like in the plan.
        return [c for c in self._collections if c.id != "all_posts"]

    def list_collection_items(self, name: str) -> list[FakeMedia]:
        items = self._items.get(name, [])
        removed_in_col = self._removed_memberships.get(name, set())
        return [
            m for m in items
            if m.pk not in self._removed_post_ids and m.pk not in removed_in_col
        ]

    # ---- legacy slice surface (kept so run.py keeps working) ---------

    def collection_medias_by_name(self, name: str, amount: int = 20) -> list[FakeMedia]:
        return self.list_collection_items(name)[:amount]

    def media_to_dict(self, media: FakeMedia) -> dict[str, Any]:
        return media.dict()

    # ---- test affordances -------------------------------------------

    def remove_posts(self, post_ids: list[str]) -> None:
        """Pretend these posts have been unsaved on IG."""
        self._removed_post_ids.update(post_ids)

    def restore_posts(self, post_ids: list[str]) -> None:
        self._removed_post_ids.difference_update(post_ids)

    def remove_from_collection(self, name: str, post_ids: list[str]) -> None:
        """Pretend these posts have been removed from a single named collection."""
        self._removed_memberships.setdefault(name, set()).update(post_ids)

    def restore_to_collection(self, name: str, post_ids: list[str]) -> None:
        self._removed_memberships.setdefault(name, set()).difference_update(post_ids)


__all__ = [
    "FakeIgClient",
    "FakeMedia",
    "FakeUser",
    "FakeResource",
    "FakeCollection",
]
