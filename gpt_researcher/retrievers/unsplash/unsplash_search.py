"""Unsplash image search for high-quality 4K+ photos.

Uses the Unsplash API (free tier: 50 requests/hour) to search for professional,
beautiful photos related to the research topic. All photos are high-resolution
(up to 4K+), licensed for free use.

Configuration:
    UNSPLASH_ACCESS_KEY: Your Unsplash API access key.
        Get one at https://unsplash.com/developers

Returns raw (highest available), full, and regular resolution URLs so the
downstream pipeline can choose the appropriate size.
"""

import json
import os
import logging
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

UNSPLASH_API_URL = "https://api.unsplash.com/search/photos"


class UnsplashImageSearch:
    """Search Unsplash for high-quality images.

    Attributes:
        access_key: Unsplash API access key.
        quality: Preferred image quality ("raw" = full resolution, "full",
            "regular"). Defaults to "raw".
    """

    def __init__(
        self,
        access_key: Optional[str] = None,
        quality: str = "raw",
    ):
        """Initialize the Unsplash image searcher.

        Args:
            access_key: Unsplash API access key. If None, reads from
                UNSPLASH_ACCESS_KEY environment variable.
            quality: Preferred resolution ("raw", "full", or "regular").
        """
        self.access_key = access_key or os.environ.get("UNSPLASH_ACCESS_KEY", "")
        self.quality = quality

    @property
    def is_configured(self) -> bool:
        """Whether the Unsplash API key is available."""
        return bool(self.access_key)

    def search(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape",
        min_width: int = 1920,
        min_height: int = 1080,
    ) -> list[dict]:
        """Search Unsplash for high-quality images.

        Args:
            query: Search query string.
            per_page: Number of results to return (max 30).
            orientation: "landscape", "portrait", or "squarish".
            min_width: Minimum image width in pixels.
            min_height: Minimum image height in pixels.

        Returns:
            List of image dicts with keys:
                - url: The best available download URL (raw/full/regular).
                - thumbnail: Small preview URL.
                - description: Image description/alt text.
                - author: Photographer name.
                - author_url: Unsplash profile link (for attribution per guidelines).
                - width: Image width in pixels.
                - height: Image height in pixels.
                - score: Quality score (always 7 for Unsplash images).
                - source: Always "unsplash".
        """
        if not self.access_key:
            logger.warning("Unsplash access key not configured. Skipping image search.")
            return []

        headers = {
            "Authorization": f"Client-ID {self.access_key}",
            "Accept-Version": "v1",
        }
        params = {
            "query": query,
            "per_page": min(per_page, 30),
            "orientation": orientation,
        }

        try:
            response = requests.get(
                UNSPLASH_API_URL,
                headers=headers,
                params=params,
                timeout=15,
            )
            if response.status_code == 403:
                logger.warning(
                    "Unsplash API rate limit exceeded (50 req/hour on free tier). "
                    "Skipping image search."
                )
                return []
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Unsplash API request failed: {e}")
            return []

        results = data.get("results", [])
        images = []

        for photo in results:
            # Filter by minimum dimensions
            width = photo.get("width", 0)
            height = photo.get("height", 0)
            if width < min_width or height < min_height:
                continue

            # Choose the best URL based on quality preference
            photo_urls = photo.get("urls", {})
            url = photo_urls.get(self.quality) or photo_urls.get("raw") or photo_urls.get("full") or photo_urls.get("regular")

            if not url:
                continue

            # Build description from photo metadata
            description = (
                photo.get("description")
                or photo.get("alt_description")
                or f"Unsplash photo by {photo.get('user', {}).get('name', 'Unknown')}"
            )
            author_name = photo.get("user", {}).get("name", "Unknown")
            author_url = photo.get("user", {}).get("links", {}).get("html", "")

            images.append({
                "url": url,
                "thumbnail": photo_urls.get("thumb", url),
                "description": description,
                "author": author_name,
                "author_url": author_url,
                "width": width,
                "height": height,
                "score": 7,  # High score so Unsplash images are always preferred
                "source": "unsplash",
            })

        if self.quality == "raw":
            # Raw URLs from Unsplash may append query params; clean them
            for img in images:
                clean_url = img["url"].split("?")[0] if "?" in img["url"] else img["url"]
                img["url"] = clean_url

        logger.info(
            f"Unsplash: found {len(images)} images for query '{query}' "
            f"(requested {per_page})"
        )
        return images


def search_unsplash_images(
    query: str,
    access_key: Optional[str] = None,
    per_page: int = 5,
    quality: str = "raw",
) -> list[dict]:
    """Convenience function to search Unsplash images.

    Args:
        query: Search query.
        access_key: Unsplash API key (reads UNSPLASH_ACCESS_KEY env var if None).
        per_page: Max results (default 5).
        quality: Resolution tier ("raw", "full", "regular").

    Returns:
        List of image dicts as described in UnsplashImageSearch.search().
    """
    searcher = UnsplashImageSearch(access_key=access_key, quality=quality)
    if not searcher.is_configured:
        return []
    return searcher.search(query, per_page=per_page)
