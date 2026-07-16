"""Pexels image search for high-quality curated photos.

Pexels provides free, high-quality stock photos hand-picked from user uploads
and external sources. Like Unsplash, all photos are free to use under the
Pexels license.

Configuration:
    PEXELS_API_KEY: Your Pexels API key.
        Get one at https://www.pexels.com/api/

Rate limits (free tier):
    - 200 requests/hour
    - 10,000 requests/month
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PEXELS_API_URL = "https://api.pexels.com/v1/search"


class PexelsImageSearch:
    """Search Pexels for high-quality images.

    Attributes:
        api_key: Pexels API key.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Pexels image searcher.

        Args:
            api_key: Pexels API key. If None, reads from PEXELS_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY", "")

    @property
    def is_configured(self) -> bool:
        """Whether the Pexels API key is available."""
        return bool(self.api_key)

    def search(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape",
        size: str = "large",
        locale: str = "",
        min_width: int = 1920,
        min_height: int = 1080,
    ) -> list[dict]:
        """Search Pexels for high-quality images.

        Args:
            query: Search query string.
            per_page: Number of results (max 80, default 15 from API).
            orientation: "landscape", "portrait", or "square".
            size: "large" (≥2MP), "medium" (≥0.5MP), or "small" for specific
                quality tiers.
            locale: Locale for localized results (e.g. "zh-CN", "ja-JP").
            min_width: Minimum image width in pixels.
            min_height: Minimum image height in pixels.

        Returns:
            List of image dicts with keys:
                - url: The highest available download URL (original).
                - thumbnail: Small preview URL.
                - description: Image alt/description text.
                - author: Photographer name.
                - author_url: Pexels photographer profile link.
                - width: Image width in pixels.
                - height: Image height in pixels.
                - score: Quality score (always 7 for Pexels — same as Unsplash).
                - source: Always "pexels".
        """
        if not self.api_key:
            logger.warning("Pexels API key not configured. Skipping image search.")
            return []

        headers = {
            "Authorization": self.api_key,
        }
        params = {
            "query": query,
            "per_page": min(per_page, 80),
            "orientation": orientation,
            "size": size,
        }
        if locale:
            params["locale"] = locale

        try:
            response = requests.get(
                PEXELS_API_URL,
                headers=headers,
                params=params,
                timeout=15,
            )
            if response.status_code == 429:
                logger.warning(
                    "Pexels API rate limit exceeded (200 req/hour on free tier). "
                    "Skipping image search."
                )
                return []
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Pexels API request failed: {e}")
            return []

        photos = data.get("photos", [])
        images = []

        for photo in photos:
            width = photo.get("width", 0)
            height = photo.get("height", 0)
            if width < min_width or height < min_height:
                continue

            # Pexels provides original and several sized-down versions
            src = photo.get("src", {})
            # Original is the highest quality
            url = src.get("original") or src.get("large2x") or src.get("large") or src.get("medium")
            if not url:
                continue

            description = photo.get("alt") or ""
            author_name = photo.get("photographer", "Unknown")
            author_url = photo.get("photographer_url", "")

            # Build a richer description when alt text is generic
            if not description or description == photo.get("photographer"):
                avg_color = photo.get("avg_color", "")
                photo_url = photo.get("url", "")
                parts = [f"Pexels photo by {author_name}"]
                if avg_color:
                    parts.append(f"(dominant color: {avg_color})")
                description = " | ".join(parts)

            images.append({
                "url": url,
                "thumbnail": src.get("tiny", src.get("small", url)),
                "description": description,
                "author": author_name,
                "author_url": author_url,
                "width": width,
                "height": height,
                "score": 7,  # Same tier as Unsplash — high-quality curated photos
                "source": "pexels",
            })

        logger.info(
            f"Pexels: found {len(images)} images for query '{query}' "
            f"(requested {per_page})"
        )
        return images


def search_pexels_images(
    query: str,
    api_key: Optional[str] = None,
    per_page: int = 5,
    orientation: str = "landscape",
    size: str = "large",
    locale: str = "",
) -> list[dict]:
    """Convenience function to search Pexels images.

    Args:
        query: Search query.
        api_key: Pexels API key (reads PEXELS_API_KEY env var if None).
        per_page: Max results (default 5).
        orientation: "landscape", "portrait", or "square".
        size: "large", "medium", or "small".
        locale: Locale code (e.g. "zh-CN").

    Returns:
        List of image dicts as described in PexelsImageSearch.search().
    """
    searcher = PexelsImageSearch(api_key=api_key)
    if not searcher.is_configured:
        return []
    return searcher.search(query, per_page=per_page, orientation=orientation, size=size, locale=locale)
