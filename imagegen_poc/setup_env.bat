@echo off
REM Install MOSS-Image inference deps into the dedicated venv.
REM All paths are relative to this script; no hard-coded absolute paths.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%venv_imagegen\Scripts\python.exe"

REM The local proxy (127.0.0.1:59363) returns 502 on the 2.7GB torch wheel.
REM All sources below work without it: Tsinghua mirror + download.pytorch.org.
set "HTTPS_PROXY="
set "HTTP_PROXY="
set "ALL_PROXY="
set "https_proxy="
set "http_proxy="

if not exist "%PY%" (
  echo [ERROR] venv not found. Create it first:
  echo   runtime\python\python.exe -m venv imagegen_poc\venv_imagegen
  exit /b 1
)

echo [1/3] upgrading pip
"%PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple || goto :fail

echo [2/3] installing torch 2.11.0+cu128 (large wheel, be patient)
"%PY%" -m pip install "torch==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128 || goto :fail

echo [3/3] installing diffusers / transformers / accelerate / modelscope
"%PY%" -m pip install -r "%HERE%requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple || goto :fail

echo.
echo [OK] environment ready. Verifying:
"%PY%" -c "import torch, diffusers, transformers; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.is_available()); print('diffusers', diffusers.__version__, 'transformers', transformers.__version__)"
exit /b 0

:fail
echo [FAILED] see pip output above
exit /b 1
