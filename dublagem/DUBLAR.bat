@echo off
chcp 65001 >nul
title Dublagem PT-BR (2 vozes, gratuito)
cd /d "%~dp0"
echo ==============================================
echo   DUBLAGEM PT-BR - duas vozes, 100%% gratuito
echo ==============================================
echo.

if exist ".venv\Scripts\python.exe" goto :temvenv

rem --- procura um Python instalado ---
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if defined PY goto :criarvenv

echo Python nao encontrado. Instalando (gratuito, so na primeira vez)...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
%PY% -c "import sys" >nul 2>&1 || (
  echo.
  echo Nao consegui instalar o Python sozinho.
  echo Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH"
  echo e de dois cliques neste arquivo de novo.
  pause & exit /b 1
)

:criarvenv
echo Preparando as ferramentas (so na primeira vez, alguns minutos)...
%PY% -m venv .venv || (echo Falha ao criar ambiente Python. & pause & exit /b 1)

:temvenv
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install -q --upgrade pip >nul 2>&1
"%VPY%" -m pip install -q --upgrade -r requirements.txt || (echo Falha ao instalar ferramentas. Verifique a internet. & pause & exit /b 1)

echo.
set "LINK=https://www.youtube.com/watch?v=z9gTnoftoRU"
set /p "LINK=Cole o link do YouTube e aperte Enter (ou so Enter para o video padrao): "
echo.
"%VPY%" dublar.py "%LINK%" --saida saida
if errorlevel 1 (echo. & echo Deu erro. Copie as mensagens acima e mande para o Claude. & pause & exit /b 1)

explorer saida
echo.
echo Pronto! Mande o arquivo saida\roteiro_pt.txt para o Claude fazer o guia de temas.
pause
