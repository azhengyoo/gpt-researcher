import aiofiles
import urllib
import mistune
import os

async def write_to_file(filename: str, text: str) -> None:
    """Asynchronously write text to a file in UTF-8 encoding.

    Args:
        filename (str): The filename to write to.
        text (str): The text to write.
    """
    # Ensure text is a string
    if not isinstance(text, str):
        text = str(text)

    # Convert text to UTF-8, replacing any problematic characters
    text_utf8 = text.encode('utf-8', errors='replace').decode('utf-8')

    async with aiofiles.open(filename, "w", encoding='utf-8') as file:
        await file.write(text_utf8)

async def write_text_to_md(text: str, filename: str = "") -> str:
    """Writes text to a Markdown file and returns the file path.

    Args:
        text (str): Text to write to the Markdown file.

    Returns:
        str: The file path of the generated Markdown file.
    """
    import uuid

    safe_name = (filename or "").strip()[:60] or f"report-{uuid.uuid4().hex[:12]}"
    safe_name = safe_name.replace("/", "-").replace("\\", "-")
    os.makedirs("outputs", exist_ok=True)
    file_path = f"outputs/{safe_name}.md"
    await write_to_file(file_path, text)
    return urllib.parse.quote(file_path)

def _preprocess_images_for_pdf(text: str) -> str:
    """Convert web image URLs to absolute file paths for PDF generation.
    
    Transforms /outputs/images/... URLs to absolute file:// paths that
    weasyprint can resolve.
    """
    import re
    
    base_path = os.path.abspath(".")
    
    # Pattern to find markdown images with /outputs/ URLs
    def replace_image_url(match):
        alt_text = match.group(1)
        url = match.group(2)
        
        # Convert /outputs/... to absolute path
        if url.startswith("/outputs/"):
            abs_path = os.path.join(base_path, url.lstrip("/"))
            return f"![{alt_text}]({abs_path})"
        return match.group(0)
    
    # Match ![alt text](/outputs/images/...)
    pattern = r'!\[([^\]]*)\]\((/outputs/[^)]+)\)'
    return re.sub(pattern, replace_image_url, text)


async def write_md_to_pdf(text: str, filename: str = "") -> str:
    """Converts Markdown text to a PDF file and returns the file path.

    Args:
        text (str): Markdown text to convert.

    Returns:
        str: The encoded file path of the generated PDF.
    """
    import uuid

    # Empty / whitespace-only filename previously wrote "outputs/.pdf" which
    # confuses download UIs (#1718). Prefer a stable non-empty basename.
    safe_name = (filename or "").strip()[:60] or f"report-{uuid.uuid4().hex[:12]}"
    # Replace path separators to keep the PDF under outputs/.
    safe_name = safe_name.replace("/", "-").replace("\\", "-")
    os.makedirs("outputs", exist_ok=True)
    file_path = f"outputs/{safe_name}.pdf"

    try:
        # Resolve css path relative to this backend module to avoid
        # dependency on the current working directory.
        current_dir = os.path.dirname(os.path.abspath(__file__))
        css_path = os.path.join(current_dir, "styles", "pdf_styles.css")
        
        # Preprocess image URLs for PDF compatibility
        processed_text = _preprocess_images_for_pdf(text)
        
        # Set base_url to current directory for resolving any remaining relative paths
        base_url = os.path.abspath(".")
        from md2pdf.core import md2pdf
        md2pdf(
               file_path,
               raw=processed_text,
               css=css_path,
               base_url=base_url,
            )
        print(f"Report written to {file_path}")
    except Exception as e:
        print(f"WeasyPrint failed (GTK may not be installed): {e}")
        print("Falling back to Playwright for PDF generation...")
        try:
            _write_md_to_pdf_playwright(text, css_path, file_path)
        except Exception as e2:
            print(f"Playwright PDF fallback also failed: {e2}")
            return ""

    encoded_file_path = urllib.parse.quote(file_path)
    return encoded_file_path


def _write_md_to_pdf_playwright(text: str, css_path: str, file_path: str) -> None:
    """Fallback PDF generation using Playwright (cross-platform, no GTK needed).

    Converts markdown → HTML, then uses headless Chromium to render to PDF.
    """
    import tempfile

    # Convert markdown to HTML
    html_body = mistune.html(text)

    # Read CSS content
    css_content = ""
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            css_content = f.read()

    # Build a complete HTML page
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
{css_content}
body {{ font-family: "Microsoft YaHei", "SimSun", Arial, sans-serif; padding: 40px; max-width: 900px; margin: 0 auto; line-height: 1.8; }}
img {{ max-width: 100%; }}
pre {{ background: #f4f4f4; padding: 12px; border-radius: 4px; overflow-x: auto; }}
code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #f2f2f2; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Write HTML to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        temp_html_path = f.name

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file:///{temp_html_path}", wait_until="networkidle")
            page.pdf(path=file_path, format="A4", print_background=True)
            browser.close()
        print(f"Report written to {file_path}")
    finally:
        os.unlink(temp_html_path)

async def write_md_to_word(text: str, filename: str = "") -> str:
    """Converts Markdown text to a DOCX file and returns the file path.

    Args:
        text (str): Markdown text to convert.

    Returns:
        str: The encoded file path of the generated DOCX.
    """
    import uuid

    safe_name = (filename or "").strip()[:60] or f"report-{uuid.uuid4().hex[:12]}"
    safe_name = safe_name.replace("/", "-").replace("\\", "-")
    os.makedirs("outputs", exist_ok=True)
    file_path = f"outputs/{safe_name}.docx"

    try:
        from docx import Document
        from htmldocx import HtmlToDocx
        # Convert report markdown to HTML
        html = mistune.html(text)
        # Create a document object
        doc = Document()
        # Convert the html generated from the report to document format
        HtmlToDocx().add_html_to_document(html, doc)

        # Saving the docx document to file_path
        doc.save(file_path)

        print(f"Report written to {file_path}")

        encoded_file_path = urllib.parse.quote(file_path)
        return encoded_file_path

    except Exception as e:
        print(f"Error in converting Markdown to DOCX: {e}")
        return ""