#!/bin/bash
set -e

VENV_PYTHON="/home/azhengya/virtualenv/gpt_researcher/bin/python"
VENV_PIP="/home/azhengya/virtualenv/gpt_researcher/bin/pip"

echo "=== Step 1: 安装 sentence-transformers ==="
$VENV_PIP install sentence-transformers -i https://pypi.org/simple/ --timeout 300

echo ""
echo "=== Step 2: 下载向量模型（约 90MB） ==="
$VENV_PYTHON -c "
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from sentence_transformers import SentenceTransformer
print('正在下载 sentence-transformers/all-MiniLM-L6-v2 ...')
model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
emb = model.encode('hello')
print(f'下载完成，向量维度: {len(emb)}')
"

echo ""
echo "=== 完成 ==="
echo "重启应用即可生效"
