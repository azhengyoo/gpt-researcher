"""Unsplash image search retriever for GPT Researcher.

Provides high-quality, professional, 4K+ resolution images via the Unsplash API.
"""

from .unsplash_search import UnsplashImageSearch, search_unsplash_images

__all__ = ["UnsplashImageSearch", "search_unsplash_images"]
