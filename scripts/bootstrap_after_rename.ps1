# =============================================================================
# 工作区重命名后的一键环境恢复脚本
#
# 用途：项目目录从含中文路径改为 ASCII 路径后，重建虚拟环境 + 重建向量库 + 跑评测
#
# 用法（在项目根目录执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_after_rename.ps1
#
# 说明：
#   - .venv 内含旧绝对路径，重命名后必须重建
#   - models/ 下的模型文件不受影响，无需重新下载
#   - data/bm25_index.pkl 与 data/embeddings_cache.npz 保留，可跳过重新编码（省约 3 分钟）
# =============================================================================

$ErrorActionPreference = "Stop"

# 项目根目录 = 本脚本上级目录
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 环境恢复开始" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "项目根目录: $ROOT"
Write-Host ""

# ---- 0. 路径检查：必须是 ASCII ----
if ($ROOT -match '[^\x00-\x7F]') {
    Write-Host "[错误] 项目路径仍包含非 ASCII 字符: $ROOT" -ForegroundColor Red
    Write-Host "       Chroma 的 HNSW 索引无法在此路径下持久化。" -ForegroundColor Red
    Write-Host "       请先把项目目录改成纯英文名，例如 E:\graduation_project" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 路径为纯 ASCII" -ForegroundColor Green

# ---- 1. 关键文件存在性检查 ----
$required = @(
    "requirements.txt",
    "models\qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "models\bge-small-zh-v1.5-onnx\onnx\model.onnx",
    "models\bge-small-zh-v1.5-onnx\tokenizer.json",
    "data\bm25_index.pkl"
)
foreach ($f in $required) {
    if (Test-Path $f) {
        Write-Host "[OK]  $f" -ForegroundColor Green
    } else {
        Write-Host "[缺失] $f" -ForegroundColor Red
    }
}
Write-Host ""

# ---- 2. 重建虚拟环境 ----
Write-Host ">>> 重建虚拟环境 .venv" -ForegroundColor Yellow
if (Test-Path ".venv") {
    Remove-Item -Recurse -Force ".venv"
    Write-Host "    已删除旧 .venv"
}
python -m virtualenv .venv
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 虚拟环境创建失败，请先 pip install virtualenv" -ForegroundColor Red
    exit 1
}
Write-Host ""

# ---- 3. 安装依赖 ----
Write-Host ">>> 安装依赖（llama-cpp-python 用 CPU 预编译 wheel）" -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip -q
& ".\.venv\Scripts\python.exe" -m pip install llama-cpp-python `
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu -q
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt -q
Write-Host "    依赖安装完成"
Write-Host ""

# ---- 4. 重建向量库 ----
Write-Host ">>> 重建 Chroma 向量库" -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" scripts\rebuild_vector_store.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 向量库重建失败，退出码 $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host ""

# ---- 5. 跑检索评测 ----
Write-Host ">>> 运行检索评测" -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" scripts\evaluate_retrieval.py
Write-Host ""

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 环境恢复完成" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "启动后端:" -ForegroundColor White
Write-Host "  .\.venv\Scripts\Activate.ps1 ; python backend\main.py" -ForegroundColor Gray
Write-Host "Swagger: http://localhost:8000/docs" -ForegroundColor Gray