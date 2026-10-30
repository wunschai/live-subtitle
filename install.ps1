# 即時字幕 安裝程式（Windows）
# 用法：在專案資料夾按右鍵「以 PowerShell 執行」，或：
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Tier cpu-high] [-SkipBench] [-NoShortcut]
# 不需要系統管理員權限。

param(
    [string]$Tier = "",
    [switch]$SkipBench,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$logFile = Join-Path $PSScriptRoot "install.log"
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

# 右鍵「以 PowerShell 執行」時，出錯視窗會直接關掉：先停下來讓使用者看到訊息
trap {
    Write-Host "`n安裝失敗：$_" -ForegroundColor Red
    Write-Host "完整紀錄：$logFile"
    try { Stop-Transcript | Out-Null } catch { }
    Read-Host "按 Enter 關閉視窗"
    exit 1
}
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
Set-Location -LiteralPath $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# 1. uv（Python 與套件管理）
Step "檢查 uv"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "安裝 uv（使用者層級）…"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw "uv 安裝失敗，請手動安裝：https://docs.astral.sh/uv/" }
}
& uv --version

# 2. 粗篩硬體 → 決定要裝哪一組加速套件
Step "偵測硬體"
$extra = (& uv run --no-project --python 3.12 python src/livesub/hardware.py --print-extra).Trim()
if ($LASTEXITCODE -ne 0) { throw "硬體偵測失敗" }
switch ($extra) {
    "cuda"  { Write-Host "偵測到 NVIDIA 顯示卡：安裝 CUDA 加速套件" }
    "intel" { Write-Host "偵測到 Intel 內顯：安裝 OpenVINO 加速套件" }
    default { Write-Host "未偵測到可加速的 GPU：使用 CPU 模式"; $extra = "none" }
}

# 3. 建立環境並安裝套件（依 uv.lock 固定版本）
Step "安裝套件（第一次需要幾分鐘）"
if ($extra -eq "none") { & uv sync --frozen --no-dev }
else { & uv sync --frozen --no-dev --extra $extra }
if ($LASTEXITCODE -ne 0) { throw "套件安裝失敗" }

# 4. 實際載入模型、校正、寫入設定、建立捷徑
Step "下載模型與校正"
$setupArgs = @()
if ($Tier) { $setupArgs += @("--profile", $Tier) }
if ($SkipBench) { $setupArgs += "--skip-bench" }
if ($NoShortcut) { $setupArgs += "--no-shortcut" }
& .\.venv\Scripts\python.exe -m livesub.setup_cli @setupArgs
if ($LASTEXITCODE -ne 0) { throw "設定失敗" }

Step "完成"
Write-Host "從桌面或開始選單的「即時字幕」捷徑啟動，或執行：.\.venv\Scripts\pythonw.exe -m livesub"
try { Stop-Transcript | Out-Null } catch { }
Read-Host "按 Enter 關閉視窗"
