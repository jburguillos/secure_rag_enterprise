param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$TokenUrl = "http://localhost:8080/realms/secure-rag/protocol/openid-connect/token",
    [string]$ClientId = "secure-rag-api",
    [string]$HrUsername = "hr.user",
    [string]$HrPassword = "ChangeMe123!",
    [string]$FinanceUsername = "finance.user",
    [string]$FinancePassword = "ChangeMe123!"
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

function Wait-ApiReady {
    param(
        [int]$MaxAttempts = 30,
        [int]$DelaySeconds = 2
    )

    $uri = "{0}/health/liveness" -f $ApiUrl.TrimEnd('/')
    for ($i = 1; $i -le $MaxAttempts; $i++) {
        try {
            $live = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 5
            if ($live.status -eq "ok") {
                return $true
            }
        } catch {
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

function Get-Token {
    param(
        [string]$Username,
        [string]$Password
    )
    $body = "grant_type=password&client_id=$ClientId&username=$Username&password=$Password"
    $resp = Invoke-RestMethod -Method Post -Uri $TokenUrl -ContentType "application/x-www-form-urlencoded" -Body $body -TimeoutSec 30
    return [string]$resp.access_token
}

function Query-WithToken {
    param(
        [string]$Token
    )

    $payload = @{
        query = "Summarize available local documents in two bullets with citations."
        mode = "summarize"
        top_k = 12
        include_images = $false
        filters = @{ sources = @("local_folder") }
    } | ConvertTo-Json -Depth 20

    return Invoke-RestMethod -Method Post -Uri ("{0}/query" -f $ApiUrl.TrimEnd('/')) -Headers @{ Authorization = "Bearer $Token" } -ContentType "application/json" -Body $payload -TimeoutSec 120
}

Write-Host "[1/6] Wait for API"
$ready = Wait-ApiReady
if ($ready) {
    $readyDetails = "liveness=ok"
} else {
    $readyDetails = "timed out waiting for API"
}
Add-Check "API reachable" $ready $readyDetails

Write-Host "[2/6] Health"
if ($ready) {
    try {
        $live = Invoke-RestMethod -Method Get -Uri ("{0}/health/liveness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
        Add-Check "Liveness endpoint" ($live.status -eq "ok") ("status={0}" -f $live.status)
    } catch {
        Add-Check "Liveness endpoint" $false (Get-HttpErrorDetails $_)
    }
} else {
    Add-Check "Liveness endpoint" $false "skipped; API unreachable"
}

Write-Host "[3/6] Auth required check"
if ($ready) {
    try {
        $payload = @{ query = "ping"; mode = "qa"; include_images = $false } | ConvertTo-Json -Depth 8
        $null = Invoke-RestMethod -Method Post -Uri ("{0}/query" -f $ApiUrl.TrimEnd('/')) -ContentType "application/json" -Body $payload -TimeoutSec 30
        Add-Check "Anonymous query blocked" $false "query unexpectedly succeeded; AUTH_ENABLED likely false"
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        Add-Check "Anonymous query blocked" ($statusCode -eq 401) ("status={0}" -f $statusCode)
    }
} else {
    Add-Check "Anonymous query blocked" $false "skipped; API unreachable"
}

$hrToken = ""
$financeToken = ""

Write-Host "[4/6] Token issuance"
try {
    $hrToken = Get-Token -Username $HrUsername -Password $HrPassword
    $financeToken = Get-Token -Username $FinanceUsername -Password $FinancePassword
    Add-Check "Keycloak direct-grant tokens" ((-not [string]::IsNullOrWhiteSpace($hrToken)) -and (-not [string]::IsNullOrWhiteSpace($financeToken))) "hr+finance tokens minted"
} catch {
    Add-Check "Keycloak direct-grant tokens" $false (Get-HttpErrorDetails $_)
}

Write-Host "[5/6] ACL checks with JWT claims"
if (-not $ready) {
    Add-Check "HR JWT ACL" $false "skipped; API unreachable"
    Add-Check "Finance JWT ACL" $false "skipped; API unreachable"
    Add-Check "Audit run_id generated" $false "skipped; API unreachable"
} elseif ([string]::IsNullOrWhiteSpace($hrToken) -or [string]::IsNullOrWhiteSpace($financeToken)) {
    Add-Check "HR JWT ACL" $false "skipped; missing token"
    Add-Check "Finance JWT ACL" $false "skipped; missing token"
    Add-Check "Audit run_id generated" $false "skipped; missing token"
} else {
    try {
        $hrResp = Query-WithToken -Token $hrToken
        $hrDocs = @($hrResp.citations | ForEach-Object { $_.doc_id } | Sort-Object -Unique)
        $hrPass = ($hrDocs -contains "hr_only.txt") -and ($hrDocs -contains "public.txt") -and -not ($hrDocs -contains "finance_only.txt")
        Add-Check "HR JWT ACL" $hrPass ("cited={0}" -f ($hrDocs -join ", "))

        $finResp = Query-WithToken -Token $financeToken
        $finDocs = @($finResp.citations | ForEach-Object { $_.doc_id } | Sort-Object -Unique)
        $finPass = ($finDocs -contains "finance_only.txt") -and ($finDocs -contains "public.txt") -and -not ($finDocs -contains "hr_only.txt")
        Add-Check "Finance JWT ACL" $finPass ("cited={0}" -f ($finDocs -join ", "))

        Add-Check "Audit run_id generated" ((-not [string]::IsNullOrWhiteSpace([string]$hrResp.run_id)) -and (-not [string]::IsNullOrWhiteSpace([string]$finResp.run_id))) ("hr_run={0}, fin_run={1}" -f $hrResp.run_id, $finResp.run_id)
    } catch {
        Add-Check "JWT ACL regression" $false (Get-HttpErrorDetails $_)
    }
}

Write-Host "[6/6] Results"
$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { $_.status -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Error ("Phase 2 verification failed: {0} checks failed." -f $failed.Count)
    exit 1
}

Write-Host "Phase 2 verification passed." -ForegroundColor Green
