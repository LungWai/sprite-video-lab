@echo off
setlocal
cd /d "%~dp0"

set "AI_ROOT=E:\sprite-video-lab-models"
if not exist "E:\" set "AI_ROOT=%~dp0work\models"

set "SPRITE_VIDEO_LAB_AI_MODEL_CACHE=%AI_ROOT%\huggingface"
set "HF_HOME=%SPRITE_VIDEO_LAB_AI_MODEL_CACHE%"
set "HUGGINGFACE_HUB_CACHE=%SPRITE_VIDEO_LAB_AI_MODEL_CACHE%\hub"
set "TRANSFORMERS_CACHE=%SPRITE_VIDEO_LAB_AI_MODEL_CACHE%\transformers"
set "HF_MODULES_CACHE=%SPRITE_VIDEO_LAB_AI_MODEL_CACHE%\modules"
set "HF_XET_CACHE=%SPRITE_VIDEO_LAB_AI_MODEL_CACHE%\xet"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PIP_CACHE_DIR=%AI_ROOT%\pip-cache"
set "VENV_DIR=%AI_ROOT%\venv"
set "SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT=%AI_ROOT%\EZ-CorridorKey"
set "CORRIDORKEY_SOURCE_REVISION=35b6b75041dbe30c1bbdf8f9af2d91070a782774"

if not exist "%AI_ROOT%" mkdir "%AI_ROOT%"

set "PY_LAUNCHER="
set "BOOTSTRAP_PYTHON="
for /f "delims=" %%i in ('where py 2^>nul') do (
  set "PY_LAUNCHER=%%i"
  goto :bootstrap_ready
)
for /f "delims=" %%i in ('where python 2^>nul') do (
  set "BOOTSTRAP_PYTHON=%%i"
  goto :bootstrap_ready
)

echo Python not found.
exit /b 1

:bootstrap_ready
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating AI runtime at %VENV_DIR%
  if not "%PY_LAUNCHER%"=="" (
    "%PY_LAUNCHER%" -3 -m venv "%VENV_DIR%"
  ) else (
    "%BOOTSTRAP_PYTHON%" -m venv "%VENV_DIR%"
  )
)

set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

"%PYTHON_EXE%" -m pip install --upgrade pip
"%PYTHON_EXE%" -m pip install -r requirements.txt
"%PYTHON_EXE%" -m pip install -r requirements-ai.txt
"%PYTHON_EXE%" -m pip install --force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cu128 torch torchvision

if not exist "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%\CorridorKeyModule" (
  for /f "delims=" %%i in ('where git 2^>nul') do (
    echo Installing verified EZ-CorridorKey source at %CORRIDORKEY_SOURCE_REVISION%
    if not exist "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" mkdir "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%"
    git -C "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" init
    git -C "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" remote remove origin >nul 2>nul
    git -C "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" remote add origin https://github.com/edenaion/EZ-CorridorKey
    git -C "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" fetch --depth 1 origin %CORRIDORKEY_SOURCE_REVISION%
    if errorlevel 1 goto :corridorkey_install_failed
    git -C "%SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%" checkout --detach FETCH_HEAD
    if errorlevel 1 goto :corridorkey_install_failed
    goto :corridorkey_ready
  )
  echo CorridorKey was not cloned because git was not found.
  echo Install git or clone https://github.com/edenaion/EZ-CorridorKey to %SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%
  goto :corridorkey_install_failed
) else (
  echo CorridorKey is available at %SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%
)
goto :corridorkey_ready

:corridorkey_install_failed
echo Failed to install the verified EZ-CorridorKey source revision.
exit /b 1

:corridorkey_ready

echo.
echo AI runtime is ready:
echo   %VENV_DIR%
echo Models will be cached in:
echo   %SPRITE_VIDEO_LAB_AI_MODEL_CACHE%
echo CorridorKey root:
echo   %SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT%
echo.
pause
