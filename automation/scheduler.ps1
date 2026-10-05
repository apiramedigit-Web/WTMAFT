# WTMAFT weekly Windows Task Scheduler registration.
# Registers ONE task that runs the existing pipeline (automation\run.py) once every 7 days:
# TUESDAY 11:30 local time (business instruction 2026-10-05; was Monday 15:00): a 7-day monitoring week ends on a
# Monday (Week 1 = 29 Sep -> 5 Oct), and the source loads day D on the morning of D+1 (Berlin time: account 6
# ~04:25, account 8 ~05:10, refreshes until ~07:30 = ~11:00 IST), so Tuesday 11:30 IST is the first run that
# can see the whole week (a day counts only when both accounts are loaded). extract.py uses a fixed lag (week ending the latest Saturday <= run date - 8
# days), which gives the same report week on Tuesday as on the Monday before it.
#
#   .\automation\scheduler.ps1              # register (or update) the task DISABLED - default
#   .\automation\scheduler.ps1 -Daily       # same, but with a DAILY 15:00 trigger. Safe: run.py is idempotent -
#                                           # each listing is checked/POSTed at most once per Monday-week and only
#                                           # >= 7 days after its last verification/POST; monitoring cycles are
#                                           # recomputed from the live-update anchors, never restarted; no e-mail.
#   .\automation\scheduler.ps1 -Enable      # enable the task for production (only after approval)
#   .\automation\scheduler.ps1 -Disable     # disable it again
#   .\automation\scheduler.ps1 -Unregister  # remove it
#
# Runs as the current user, "run only when user is logged on" (no stored password), like the
# other automation tasks on this machine. StartWhenAvailable runs a missed Tuesday when the PC
# is next on. The user environment supplies WLP_SOURCE_DB_URL. No e-mail is sent (removed 2026-10-02).
param([switch]$Enable, [switch]$Disable, [switch]$Unregister, [switch]$Daily)

$TaskName = 'WTMAFT_Weekly_Keyword_Monitoring'
$Project  = 'C:\Users\LED 222\Weekly_Top_Moving_ASIN_Keyword_Report'
$Python   = 'C:\Program Files\Python313\python.exe'

if ($Unregister) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "Unregistered $TaskName"; return }
if ($Enable)     { Enable-ScheduledTask  -TaskName $TaskName | Out-Null; "Enabled $TaskName";  return }
if ($Disable)    { Disable-ScheduledTask -TaskName $TaskName | Out-Null; "Disabled $TaskName"; return }

if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
if (-not (Test-Path -LiteralPath "$Project\automation\run.py")) { throw "run.py not found under $Project" }

$action   = New-ScheduledTaskAction -Execute $Python -Argument "`"$Project\automation\run.py`"" -WorkingDirectory $Project
$trigger  = if ($Daily) { New-ScheduledTaskTrigger -Daily -At '15:00' }
            else        { New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Tuesday -At '11:30' }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$task = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'WTMAFT keyword monitoring (weekly Tuesday 11:30, or daily 15:00 with -Daily; idempotent): extract -> weekly keyword check (--apply, due listings only: fresh GET, POST only for a genuinely new keyword value, accepted payloads never re-sent) -> build_dataset -> performance monitoring (Week 1 / Week 2 orders from the update date; 7/7 days required) -> render -> validate. No e-mail. No PH publish. Only backend keywords are ever updated.' -Force
Disable-ScheduledTask -TaskName $TaskName | Out-Null
"Registered $TaskName (DISABLED). Enable with: .\automation\scheduler.ps1 -Enable"
