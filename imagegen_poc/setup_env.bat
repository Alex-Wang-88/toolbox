@echo off
REM Install MOSS-Image inference deps into the dedicated venv.
REM All paths are relative to this script; no hard-coded absolute paths.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%venv_imagegen\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] venv not found. Create it first:
  echo   runtime\python\python.exe -m venv imagegen_poc\venv_imagegen
  exit /b 1
)

REM Save proxy vars. torch needs them CLEARED (local proxy 502s on the 2.7GB
REM wheel); everything else needs them BACK (pip cannot reach PyPI direct).
set "SAVED_HTTPS=%HTTPS_PROXY%"
set "SAVED_HTTP=%HTTP_PROXY%"
set "SAVED_ALL=%ALL_PROXY%"
set "SAVED_https=%https_proxy%"
set "SAVED_http=%http_proxy%"

echo [1/3] upgrading pip
"%PY%" -m pip install --upgrade pip -i https://pypi.org/simple || goto :fail

echo [2/3] installing torch 2.11.0+cu128 DIRECT (2.75GB, be patient)
set "HTTPS_PROXY="
set "HTTP_PROXY="
set "ALL_PROXY="
set "https_proxy="
set "http_proxy="
"%PY%" -m pip install "torch==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128 || goto :fail

echo [3/3] installing diffusers / transformers / accelerate
REM NOTE: the Tsinghua mirror returns an EMPTY index to pip 26, so use PyPI.
set "HTTPS_PROXY=%SAVED_HTTPS%"
set "HTTP_PROXY=%SAVED_HTTP%"
set "ALL_PROXY=%SAVED_ALL%"
set "https_proxy=%SAVED_https%"
set "http_proxy=%SAVED_http%"
"%PY%" -m pip install -r "%HERE%requirements.txt" -i https://pypi.org/simple || goto :fail

echo.
echo [OK] environment ready. Verifying:
"%PY%" -c "import torch, diffusers, transformers, accelerate; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.is_available()); print('diffusers', diffusers.__version__, 'transformers', transformers.__version__)"
exit /b 0

:fail
echo [FAILED] see pip output above
exit /b 1
