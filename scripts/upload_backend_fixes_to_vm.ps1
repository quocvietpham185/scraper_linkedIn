param(
    [string]$VmUser = "vmadmin",
    [string]$VmHost = "10.30.50.29",
    [string]$RemoteRoot = "/home/vmadmin/vietpq-linkedin-scraper",
    [string]$Pm2Process = "vietpq-backend",
    [string]$SshKey = "",
    [bool]$EnableSchedule = $true,
    [string]$ScheduleTime = "08:00",
    [string]$ScheduledCrawlerType = "auto",
    [int]$ScheduledAccountDelaySec = 300,
    [bool]$RunScheduleOnStartup = $false,
    [switch]$PushApifyActor
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RemoteProject = "$RemoteRoot/linkedin_group_crawler"
$Target = "${VmUser}@${VmHost}"

$SshArgs = @()
if ($SshKey.Trim()) {
    $SshArgs += @("-i", $SshKey)
}

function ConvertTo-ShellSingleQuoted([string]$Value) {
    return "'" + $Value.Replace("'", "'`"`"'") + "'"
}

$Files = @(
    @{
        Local = "linkedin_group_crawler/app/services/apify_crawler_service.py"
        RemoteDir = "$RemoteProject/app/services/"
    },
    @{
        Local = "linkedin_group_crawler/app/config.py"
        RemoteDir = "$RemoteProject/app/"
    },
    @{
        Local = "linkedin_group_crawler/app/main.py"
        RemoteDir = "$RemoteProject/app/"
    },
    @{
        Local = "linkedin_group_crawler/app/services/background_crawler_service.py"
        RemoteDir = "$RemoteProject/app/services/"
    },
    @{
        Local = "linkedin_group_crawler/app/services/scheduled_crawl_service.py"
        RemoteDir = "$RemoteProject/app/services/"
    }
)

if ($PushApifyActor) {
    $Files += @{
        Local = "linkedin-apify-actor/src/main.js"
        RemoteDir = "$RemoteRoot/linkedin-apify-actor/src/"
    }
}

Push-Location $RepoRoot
try {
    foreach ($File in $Files) {
        $LocalPath = $File.Local
        $RemoteDir = $File.RemoteDir
        Write-Host "Uploading $LocalPath -> ${Target}:$RemoteDir"
        & scp @SshArgs $LocalPath "${Target}:$RemoteDir"
    }

    $RemoteProjectQuoted = ConvertTo-ShellSingleQuoted $RemoteProject
    $EnableScheduleValue = if ($EnableSchedule) { "true" } else { "false" }
    $RunScheduleOnStartupValue = if ($RunScheduleOnStartup) { "true" } else { "false" }

    $RemoteCommand = @'
set -e
cd __REMOTE_PROJECT__

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

env_path = Path(".env")
updates = {
    "APIFY_DEFAULT_MAX_ITEMS": "20",
    "APIFY_DEFAULT_SCROLL_TIMES": "3",
    "APIFY_DELAY_MIN_MS": "5000",
    "APIFY_DELAY_MAX_MS": "12000",
    "APIFY_BATCH_SIZE": "5",
    "APIFY_GROUP_DELAY_MIN_SEC": "300",
    "APIFY_GROUP_DELAY_MAX_SEC": "600",
    "BACKEND_BATCH_DELAY_MIN_SEC": "900",
    "BACKEND_BATCH_DELAY_MAX_SEC": "1800",
    "CRAWL_BATCH_GROUP_DELAY_MIN_SEC": "30",
    "CRAWL_BATCH_GROUP_DELAY_MAX_SEC": "90",
    "SCHEDULED_CRAWL_ENABLED": "__ENABLE_SCHEDULE__",
    "SCHEDULED_CRAWL_TIME": "__SCHEDULE_TIME__",
    "SCHEDULED_CRAWLER_TYPE": "__SCHEDULED_CRAWLER_TYPE__",
    "SCHEDULED_ACCOUNT_DELAY_SEC": "__SCHEDULED_ACCOUNT_DELAY_SEC__",
    "SCHEDULED_RUN_ON_STARTUP": "__RUN_SCHEDULE_ON_STARTUP__",
}

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
next_lines = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in updates:
        continue
    else:
        next_lines.append(line)

for key, value in updates.items():
    next_lines.append(f"{key}={value}")

env_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
PY

source .venv/bin/activate
"$PYTHON_BIN" -m compileall app
pm2 restart "__PM2_PROCESS__" --update-env || pm2 restart "__PM2_PROCESS__"
'@
    $RemoteCommand = $RemoteCommand.Replace("__REMOTE_PROJECT__", $RemoteProjectQuoted)
    $RemoteCommand = $RemoteCommand.Replace("__PM2_PROCESS__", $Pm2Process.Replace('"', '\"'))
    $RemoteCommand = $RemoteCommand.Replace("__ENABLE_SCHEDULE__", $EnableScheduleValue)
    $RemoteCommand = $RemoteCommand.Replace("__SCHEDULE_TIME__", $ScheduleTime)
    $RemoteCommand = $RemoteCommand.Replace("__SCHEDULED_CRAWLER_TYPE__", $ScheduledCrawlerType)
    $RemoteCommand = $RemoteCommand.Replace("__SCHEDULED_ACCOUNT_DELAY_SEC__", [string]$ScheduledAccountDelaySec)
    $RemoteCommand = $RemoteCommand.Replace("__RUN_SCHEDULE_ON_STARTUP__", $RunScheduleOnStartupValue)
    $RemoteCommand = $RemoteCommand.Replace("`r", "")

    Write-Host "Compiling and restarting backend on $Target"
    $RemoteCommand | & ssh @SshArgs $Target "bash -s"

    if ($PushApifyActor) {
        $RemoteActorDirQuoted = ConvertTo-ShellSingleQuoted "$RemoteRoot/linkedin-apify-actor"
        Write-Host "Pushing Apify actor from VM"
        & ssh @SshArgs $Target "cd $RemoteActorDirQuoted && apify push"
    }
}
finally {
    Pop-Location
}
