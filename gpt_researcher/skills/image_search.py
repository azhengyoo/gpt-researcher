"""Dedicated image search skill for GPT Researcher.

Searches high-quality image sources (Unsplash, Pexels, Tavily images)
independently of web page scraping, finding professional 4K+ photos
specifically matched to the research topic. This supplements the images
scraped from article pages with much higher quality results.

Supported sources:
    - unsplash: Professional, beautiful photos (needs UNSPLASH_ACCESS_KEY)
    - pexels: Curated stock photos, diverse styles (needs PEXELS_API_KEY)
    - tavily: Web image search using Tavily's image index (needs TAVILY_API_KEY)
"""

import asyncio
import logging
from typing import Optional

from ..retrievers.unsplash.unsplash_search import search_unsplash_images
from ..retrievers.pexels.pexels_search import search_pexels_images

logger = logging.getLogger(__name__)


async def search_quality_images(
    query: str,
    cfg,
    tavily_search_func=None,
) -> list[dict]:
    """Search for high-quality images from configured sources.

    Combines results from multiple image search providers (Unsplash, Tavily)
    based on the IMAGE_SEARCH_SOURCES configuration. Images are returned with
    scores so they naturally outrank scraped images.

    Args:
        query: Search query for finding relevant images.
        cfg: Config object with image search settings.
        tavily_search_func: Optional async function for Tavily image search.
            Signature: async (query: str) -> list[dict]

    Returns:
        List of image dicts with keys: url, description, score, source,
        and optionally author, width, height.
    """
    sources_str = getattr(cfg, 'image_search_sources', '') or ''
    if not sources_str or not sources_str.strip():
        return []

    sources = [s.strip().lower() for s in sources_str.split(',') if s.strip()]
    max_per_source = getattr(cfg, 'image_search_max_images', 5)
    min_width = getattr(cfg, 'image_search_min_width', 1920)
    orientation = getattr(cfg, 'image_search_orientation', 'landscape')

    tasks = []

    if 'unsplash' in sources:
        tasks.append(_search_unsplash(query, max_per_source, min_width, orientation))

    if 'pexels' in sources:
        tasks.append(_search_pexels(query, max_per_source, min_width, orientation))

    if 'tavily' in sources and tavily_search_func:
        tasks.append(_search_tavily_images(query, max_per_source, tavily_search_func))

    if not tasks:
        return []

    # Run all sources in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_images = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Image search source failed: {result}")
            continue
        if result:
            all_images.extend(result)

    # Sort by score descending (Unsplash=7, Tavily=6, both higher than scraped max=5)
    all_images.sort(key=lambda x: x.get('score', 0), reverse=True)

    logger.info(
        f"Image search: found {len(all_images)} high-quality images "
        f"for query '{query[:80]}...'"
    )
    return all_images


# Map config orientation values to Pexels API orientation values
_PEXELS_ORIENTATION_MAP = {
    "landscape": "landscape",
    "portrait": "portrait",
    "all": "",
    "square": "square",
}


async def _search_unsplash(
    query: str,
    per_page: int,
    min_width: int,
    orientation: str,
) -> list[dict]:
    """Run Unsplash image search in a thread pool."""
    return await asyncio.to_thread(
        search_unsplash_images,
        query=query,
        per_page=per_page,
        quality="raw",
    )


async def _search_pexels(
    query: str,
    per_page: int,
    min_width: int,
    orientation: str,
) -> list[dict]:
    """Run Pexels image search in a thread pool."""
    pexels_orientation = _PEXELS_ORIENTATION_MAP.get(orientation, "landscape")
    return await asyncio.to_thread(
        search_pexels_images,
        query=query,
        per_page=per_page,
        orientation=pexels_orientation,
        size="large",
    )


async def _search_tavily_images(
    query: str,
    max_results: int,
    tavily_search_func,
) -> list[dict]:
    """Run Tavily image search."""
    try:
        images = await tavily_search_func(query, max_results)
        return images
    except Exception as e:
        logger.error(f"Tavily image search failed: {e}")
        return []


def merge_research_images(
    web_images: list[dict],
    search_images: list[dict],
    max_total: int = 10,
) -> list[dict]:
    """Merge web-scraped images with dedicated image search results.

    Image search results (Unsplash/Tavily) naturally have higher scores
    (6-7) than scraped images (1-5), so they will appear first after sorting.

    Args:
        web_images: Images scraped from web pages (from get_relevant_images).
        search_images: High-quality images from dedicated search.
        max_total: Maximum total images to keep.

    Returns:
        Combined and deduplicated list sorted by score descending.
    """
    combined = list(web_images)
    seen_urls = {img.get('url', '') for img in web_images if img.get('url')}

    for img in search_images:
        url = img.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            combined.append(img)

    # Sort by score descending
    combined.sort(key=lambda x: x.get('score', 0), reverse=True)

    return combined[:max_total]


def filter_relevant_images(
    images: list[dict],
    queries: list[str],
    min_overlap: int = 1,
) -> list[dict]:
    """Filter and reorder images by relevance to search queries.

    Scores each image based on:
    1. Keyword overlap between image description and search queries
    2. Whether the image's _search_query tag matches a query

    For English queries this removes obvious mismatches. For Chinese
    queries the English descriptions rarely overlap, so we rely on the
    _search_query tag and original API relevance ranking.

    Args:
        images: Image dicts from search_quality_images (may have _search_query).
        queries: Search queries used to find the images.
        min_overlap: Minimum number of shared non-stop tokens required.

    Returns:
        Scored and reordered list, most relevant first. If no image
        passes the filter, the original list is returned as fallback.
    """
    import re

    stop_words = {
        "photo", "by", "pexels", "unsplash", "tavily", "image", "of", "the",
        "a", "an", "in", "on", "at", "and", "or", "with", "from", "for",
        "as", "to", "is", "are", "photography", "photographer", "picture",
        "shot", "illustration", "stock", "curated", "high", "quality",
        "resolution", "hd", "4k", "free", "download",
    }

    has_chinese_query = any(re.search(r'[\u4e00-\u9fff]', q) for q in queries)

    # Build query token sets for each individual query and combined
    query_tokens_all = set()
    query_token_sets = []
    for q in queries:
        tokens = set(re.findall(r'[a-z0-9]+', q.lower())) - stop_words
        query_token_sets.append(tokens)
        query_tokens_all.update(tokens)

    # Score each image
    scored = []
    for img in images:
        desc = (img.get('description', '') or img.get('title', '') or '').lower()
        desc_tokens = set(re.findall(r'[a-z0-9]+', desc)) - stop_words

        # Score 1: Keyword overlap with all query tokens
        overlap_score = len(desc_tokens & query_tokens_all) if query_tokens_all else 0

        # Score 2: _search_query match (image was explicitly returned for a query)
        search_query = img.get('_search_query', '') or ''
        query_match_score = 0
        if search_query:
            sq_lower = search_query.lower()
            for q_tokens in query_token_sets:
                sq_tokens = set(re.findall(r'[a-z0-9]+', sq_lower))
                if q_tokens and sq_tokens:
                    match = len(q_tokens & sq_tokens)
                    query_match_score = max(query_match_score, match)

        # Combined relevance: overlap + 2x query_match (query_match is a stronger signal)
        relevance = overlap_score + (query_match_score * 2)

        # Consider API score too (higher = better resolution/source quality)
        api_score = img.get('score', 0)

        scored.append((relevance, api_score, img))

    # Sort: relevance first (desc), then API quality (desc)
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # For Chinese queries, don't drop images — just reorder.
    # The report writer will use the real descriptions to decide.
    if has_chinese_query:
        return [img for _, _, img in scored]

    # For English queries: return relevant ones first, then fallback
    relevant = [img for rel, _, img in scored if rel >= min_overlap]
    fallback = [img for rel, _, img in scored if rel < min_overlap]
    return relevant + fallback
