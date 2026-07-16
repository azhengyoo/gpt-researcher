"""Utility functions for web scraping.

This module provides helper functions for extracting content, images,
and processing HTML from web pages.
"""

import hashlib
import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

import bs4
from bs4 import BeautifulSoup


def _try_resolve_full_res_url(img_url: str) -> str:
    """Attempt to resolve a thumbnail URL to its full-resolution version.

    Handles common CMS/WordPress patterns like:
    - image-150x150.jpg -> image.jpg
    - image_300x200.png -> image.png
    - image-thumb.jpg -> image.jpg
    - /w=200,h=150/ -> /w=1200,h=900/  (CDN resize params)
    """
    if not img_url:
        return img_url

    # Pattern 1: WordPress-style dimensions: name-WxH.ext or name-WxH-crop.ext
    # e.g., image-150x150.jpg -> image.jpg
    size_pattern = re.compile(
        r'([\-_]\d{1,6}x\d{1,6})([\-_][a-z]+)?\.(jpg|jpeg|png|webp|gif)$',
        re.IGNORECASE,
    )
    resolved = size_pattern.sub(r'.\3', img_url)
    if resolved != img_url:
        return resolved

    # Pattern 2: CDN resize parameters in path or query
    # e.g., /images/w=200,h=150/sample.jpg -> /images/w=1200,h=900/sample.jpg
    cdn_size_pattern = re.compile(r'/w=\d{1,6},h=\d{1,6}/')
    if cdn_size_pattern.search(img_url):
        return cdn_size_pattern.sub('/w=1200,h=900/', img_url)

    # Pattern 3: URL query params for resize (common on image CDNs)
    # Remove width/height query params but keep others
    parsed = urlparse(img_url)
    if parsed.query:
        # Remove common resize params
        qs_cleaned = re.sub(r'[&?](w|width|h|height|size|resize)=\d{1,6}', '', parsed.query)
        qs_cleaned = qs_cleaned.lstrip('&')
        if qs_cleaned != parsed.query:
            new_query = f'?{qs_cleaned}' if qs_cleaned else ''
            return f'{parsed.scheme}://{parsed.netloc}{parsed.path}{new_query}'

    return img_url


def _parse_srcset(srcset_str: str, base_url: str) -> list[tuple[str, int]]:
    """Parse srcset attribute and return list of (url, width_descriptor) tuples."""
    candidates = []
    if not srcset_str:
        return candidates

    parts = srcset_str.split(',')
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Format: "url 1200w" or "url 2x"
        tokens = part.rsplit(None, 1)
        if len(tokens) == 2:
            img_url, descriptor = tokens
            img_url = img_url.strip()
            descriptor = descriptor.strip().lower()
            if descriptor.endswith('w'):
                try:
                    width = int(descriptor[:-1])
                    candidates.append((urljoin(base_url, img_url), width))
                except ValueError:
                    candidates.append((urljoin(base_url, img_url), 0))
            elif descriptor.endswith('x'):
                try:
                    multiplier = float(descriptor[:-1])
                    candidates.append((urljoin(base_url, img_url), int(multiplier * 100)))
                except ValueError:
                    candidates.append((urljoin(base_url, img_url), 0))
        else:
            candidates.append((urljoin(base_url, part), 0))
    return candidates


def _is_low_quality_class(class_list: list[str]) -> bool:
    """Check if classes indicate a low-quality/thumbnail image."""
    if not class_list:
        return False
    low_quality_classes = {
        'thumbnail', 'thumb', 'icon', 'avatar', 'logo', 'favicon',
        'small', 'mini', 'tiny', 'micro', 'badge', 'emoji',
        'placeholder', 'lazy', 'spacer', 'widget', 'sidebar-img',
        'footer-img', 'header-logo', 'site-logo', 'profile-pic',
        'preview', 'screenshot-small',
    }
    return bool(low_quality_classes & set(cls.lower() for cls in class_list))


def _is_content_class(class_list: list[str]) -> bool:
    """Check if classes indicate a content/main image."""
    if not class_list:
        return False
    content_classes = {
        'featured', 'hero', 'main', 'content', 'article', 'post',
        'cover', 'full', 'large', 'banner', 'header-img', 'figure',
        'wp-post-image', 'attachment', 'size-full', 'size-large',
    }
    return bool(content_classes & set(cls.lower() for cls in class_list))


def _estimate_score_from_url(img_url: str) -> int:
    """Estimate image quality from URL patterns."""
    url_lower = img_url.lower()
    # High-quality CDNs / patterns
    hq_patterns = [
        'unsplash', 'pexels', 'pixabay', 'cdn.', 'images.unsplash',
        'cloudinary', 'images.ctfassets.net', 'imgix', 'staticflickr',
        'images.pexels', 'cdn-images', 'wp-content/uploads',
        'media.gettyimages', 'images.nationalgeographic', 'images.unsplash.com',
    ]
    lq_patterns = ['thumbnail', 'thumb', '-150x', '-300x', '-80x80', 'icon', 'avatar',
                   'pixel', 'placehold', 'svg', 'logo', 'favicon', '-50x50', '-100x100']

    # Check for full-size WordPress image (no dimension suffix in filename)
    # Good: image.jpg, image-1024x768.jpg
    # Bad: image-150x150.jpg, image-thumb.jpg

    score = 1  # Default base

    for pattern in hq_patterns:
        if pattern in url_lower:
            score += 2
            break

    for pattern in lq_patterns:
        if pattern in url_lower:
            score -= 2
            break

    # Prefer larger dimension suffixes in URLs
    size_match = re.search(r'[-_](\d{3,})x(\d{3,})', url_lower)
    if size_match:
        w = int(size_match.group(1))
        h = int(size_match.group(2))
        if w >= 2560 and h >= 1440:
            score += 3  # 2K+
        elif w >= 1920 and h >= 1080:
            score += 2  # Full HD+
        elif w >= 1200 and h >= 800:
            score += 1

    return score


def normalize_image_url(img_url: str) -> str:
    """Normalize an image URL for deduplication purposes.

    Strips protocol differences, trailing slashes, common tracking/fingerprinting
    query parameters, and fragment identifiers so that essentially the same image
    referenced via slightly different URLs is recognized as a duplicate.

    Args:
        img_url: Raw image URL.

    Returns:
        Normalized URL string suitable for dedup key comparison.
    """
    if not img_url:
        return ""

    parsed = urlparse(img_url)

    # Normalize protocol: prefer https
    scheme = 'https' if parsed.scheme in ('http', 'https') else parsed.scheme

    # Normalize hostname: lowercase, remove leading 'www.'
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]

    # Normalize path: remove trailing slash unless it's just "/"
    path = parsed.path.rstrip('/') or '/'

    # Remove fragment entirely
    fragment = ''

    # Keep only essential query params; strip tracking/resize/utm params
    if parsed.query:
        strip_params = {
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
            'ref', 'source', 'fbclid', 'gclid', '_ga', 'w', 'h', 'width', 'height',
            'size', 'resize', 'quality', 'q', 'ts', 'v', 'ver', 'version',
            'token', '_t', 'nocache', 't', 'rnd', 'rand',
        }
        params = parse_qs(parsed.query, keep_blank_values=False)
        cleaned = {k: v for k, v in params.items() if k.lower() not in strip_params}
        if cleaned:
            # Sort for consistent ordering
            query = '&'.join(f'{k}={v[0]}' for k, v in sorted(cleaned.items()))
        else:
            query = ''
    else:
        query = ''

    normalized = f'{scheme}://{netloc}{path}'
    if query:
        normalized += f'?{query}'

    return normalized


def get_relevant_images(soup: BeautifulSoup, url: str) -> list:
    """Extract relevant, high-quality images from the page.

    Prioritizes high-resolution images by checking srcset, picture elements,
    lazy-load attributes, and HTML dimension attributes. Filters out small
    thumbnails and attempts to resolve thumbnail URLs to full-size versions.
    """
    image_urls = []
    seen_urls = set()

    try:
        # --- Step 1: Collect from <picture> elements (highest quality first) ---
        for picture in soup.find_all('picture'):
            for source in picture.find_all('source', srcset=True):
                candidates = _parse_srcset(source.get('srcset', ''), url)
                for src_url, width_desc in candidates:
                    if src_url.startswith(('http://', 'https://')) and src_url not in seen_urls:
                        if width_desc >= 2560:
                            score = 5
                        elif width_desc >= 1920:
                            score = 4
                        elif width_desc >= 1200:
                            score = 3
                        else:
                            score = 2
                        image_urls.append({'url': src_url, 'score': score})
                        seen_urls.add(src_url)

        # --- Step 2: Collect from all <img> tags ---
        all_images = soup.find_all('img')

        for img in all_images:
            img_classes = img.get('class', [])

            # Skip obvious low-quality / decorative images
            if _is_low_quality_class(img_classes):
                continue

            # Try to get the best available URL: srcset > data-src > data-original > src
            best_url = None
            base_score = 0

            # Check srcset (responsive images - usually highest quality)
            srcset_str = img.get('srcset', '') or img.get('data-srcset', '')
            if srcset_str:
                candidates = _parse_srcset(srcset_str, url)
                if candidates:
                    # Pick the candidate with the highest width descriptor
                    best_candidate = max(candidates, key=lambda x: x[1])
                    best_url = best_candidate[0]
                    w = best_candidate[1]
                    if w >= 2560:
                        base_score = 5  # 2K+, excellent
                    elif w >= 1920:
                        base_score = 4  # Full HD+, very good
                    elif w >= 1200:
                        base_score = 3
                    elif w >= 800:
                        base_score = 2
                    else:
                        base_score = 1

            # Fallback: check lazy-load attributes
            if not best_url:
                for attr in ['data-src', 'data-lazy-src', 'data-original', 'data-large',
                             'data-full', 'data-src-large', 'data-zoom']:
                    val = img.get(attr, '')
                    if val:
                        candidate = urljoin(url, val)
                        if candidate.startswith(('http://', 'https://')):
                            best_url = candidate
                            base_score = 2  # Lazy-load images are usually content images
                            break

            # Final fallback: standard src attribute
            if not best_url:
                src_val = img.get('src', '')
                if src_val:
                    best_url = urljoin(url, src_val)

            if not best_url or not best_url.startswith(('http://', 'https://')):
                continue

            # Skip duplicates
            if best_url in seen_urls:
                continue
            seen_urls.add(best_url)

            # --- Step 3: Try to resolve to full-resolution version ---
            resolved_url = _try_resolve_full_res_url(best_url)
            if resolved_url != best_url and resolved_url not in seen_urls:
                # Use the full-res URL instead
                seen_urls.add(resolved_url)
                best_url = resolved_url

            # --- Step 4: Scoring ---

            # 4a: Check if class indicates a content image
            if _is_content_class(img_classes):
                base_score = max(base_score, 5)

            # 4b: Check explicit width/height attributes
            if img.get('width') and img.get('height'):
                width = parse_dimension(img['width'])
                height = parse_dimension(img['height'])
                if width and height:
                    if width >= 2560 and height >= 1440:
                        base_score = max(base_score, 5)  # 2K+
                    elif width >= 1920 and height >= 1080:
                        base_score = max(base_score, 4)  # Full HD+
                    elif width >= 1200 or height >= 800:
                        base_score = max(base_score, 3)
                    elif width >= 800 or height >= 500:
                        base_score = max(base_score, 2)
                    elif width >= 500 or height >= 300:
                        base_score = max(base_score, 1)
                    else:
                        continue  # Too small, skip entirely

            # 4c: Check CSS inline styles for large sizes
            style = img.get('style', '')
            if style:
                style_w = re.search(r'width\s*:\s*(\d+)px', style)
                style_h = re.search(r'height\s*:\s*(\d+)px', style)
                if style_w and style_h:
                    w = int(style_w.group(1))
                    h = int(style_h.group(1))
                    if w >= 1200 and h >= 800:
                        base_score = max(base_score, 4)

            # 4d: Check if image is within <figure> or <article> (good semantic indicator)
            parent_figure = img.find_parent('figure')
            parent_article = img.find_parent('article')
            if parent_figure or parent_article:
                base_score = max(base_score, 2)

            # 4e: Estimate from URL if no score yet
            if base_score <= 1:
                base_score = _estimate_score_from_url(best_url)

            image_urls.append({'url': best_url, 'score': base_score})

        # --- Step 5: Sort by score descending ---
        sorted_images = sorted(image_urls, key=lambda x: x['score'], reverse=True)

        # Return top 8 images (quality over quantity)
        return sorted_images[:8]

    except Exception as e:
        logging.error(f"Error in get_relevant_images: {e}")
        return []

def parse_dimension(value: str) -> int:
    """Parse dimension value, handling px units"""
    if value.lower().endswith('px'):
        value = value[:-2]  # Remove 'px' suffix
    try:
        # Convert to float first to handle decimal values like '409.12'
        return int(float(value))
    except (ValueError, TypeError) as e:
        # Non-numeric dimensions (e.g. '100%', 'auto', '50em') are common and
        # expected on real pages; log at debug level instead of spamming stdout.
        logging.debug("Could not parse dimension value %r: %s", value, e)
        return None

def extract_title(soup: BeautifulSoup) -> str:
    """Extract the title text from the BeautifulSoup object.

    Always returns a string. An empty ``<title></title>`` yields ``""`` (not
    ``None``), and a title containing nested markup (e.g.
    ``<title><span>x</span></title>``) yields its text content rather than the
    raw inner HTML.
    """
    title_tag = soup.title
    if not title_tag:
        return ""
    return title_tag.get_text(strip=True)

def get_image_hash(image_url: str) -> str:
    """Calculate a simple hash based on the image filename and essential query parameters"""
    try:
        parsed_url = urlparse(image_url)
        
        # Extract the filename
        filename = parsed_url.path.split('/')[-1]
        
        # Extract essential query parameters (e.g., 'url' for CDN-served images)
        query_params = parse_qs(parsed_url.query)
        essential_params = query_params.get('url', [])
        
        # Combine filename and essential parameters
        image_identifier = filename + ''.join(essential_params)
        
        # Calculate hash
        return hashlib.md5(image_identifier.encode()).hexdigest()
    except Exception as e:
        logging.error(f"Error calculating image hash for {image_url}: {e}")
        return None


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Clean the soup by removing unwanted tags"""
    for tag in soup.find_all(
        [
            "script",
            "style",
            "footer",
            "header",
            "nav",
            "menu",
            "sidebar",
            "svg",
        ]
    ):
        tag.decompose()

    disallowed_class_set = {"nav", "menu", "sidebar", "footer"}

    # clean tags with certain classes
    def does_tag_have_disallowed_class(elem) -> bool:
        if not isinstance(elem, bs4.Tag):
            return False

        return any(
            cls_name in disallowed_class_set for cls_name in elem.get("class", [])
        )

    for tag in soup.find_all(does_tag_have_disallowed_class):
        tag.decompose()

    return soup


def get_text_from_soup(soup: BeautifulSoup) -> str:
    """Get the relevant text from the soup with improved filtering"""
    text = soup.get_text(strip=True, separator="\n")
    # Remove excess whitespace
    text = re.sub(r"\s{2,}", " ", text)
    return text