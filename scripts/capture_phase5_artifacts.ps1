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
    [string]$OutRoot = "artifacts/capstone"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $OutRoot $ts
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "Output folder: $out"

function Run-And-Capture {
    param(
        [string]$Name,
        [scriptblock]$Script,
        [string]$OutputFile
    )
    Write-Host "Running: $Name"
    try {
        $script:LASTEXITCODE = 0
        & $Script *>&1 | Tee-Object $OutputFile | Out-Null
        $exitCode = if ($script:LASTEXITCODE -is [int]) { [int]$script:LASTEXITCODE } else { 0 }
        if ($exitCode -ne 0) {
            return $exitCode
        }
        return 0
    } catch {
        $_ | Out-String | Tee-Object $OutputFile -Append | Out-Null
        return 1
    }
}

# 1) Health and metrics snapshots
Invoke-RestMethod "$ApiUrl/health/liveness" | ConvertTo-Json | Set-Content (Join-Path $out "health_liveness.json")
Invoke-RestMethod "$ApiUrl/health/readiness" | ConvertTo-Json | Set-Content (Join-Path $out "health_readiness.json")
Invoke-WebRequest "$ApiUrl/metrics" -OutFile (Join-Path $out "metrics.prom")

# 2) API test suite in container
$pytestExit = Run-And-Capture `
    -Name "docker pytest" `
    -OutputFile (Join-Path $out "pytest_api_docker.txt") `
    -Script { docker compose exec -T api pytest -q }

# 3) Full phase 5 verification
$verifyArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", "scripts/verify_phase5.ps1",
    "-ApiUrl", $ApiUrl,
    "-CasesPath", $CasesPath,
    "-LoadRequests", $LoadRequests,
    "-LoadConcurrency", $LoadConcurrency,
    "-MaxFailureRate", $MaxFailureRate
)
if (-not [string]::IsNullOrWhiteSpace($BearerToken)) {
    $verifyArgs += @("-BearerToken", $BearerToken)
}
if (-not [string]::IsNullOrWhiteSpace($Username) -and -not [string]::IsNullOrWhiteSpace($Password)) {
    $verifyArgs += @("-Username", $Username, "-Password", $Password)
}
if (-not [string]::IsNullOrWhiteSpace($TokenUrl)) {
    $verifyArgs += @("-TokenUrl", $TokenUrl)
}
if (-not [string]::IsNullOrWhiteSpace($ClientId)) {
    $verifyArgs += @("-ClientId", $ClientId)
}

$verifyExit = Run-And-Capture `
    -Name "verify_phase5" `
    -OutputFile (Join-Path $out "verify_phase5.txt") `
    -Script { powershell @verifyArgs }

$summary = [pscustomobject]@{
    timestamp = $ts
    output_dir = (Resolve-Path $out).Path
    pytest_exit = $pytestExit
    verify_phase5_exit = $verifyExit
    files = @(
        "health_liveness.json",
        "health_readiness.json",
        "metrics.prom",
        "pytest_api_docker.txt",
        "verify_phase5.txt"
    )
}
$summary | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $out "summary.json")

Write-Host "Capture completed: $out"
if ($pytestExit -ne 0 -or $verifyExit -ne 0) {
    exit 1
}
