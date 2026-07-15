import os
import re
import aiohttp
import tempfile
from bs4 import BeautifulSoup
from langchain_community.document_loaders import (
    PyMuPDFLoader,
    TextLoader,
    UnstructuredCSVLoader,
    UnstructuredExcelLoader,
    UnstructuredMarkdownLoader,
    UnstructuredPowerPointLoader,
    UnstructuredWordDocumentLoader
)

# Document file extensions handled via LangChain loaders
_DOC_EXTENSIONS = {'pdf', 'doc', 'docx', 'pptx', 'csv', 'xls', 'xlsx', 'md', 'txt'}


class OnlineDocumentLoader:

    def __init__(self, urls):
        self.urls = urls
        self.failed_urls = {}  # {url: error_message}

    async def load(self) -> list:
        docs = []
        for url in self.urls:
            pages = await self._download_and_process(url)
            for page in pages:
                # Handle LangChain Document objects (from _load_document)
                if hasattr(page, 'page_content'):
                    raw_content = page.page_content
                    source_url = page.metadata.get('source', url) if hasattr(page, 'metadata') else url
                # Handle dict format (from _scrape_webpage)
                elif isinstance(page, dict):
                    raw_content = page.get('raw_content', '')
                    source_url = page.get('url', url)
                else:
                    continue

                if raw_content:
                    docs.append({
                        "raw_content": raw_content,
                        "url": source_url
                    })

        return docs

    async def _download_and_process(self, url: str) -> list:
        """Route URL to document downloader or webpage scraper based on extension."""
        ext = self._get_extension(url).strip('.').lower()

        if ext in _DOC_EXTENSIONS:
            return await self._download_document(url, ext)
        else:
            # Web pages: .html, .htm, .php, no extension, etc.
            return await self._scrape_webpage(url)

    async def _download_document(self, url: str, ext: str) -> list:
        """Download and parse a document file (PDF, DOCX, etc.)."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=6) as response:
                    if response.status != 200:
                        error_msg = f"HTTP {response.status}"
                        print(f"Failed to download {url}: {error_msg}")
                        self.failed_urls[url] = f"地址返回状态码 {response.status}，可能不存在或无法访问"
                        return []

                    content = await response.read()
                    suffix = self._get_extension(url)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                        tmp_file.write(content)
                        tmp_file_path = tmp_file.name

                    return await self._load_document(tmp_file_path, ext)
        except aiohttp.ClientError as e:
            error_msg = str(e)
            print(f"Failed to process {url}: {error_msg}")
            self.failed_urls[url] = f"无法连接到该地址：{error_msg}"
            return []
        except Exception as e:
            error_msg = str(e)
            print(f"Unexpected error processing {url}: {error_msg}")
            self.failed_urls[url] = f"处理该文档时出错：{error_msg}"
            return []

    async def _scrape_webpage(self, url: str) -> list:
        """Scrape a regular web page and extract readable text content."""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as response:
                    if response.status != 200:
                        self.failed_urls[url] = f"地址返回状态码 {response.status}，该网页不存在或无法访问"
                        return []

                    content_type = response.headers.get('Content-Type', '').lower()
                    # Skip binary/non-html responses
                    if 'text/html' not in content_type and 'text/plain' not in content_type:
                        self.failed_urls[url] = f"该地址返回的不是网页内容（Content-Type: {content_type}）"
                        return []

                    html = await response.text(encoding='utf-8', errors='replace')
        except aiohttp.ClientError as e:
            self.failed_urls[url] = f"无法连接到该地址：{e}"
            return []
        except Exception as e:
            self.failed_urls[url] = f"访问该网页时出错：{e}"
            return []

        if not html or len(html.strip()) < 100:
            self.failed_urls[url] = "该网页返回内容为空或过短"
            return []

        # Parse HTML and extract readable text
        content = self._extract_webpage_text(html)
        if not content or len(content.strip()) < 20:
            self.failed_urls[url] = "未能从该网页提取到有效文本内容（页面可能为纯JS渲染或内容过少，建议提供包含正文内容的文章/文档页面URL）"
            return []

        return [{"raw_content": content, "url": url}]

    def _extract_webpage_text(self, html: str) -> str:
        """Parse HTML with BeautifulSoup and extract clean text."""
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            soup = BeautifulSoup(html, 'html.parser')

        # Remove unwanted tags
        for tag_name in ['script', 'style', 'footer', 'header', 'nav', 'menu', 'sidebar', 'svg', 'noscript']:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove elements with disallowed classes
        disallowed_classes = {'nav', 'menu', 'sidebar', 'footer', 'header'}
        for tag in soup.find_all(class_=True):
            tag_classes = set(tag.get('class', []))
            if tag_classes & disallowed_classes:
                tag.decompose()

        # Extract text
        text = soup.get_text(separator='\n', strip=True)
        # Remove excess whitespace
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    async def _load_document(self, file_path: str, file_extension: str) -> list:
        """Parse a downloaded document file using LangChain loaders."""
        ret_data = []
        try:
            loader_dict = {
                "pdf": PyMuPDFLoader(file_path),
                "txt": TextLoader(file_path),
                "doc": UnstructuredWordDocumentLoader(file_path),
                "docx": UnstructuredWordDocumentLoader(file_path),
                "pptx": UnstructuredPowerPointLoader(file_path),
                "csv": UnstructuredCSVLoader(file_path, mode="elements"),
                "xls": UnstructuredExcelLoader(file_path, mode="elements"),
                "xlsx": UnstructuredExcelLoader(file_path, mode="elements"),
                "md": UnstructuredMarkdownLoader(file_path)
            }

            loader = loader_dict.get(file_extension, None)
            if loader:
                ret_data = loader.load()

        except Exception as e:
            print(f"Failed to load document : {file_path}")
            print(e)
        finally:
            try:
                os.remove(file_path)  # 删除临时文件
            except OSError:
                pass

        return ret_data

    @staticmethod
    def _get_extension(url: str) -> str:
        # Lower-case the extension so loader lookup (whose keys are lower-case,
        # e.g. "pdf"/"docx") matches URLs that use upper-case extensions like
        # "report.PDF" or "doc.DOCX". The leading "?" split drops query strings
        # (signed CDN/S3 URLs) before extracting the suffix.
        return os.path.splitext(url.split("?")[0])[1].lower()
