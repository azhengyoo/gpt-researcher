from dotenv import load_dotenv
import logging
import sys
from pathlib import Path

from gpt_researcher.utils.logging_config import RelativePathFormatter

# Create logs directory if it doesn't exist
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure logging with UTF-8 encoding for Windows compatibility
_STREAM_FMT = '%(asctime)s - %(relative_path)s:%(lineno)d - %(levelname)s - %(message)s'
_FILE_FMT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

stream_handler = logging.StreamHandler(
    open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
)
stream_handler.setFormatter(RelativePathFormatter(_STREAM_FMT))

file_handler = logging.FileHandler('logs/app.log', encoding='utf-8')
file_handler.setFormatter(logging.Formatter(_FILE_FMT))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler],
)

# Suppress verbose fontTools logging
logging.getLogger('fontTools').setLevel(logging.WARNING)
logging.getLogger('fontTools.subset').setLevel(logging.WARNING)
logging.getLogger('fontTools.ttLib').setLevel(logging.WARNING)

# Create logger instance
logger = logging.getLogger(__name__)

load_dotenv()

# 设置 HuggingFace 国内镜像，解决模型下载被墙问题
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from backend.server.app import app

if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
