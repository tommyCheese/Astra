@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

where npm >nul 2>&1
if errorlevel 1 (
  echo [Astra] Error: npm is required. Install Node.js and try again.
  exit /b 1
)

if not exist "%VENV_PYTHON%" (
  where py >nul 2>&1
  if not errorlevel 1 (
    echo [Astra] Creating the backend virtual environment...
    py -3 -m venv "%VENV_DIR%"
  ) else (
    where python >nul 2>&1
    if errorlevel 1 (
      echo [Astra] Error: Python 3.10+ is required.
      exit /b 1
    )
    echo [Astra] Creating the backend virtual environment...
    python -m venv "%VENV_DIR%"
  )
  if errorlevel 1 exit /b 1
)

"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info ^>= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [Astra] Error: The backend virtual environment must use Python 3.10+.
  echo [Astra] Remove backend\.venv and retry.
  exit /b 1
)

"%VENV_PYTHON%" -c "import alembic, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo [Astra] Installing backend dependencies...
  "%VENV_PYTHON%" -m pip install -e "%BACKEND_DIR%"
  if errorlevel 1 exit /b 1
)

if not exist "%FRONTEND_DIR%\node_modules\.bin\vite.cmd" (
  echo [Astra] Installing frontend dependencies...
  pushd "%FRONTEND_DIR%"
  call npm ci
  if errorlevel 1 (
    popd
    exit /b 1
  )
  popd
)

if not exist "%BACKEND_DIR%\.env" (
  echo [Astra] Creating backend\.env with the local mock model...
  >"%BACKEND_DIR%\.env" (
    echo DATABASE_URL=sqlite+aiosqlite:///./astra-dev.db
    echo MODEL_PROVIDER=mock
    echo MODEL_NAME=mock
  )
)

echo [Astra] Applying database migrations...
pushd "%BACKEND_DIR%"
"%VENV_PYTHON%" -m alembic upgrade head
if errorlevel 1 (
  popd
  exit /b 1
)
popd

echo [Astra] Starting backend at http://127.0.0.1:8000 ...
start "Astra Backend" /D "%BACKEND_DIR%" cmd /k ""%VENV_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [Astra] Starting frontend at http://127.0.0.1:5173 ...
start "Astra Frontend" /D "%FRONTEND_DIR%" cmd /k "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"

echo [Astra] Startup commands dispatched. Close both service windows to stop Astra.
endlocal
