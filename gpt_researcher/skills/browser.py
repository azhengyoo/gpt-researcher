"""Browser manager skill for GPT Researcher.

This module provides the BrowserManager class that handles web scraping
and content extraction from URLs.
"""

import logging

from gpt_researcher.utils.workers import WorkerPool

from ..actions.utils import stream_output
from ..actions.web_scraping import scrape_urls
from ..scraper.utils import get_image_hash, normalize_image_url

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages web browsing and content scraping for research.

    This class handles URL scraping, content extraction, and image
    selection during the research process.

    Attributes:
        researcher: The parent GPTResearcher instance.
        worker_pool: Pool of workers for parallel scraping.
    """

    def __init__(self, researcher):
        """Initialize the BrowserManager.

        Args:
            researcher: The GPTResearcher instance that owns this manager.
        """
        logger.info("▶ BrowserManager.__init__ — 初始化浏览器管理器 | 入参: researcher=%s", researcher)
        self.researcher = researcher
        self.worker_pool = WorkerPool(
            researcher.cfg.max_scraper_workers,
            researcher.cfg.scraper_rate_limit_delay
        )

    async def browse_urls(self, urls: list[str]) -> list[dict]:
        """
        Scrape content from a list of URLs.

        Args:
            urls (list[str]): list of URLs to scrape.

        Returns:
            list[dict]: list of scraped content results.
        """
        logger.info("▶ BrowserManager.browse_urls — 浏览多个URL获取内容 | 入参: urls数量=%d", len(urls))
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "scraping_urls",
                f"🌐 Scraping content from {len(urls)} URLs...",
                self.researcher.websocket,
            )

        scraped_content, images = await scrape_urls(
            urls, self.researcher.cfg, self.worker_pool
        )
        self.researcher.add_research_sources(scraped_content)
        new_images = self.select_top_images(images, k=4)  # Select top 4 images
        self.researcher.add_research_images(new_images)

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "scraping_content",
                f"📄 Scraped {len(scraped_content)} pages of content",
                self.researcher.websocket,
            )
            await stream_output(
                "logs",
                "scraping_images",
                f"🖼️ Selected {len(new_images)} new images from {len(images)} total images",
                self.researcher.websocket,
                True,
                new_images,
            )
            await stream_output(
                "logs",
                "scraping_complete",
                f"🌐 Scraping complete",
                self.researcher.websocket,
            )

        return scraped_content

    def select_top_images(self, images: list[dict], k: int = 2) -> list[str]:
        """
        Select most relevant images and remove duplicates based on image content.

        Args:
            images (list[dict]): list of image dictionaries with 'url' and 'score' keys.
            k (int): Number of top images to select if no high-score images are found.

        Returns:
            list[str]: list of selected image URLs.
        """
        logger.info("▶ BrowserManager.select_top_images — 选择最佳图片并去重 | 入参: images数量=%d, k=%d", len(images), k)
        unique_images = []
        seen_keys = set()  # Track both hash and normalized URL
        current_research_images = self.researcher.get_research_images()
        # Pre-compute normalized URLs for existing images
        existing_norm_urls = {normalize_image_url(u) for u in current_research_images}

        # Process images in descending order of their scores
        for img in sorted(images, key=lambda im: im["score"], reverse=True):
            img_url = img['url']
            norm_url = normalize_image_url(img_url)
            img_hash = get_image_hash(img_url)

            # Dedup: check by normalized URL first (catches http/https, param differences)
            if norm_url in seen_keys or norm_url in existing_norm_urls:
                continue

            # Also check by hash (catches different filenames for same image)
            if img_hash and img_hash in seen_keys:
                continue

            if img_hash:
                seen_keys.add(img_hash)
            seen_keys.add(norm_url)
            seen_keys.add(img_url)  # Also track raw URL

            unique_images.append(img_url)

            if len(unique_images) == k:
                break

        return unique_images
