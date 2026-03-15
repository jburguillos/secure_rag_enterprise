param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$CasesPath = "tests/redteam/prompts.json",
    [int]$LoadRequests = 10,
    [int]$LoadConcurrency = 1,
    [double]$MaxFailureRate = 0.20,
    [string]$BearerToken = "",
    [string]$TokenUrl = "http://localhost:8080/realms/secure-rag/protocol/openid-connect/token",
    [string]$ClientId = "secure-rag-api",
    [string]$Username = "",
    [string]$Password = "",
    [string]$BackupDir = "backups",
    [switch]$SkipSecurity,
    [switch]$SkipLoad,
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [string]$Name,
        [bool]$Pass,
        [string]$Details
    )
    $checks.Add([pscustomobject]@{
        check = $Name
        status = if ($Pass) { "PASS" } else { "FAIL" }
        details = $Details
    }) | Out-Null
}

function Get-HttpErrorDetails {
    param($ErrorRecord)
    $message = $ErrorRecord.Exception.Message
    try {
        $response = $ErrorRecord.Exception.Response
        if ($null -ne $response) {
            $stream = $response.GetResponseStream()
            if ($null -ne $stream) {
                $reader = New-Object System.IO.StreamReader($stream)
                $body = $reader.ReadToEnd()
                if (-not [string]::IsNullOrWhiteSpace($body)) {
                    return "$message | body=$body"
                }
            }
        }
    } catch {
    }
    return $message
}

function Get-KeycloakToken {
    param(
        [string]$TokenUrlArg,
        [string]$ClientIdArg,
        [string]$UsernameArg,
        [string]$PasswordArg
    )
    $body = "grant_type=password&client_id=$ClientIdArg&username=$UsernameArg&password=$PasswordArg"
    $resp = Invoke-RestMethod -Method Post -Uri $TokenUrlArg -ContentType "application/x-www-form-urlencoded" -Body $body -TimeoutSec 30
    return [string]$resp.access_token
}

$authEnabled = $false
$effectiveToken = $BearerToken

Write-Host "[1/6] Health + metrics"
try {
    $live = Invoke-RestMethod -Method Get -Uri ("{0}/health/liveness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
    $ready = Invoke-RestMethod -Method Get -Uri ("{0}/health/readiness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
    $metricsBody = [string](Invoke-RestMethod -Method Get -Uri ("{0}/metrics" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30)
    Add-Check "Liveness endpoint" ($live.status -eq "ok") ("status={0}" -f $live.status)
    Add-Check "Readiness endpoint" ($ready.status -eq "ok") ("status={0}" -f $ready.status)
    Add-Check "Metrics endpoint" ($metricsBody -match "http_requests_total") "status=ok"
} catch {
    Add-Check "Health/metrics" $false (Get-HttpErrorDetails $_)
}

Write-Host "[2/6] Security mode env checks"
try {
    $envOut = docker compose exec -T api env
    $authEnabledVar = ($envOut | Where-Object { $_ -match "^AUTH_ENABLED=" } | Select-Object -First 1)
    $authEnabled = ($authEnabledVar -match "AUTH_ENABLED=true")
    $allowOutbound = ($envOut | Where-Object { $_ -match "^ALLOW_OUTBOUND=" } | Select-Object -First 1)
    $allowPublic = ($envOut | Where-Object { $_ -match "^ALLOW_PUBLIC_LLM=" } | Select-Object -First 1)
    $outboundPass = [bool]($allowOutbound -match "ALLOW_OUTBOUND=false")
    $publicPass = [bool]($allowPublic -match "ALLOW_PUBLIC_LLM=false")
    Add-Check "AUTH_ENABLED detected" $true ($authEnabledVar)
    Add-Check "ALLOW_OUTBOUND disabled" $outboundPass ($allowOutbound)
    Add-Check "ALLOW_PUBLIC_LLM disabled" $publicPass ($allowPublic)
} catch {
    Add-Check "Security mode env checks" $false $_.Exception.Message
}

if ([string]::IsNullOrWhiteSpace($effectiveToken) -and -not [string]::IsNullOrWhiteSpace($Username) -and -not [string]::IsNullOrWhiteSpace($Password)) {
    try {
        $effectiveToken = Get-KeycloakToken -TokenUrlArg $TokenUrl -ClientIdArg $ClientId -UsernameArg $Username -PasswordArg $Password
        Add-Check "Bearer token acquisition" (-not [string]::IsNullOrWhiteSpace($effectiveToken)) "token acquired via Keycloak direct grant"
    } catch {
        Add-Check "Bearer token acquisition" $false (Get-HttpErrorDetails $_)
    }
}

if ($authEnabled -and [string]::IsNullOrWhiteSpace($effectiveToken)) {
    if ($SkipSecurity -and $SkipLoad) {
        Add-Check "Auth token for gated checks" $true "not required (security/load skipped)"
    } else {
        Add-Check "Auth token for gated checks" $false "AUTH_ENABLED=true but no token provided/acquired. Use -BearerToken or -Username/-Password."
    }
}

Write-Host "[3/6] Security regression suite"
if ($SkipSecurity) {
    Add-Check "Security regression" $true "skipped"
} else {
    try {
        if ([string]::IsNullOrWhiteSpace($effectiveToken)) {
            python scripts/security_regression.py --url $ApiUrl --cases $CasesPath --timeout 120
        } else {
            python scripts/security_regression.py --url $ApiUrl --cases $CasesPath --timeout 120 --bearer-token $effectiveToken
        }
        if ($LASTEXITCODE -ne 0) { throw "security_regression_exit_$LASTEXITCODE" }
        Add-Check "Security regression" $true ("cases={0}" -f $CasesPath)
    } catch {
        Add-Check "Security regression" $false $_.Exception.Message
    }
}

Write-Host "[4/6] Load test"
if ($SkipLoad) {
    Add-Check "Load test" $true "skipped"
} else {
    try {
        if (-not [string]::IsNullOrWhiteSpace($Username) -and -not [string]::IsNullOrWhiteSpace($Password)) {
            python scripts/load_test.py --url ("{0}/query" -f $ApiUrl.TrimEnd('/')) --requests $LoadRequests --concurrency $LoadConcurrency --max-failure-rate $MaxFailureRate --timeout 240 --token-url $TokenUrl --client-id $ClientId --username $Username --password $Password
        } elseif ([string]::IsNullOrWhiteSpace($effectiveToken)) {
            python scripts/load_test.py --url ("{0}/query" -f $ApiUrl.TrimEnd('/')) --requests $LoadRequests --concurrency $LoadConcurrency --max-failure-rate $MaxFailureRate --timeout 240
        } else {
            python scripts/load_test.py --url ("{0}/query" -f $ApiUrl.TrimEnd('/')) --requests $LoadRequests --concurrency $LoadConcurrency --max-failure-rate $MaxFailureRate --timeout 240 --bearer-token $effectiveToken
        }
        if ($LASTEXITCODE -ne 0) { throw "load_test_exit_$LASTEXITCODE" }
        Add-Check "Load test" $true ("requests={0} concurrency={1}" -f $LoadRequests, $LoadConcurrency)
    } catch {
        Add-Check "Load test" $false $_.Exception.Message
    }
}

Write-Host "[5/6] Backup create"
if ($SkipBackup) {
    Add-Check "Backup create" $true "skipped"
    Add-Check "Backup restore simulation" $true "skipped"
} else {
    try {
        python scripts/backup_restore.py backup --backup-dir $BackupDir
        if ($LASTEXITCODE -ne 0) { throw "backup_create_exit_$LASTEXITCODE" }
        $latestManifest = Get-ChildItem -Path $BackupDir -Recurse -Filter manifest.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $hasManifest = $null -ne $latestManifest
        Add-Check "Backup create" $hasManifest ($(if ($hasManifest) { $latestManifest.FullName } else { "manifest missing" }))
        if ($hasManifest) {
            python scripts/backup_restore.py restore --backup-dir $BackupDir --manifest $latestManifest.FullName --skip-postgres --skip-qdrant
            if ($LASTEXITCODE -ne 0) { throw "backup_restore_exit_$LASTEXITCODE" }
            Add-Check "Backup restore simulation" $true "manifest load + restore flow ok (skip data apply)"
        } else {
            Add-Check "Backup restore simulation" $false "manifest missing"
        }
    } catch {
        Add-Check "Backup create/restore simulation" $false $_.Exception.Message
    }
}

Write-Host "[6/6] Results"
$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { $_.status -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Error ("Phase 5 verification failed: {0} checks failed." -f $failed.Count)
    exit 1
}
Write-Host "Phase 5 verification passed." -ForegroundColor Green
