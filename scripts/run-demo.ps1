param([string]$ApiBase = 'http://localhost:8000', [switch]$Authenticated)
$ErrorActionPreference = 'Stop'
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
function Request-Api([string]$Path, [string]$Method = 'GET', $Body = $null) {
    $parameters = @{ Uri = "$ApiBase/api$Path"; Method = $Method; WebSession = $session; TimeoutSec = 30 }
    if ($null -ne $Body) { $parameters.ContentType = 'application/json'; $parameters.Body = ConvertTo-Json $Body -Depth 12 }
    Invoke-RestMethod @parameters
}
if ($Authenticated) {
    $secure = Read-Host 'Application token' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer); Request-Api '/session' 'POST' @{token=$token} | Out-Null }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $token = $null }
}
$config = Request-Api '/settings'
if ($config.remediation_mode -ne 'dry_run') { throw 'This walkthrough requires REMEDIATION_MODE=dry_run. See docs/DEMO.md for controlled execution.' }
$workers = @(Request-Api '/workers')
if (-not ($workers | Where-Object { $_.name -eq 'experiment@autopilot' -and $_.status -eq 'ONLINE' })) { throw 'Experimental worker is not online yet. Wait for startup and check System health.' }
Request-Api '/workload' 'POST' @{count=6;name='mixed'} | Out-Null
$fault = Request-Api '/faults/inject' 'POST' @{fault_type='API_TIMEOUT';duration_seconds=40;task_count=3}
Write-Host "Fault scheduled: $($fault.id). Waiting for actual task failures."
$deadline = (Get-Date).AddSeconds(120)
$incident = $null
do {
    Start-Sleep -Seconds 2
    $incident = @(Request-Api '/incidents') | Where-Object { $_.scope_id -eq $fault.scope_id } | Sort-Object created_at | Select-Object -First 1
} until ($incident -or (Get-Date) -gt $deadline)
if (-not $incident) { throw 'No incident detected within 120 seconds. Inspect control and worker logs.' }
$path = '/incidents/' + $incident.id
$detail = Request-Api $path
while (($detail.incident.investigation_requested -or @($detail.investigations | Where-Object status -eq 'RUNNING').Count) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $detail = Request-Api $path
}
Request-Api "$path/investigate" 'POST' @{configuration='RULE_BASED'} | Out-Null
do {
    Start-Sleep -Seconds 2
    $detail = Request-Api $path
    $rule = @($detail.investigations | Where-Object configuration -eq 'RULE_BASED') | Sort-Object created_at | Select-Object -Last 1
} until (($rule -and $rule.status -ne 'RUNNING') -or (Get-Date) -gt $deadline)
if (-not $rule -or $rule.status -ne 'COMPLETED') { throw 'Rule investigation did not complete. Inspect its recorded error.' }
$verification = Request-Api "$path/verify" 'POST'
$proposal = Request-Api "$path/remediate" 'POST'
Request-Api ('/faults/' + $fault.id + '/reset') 'POST' | Out-Null
Write-Host "Incident: http://localhost:5173/incidents/$($incident.id)"
Write-Host "Verification passed: $($verification.verified)"
Write-Host "Policy: $($proposal.policy_decision); mode/status: $($proposal.mode)/$($proposal.status)"
Write-Host 'Review observed evidence and policy reasons in the dashboard. A dry run has not executed a remediation.'
