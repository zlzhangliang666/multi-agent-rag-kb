@echo off
setlocal
title RAG 知识库问答系统 - 一键启动
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   多 Agent 协同 RAG 知识库问答系统   一键启动
echo ============================================================
echo   项目目录 : %ROOT%
echo.

REM ---------- 1. 后端虚拟环境 ----------
if exist "%ROOT%\.venv\Scripts\python.exe" goto venv_ok
echo [错误] 未找到 Python 虚拟环境 .venv\Scripts\python.exe
echo        请先在项目目录执行:
echo          python -m venv .venv
echo          .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
:venv_ok

REM ---------- 2. Node.js ----------
set "SKIP_FE="
where node >nul 2>nul
if not errorlevel 1 goto node_ok
echo [警告] 未找到 Node.js，将只启动后端。前端需 Node.js 18 或更高版本。
echo.
set "SKIP_FE=1"
:node_ok

REM ---------- 3. 模型文件 ----------
set "MODEL_MISS="
if not exist "%ROOT%\models\qwen2.5-1.5b-instruct-q4_k_m.gguf" set "MODEL_MISS=1"
if not exist "%ROOT%\models\bge-small-zh-v1.5-onnx\onnx\model.onnx" set "MODEL_MISS=1"
if not defined MODEL_MISS goto model_ok
echo [警告] models 目录下模型文件不完整，后端仍可启动，但无法生成回答。
echo        请参考 README.md 的「模型下载」补齐。
echo.
:model_ok

REM ---------- 4. 知识库数据 ----------
if exist "%ROOT%\data\chroma_db" goto data_ok
echo [警告] 未找到知识库数据 data\chroma_db
echo        首次使用请先重建: python scripts\rebuild_all.py --yes
echo.
:data_ok

REM ---------- 5. 端口占用检查 ----------
netstat -ano | findstr "LISTENING" | findstr ":8000" >nul
if errorlevel 1 goto port8000_free
echo [警告] 端口 8000 已被占用，后端可能已在运行。
echo.
:port8000_free
netstat -ano | findstr "LISTENING" | findstr ":5173" >nul
if errorlevel 1 goto port5173_free
echo [警告] 端口 5173 已被占用，前端可能已在运行。
echo.
:port5173_free

REM ---------- 6. 启动后端 ----------
echo [启动] 后端  http://localhost:8000   Swagger: http://localhost:8000/docs
start "RAG-Backend" /D "%ROOT%" cmd /k ".venv\Scripts\python.exe backend\main.py"

REM ---------- 7. 启动前端 ----------
if defined SKIP_FE goto after_fe
if exist "%ROOT%\frontend\node_modules" goto fe_deps_ok
echo [信息] 首次运行，正在安装前端依赖，请稍候 ...
pushd "%ROOT%\frontend"
call npm install
popd
:fe_deps_ok
echo [启动] 前端  http://localhost:5173
start "RAG-Frontend" /D "%ROOT%\frontend" cmd /k "npm run dev"
:after_fe

REM ---------- 8. 打开浏览器 ----------
echo.
echo 等待服务就绪 ...
timeout /t 8 /nobreak >nul
if defined SKIP_FE goto summary
start "" "http://localhost:5173"
:summary
echo.
echo ============================================================
echo   已启动:
echo     后端  http://localhost:8000   Swagger: http://localhost:8000/docs
echo     前端  http://localhost:5173
echo   停止服务: 关闭弹出的两个窗口即可。
echo ============================================================
echo.
pause