# WTMAFT weekly Windows Task Scheduler registration.
# Registers ONE task that runs the existing pipeline (automation\run.py) once every 7 days:
# MONDAY 15:00 local time (business instruction 2026-09-29: weekly Monday cycle). extract.py uses a
# fixed one-week lag (the Sun-Sat week that ended 9 days before the Monday), which was loaded on every
# Monday Jul-Sep 2026, so each Monday processes exactly one new, contiguous week.
#
#   .\automation\scheduler.ps1              # register (or update) the task DISABLED - default
#   .\automation\scheduler.ps1 -Enable      # enable the task for production (only after approval)
#   .\automation\scheduler.ps1 -Disable     # disable it again
#   .\automation\scheduler.ps1 -Unregister  # remove it
#
# Runs as the current user, "run only when user is logged on" (no stored password), like the
# other automation tasks on this machine. StartWhenAvailable runs a missed Monday when the PC
# is next on. The user environment supplies WLP_SOURCE_DB_URL and the WTMA_GMAIL_* paths.
param([switch]$Enable, [switch]$Disable, [switch]$Unregister)

$TaskName = 'WTMAFT_Weekly_Keyword_Monitoring'
$Project  = 'C:\Users\LED 222\Weekly_Top_Moving_ASIN_Keyword_Report'
$Python   = 'C:\Program Files\Python313\python.exe'

if ($Unregister) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "Unregistered $TaskName"; return }
if ($Enable)     { Enable-ScheduledTask  -TaskName $TaskName | Out-Null; "Enabled $TaskName";  return }
if ($Disable)    { Disable-ScheduledTask -TaskName $TaskName | Out-Null; "Disabled $TaskName"; return }

if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
if (-not (Test-Path -LiteralPath "$Project\automation\run.py")) { throw "run.py not found under $Project" }

$action   = New-ScheduledTaskAction -Execute $Python -Argument "`"$Project\automation\run.py`"" -WorkingDirectory $Project
$trigger  = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At '15:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$task = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'WTMAFT weekly Monday cycle: extract -> pending re-check (read-only) -> build_dataset -> weekly keyword check (--apply, due ASINs only) -> performance_alert (--send) -> render -> validate. Alerts only on 2 consecutive 7D declines. Only backend keywords are ever updated.' -Force
Disable-ScheduledTask -TaskName $TaskName | Out-Null
"Registered $TaskName (DISABLED). Enable with: .\automation\scheduler.ps1 -Enable"
