param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$UiUrl = "http://localhost:8501",
    [string]$TokenUrl = "http://localhost:8080/realms/secure-rag/protocol/openid-connect/token",
    [string]$ClientId = "secure-rag-api",
    [string]$Username = "",
    [string]$Password = "",
    [string]$DriveDocQuery = "In 2024-Q4_Investor_Letter.pdf, summarize key points with citations.",
    [string]$TabularQuery = "In 2025_LP_Commitment_Register.xlsx, summarize the relevant evidence with citations.",
    [string]$OutRoot = "artifacts/capstone"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path (Join-Path $OutRoot $ts) "local_baseline"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$dockerConfig = Join-Path $out "_docker_config"
New-Item -ItemType Directory -Force -Path $dockerConfig | Out-Null
$env:DOCKER_CONFIG = $dockerConfig

function Write-JsonFile {
    param(
        [string]$Path,
        $Object
    )
    $Object | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $Path
}

function Run-And-Capture {
    param(
        [string]$Name,
        [scriptblock]$Script,
        [string]$OutputFile
    )
    Write-Host "Running: $Name"
    try {
        $script:LASTEXITCODE = 0
        $output = & $Script 2>&1
        $output | Tee-Object $OutputFile | Out-Null
        $exitCode = if ($script:LASTEXITCODE -is [int]) { [int]$script:LASTEXITCODE } else { 0 }
        return @{
            exit_code = $exitCode
            ok = ($exitCode -eq 0)
            file = $OutputFile
        }
    } catch {
        $_ | Out-String | Tee-Object $OutputFile -Append | Out-Null
        return @{
            exit_code = 1
            ok = $false
            file = $OutputFile
        }
    }
}

function Get-FileText {
    param([string]$Path)
    if (Test-Path $Path) {
        return (Get-Content $Path -Raw)
    }
    return ""
}

function Test-AccessDeniedDockerCapture {
    param([string]$Path)
    $text = Get-FileText -Path $Path
    return ($text -match "docker client must be run with elevated privileges" -or
            $text -match "open //\./pipe/docker_engine: Access is denied")
}

function Truncate-Text {
    param(
        [AllowNull()]
        [string]$Text,
        [int]$MaxLen = 240
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $Text
    }
    if ($Text.Length -le $MaxLen) {
        return $Text
    }
    return $Text.Substring(0, $MaxLen)
}

function Get-KeycloakToken {
    param(
        [string]$TokenUrlArg,
        [string]$ClientIdArg,
        [string]$UsernameArg,
        [string]$PasswordArg
    )
    $body = "grant_type=password&client_id=$([uri]::EscapeDataString($ClientIdArg))&username=$([uri]::EscapeDataString($UsernameArg))&password=$([uri]::EscapeDataString($PasswordArg))"
    $resp = Invoke-RestMethod -Method Post -Uri $TokenUrlArg -ContentType "application/x-www-form-urlencoded" -Body $body -TimeoutSec 30
    return [string]$resp.access_token
}

function Get-TokenClaims {
    param([string]$Token)
    if ([string]::IsNullOrWhiteSpace($Token)) {
        return $null
    }

    $payload = $Token.Split('.')[1]
    switch ($payload.Length % 4) {
        2 { $payload += "==" }
        3 { $payload += "=" }
    }
    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($payload.Replace('-', '+').Replace('_', '/')))
    return ($json | ConvertFrom-Json)
}

function Get-EnvFileMap {
    param([string]$Path = ".env")

    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -eq 2) {
            $map[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $map
}

function Select-DeploySettings {
    param([hashtable]$EnvMap)

    $keys = @(
        "APP_MODE",
        "ALLOW_PUBLIC_LLM",
        "ALLOW_OUTBOUND",
        "ENABLE_OCR",
        "AUTH_ENABLED",
        "KEYCLOAK_ISSUER",
        "KEYCLOAK_ISSUER_ALIASES",
        "KEYCLOAK_AUDIENCE",
        "OLLAMA_BASE_URL",
        "OLLAMA_CHAT_MODEL",
        "OLLAMA_EMBED_MODEL",
        "LLM_PROVIDER",
        "EMBEDDING_PROVIDER",
        "DOMAIN_CONTEXT_HINT"
    )

    $selected = [ordered]@{}
    foreach ($key in $keys) {
        $selected[$key] = if ($EnvMap.ContainsKey($key)) { $EnvMap[$key] } else { $null }
    }
    return $selected
}

Write-Host "Output folder: $out"

$gitCommit = (git rev-parse --short HEAD).Trim()
$gitBranch = (git branch --show-current).Trim()
$gitStatus = git status --short
$envMap = Get-EnvFileMap
$deploySettings = Select-DeploySettings -EnvMap $envMap
$warnings = New-Object System.Collections.Generic.List[string]

Set-Content -Encoding UTF8 (Join-Path $out "00_git_commit.txt") $gitCommit
Set-Content -Encoding UTF8 (Join-Path $out "00_git_branch.txt") $gitBranch
$gitStatus | Set-Content -Encoding UTF8 (Join-Path $out "00_git_status.txt")

$composePs = Run-And-Capture -Name "docker compose ps" -OutputFile (Join-Path $out "01_compose_ps.txt") -Script { docker compose ps }
$composeConfig = Run-And-Capture -Name "docker compose config" -OutputFile (Join-Path $out "01_compose_config.txt") -Script { docker compose config }
$composeImages = Run-And-Capture -Name "docker compose images" -OutputFile (Join-Path $out "01_compose_images.txt") -Script { docker compose images }

$composePsAccessDenied = Test-AccessDeniedDockerCapture -Path $composePs.file
$composeImagesAccessDenied = Test-AccessDeniedDockerCapture -Path $composeImages.file
if ($composePsAccessDenied) {
    $warnings.Add("docker compose ps could not access Docker Desktop pipe from this shell; treating as local capture warning.")
}
if ($composeImagesAccessDenied) {
    $warnings.Add("docker compose images could not access Docker Desktop pipe from this shell; treating as local capture warning.")
}

$live = Invoke-RestMethod -Method Get -Uri ("{0}/health/liveness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
$ready = Invoke-RestMethod -Method Get -Uri ("{0}/health/readiness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
$uiCheck = Invoke-WebRequest -Method Get -Uri $UiUrl -TimeoutSec 30 -UseBasicParsing
$metricsPath = Join-Path $out "02_metrics.prom"
Invoke-WebRequest -Method Get -Uri ("{0}/metrics" -f $ApiUrl.TrimEnd('/')) -OutFile $metricsPath -TimeoutSec 30 -UseBasicParsing
Write-JsonFile -Path (Join-Path $out "02_health_liveness.json") -Object $live
Write-JsonFile -Path (Join-Path $out "02_health_readiness.json") -Object $ready
Write-JsonFile -Path (Join-Path $out "02_ui_health.json") -Object ([ordered]@{
    status_code = [int]$uiCheck.StatusCode
    status_description = $uiCheck.StatusDescription
    ui_url = $UiUrl
})

$token = ""
$tokenClaims = $null
$queryResults = @{}
$authSummary = [ordered]@{
    token_acquired = $false
    token_len = 0
    username = $Username
    email = $null
    preferred_username = $null
    aud = $null
    iss = $null
    groups = @()
}

if (-not [string]::IsNullOrWhiteSpace($Username) -and -not [string]::IsNullOrWhiteSpace($Password)) {
    $token = Get-KeycloakToken -TokenUrlArg $TokenUrl -ClientIdArg $ClientId -UsernameArg $Username -PasswordArg $Password
    $tokenClaims = Get-TokenClaims -Token $token
    $authSummary.token_acquired = (-not [string]::IsNullOrWhiteSpace($token))
    $authSummary.token_len = $token.Length
    if ($null -ne $tokenClaims) {
        $authSummary.email = $tokenClaims.email
        $authSummary.preferred_username = $tokenClaims.preferred_username
        $authSummary.aud = $tokenClaims.aud
        $authSummary.iss = $tokenClaims.iss
        $authSummary.groups = if (($tokenClaims.PSObject.Properties.Name -contains "groups") -and $null -ne $tokenClaims.groups) { @($tokenClaims.groups) } else { @() }
    }
    Write-JsonFile -Path (Join-Path $out "03_token_claims.json") -Object $tokenClaims
}

Write-JsonFile -Path (Join-Path $out "03_auth_summary.json") -Object $authSummary

if (-not [string]::IsNullOrWhiteSpace($token)) {
    $headers = @{
        Authorization = "Bearer $token"
        "Content-Type" = "application/json"
    }

    $driveBody = @{
        query = $DriveDocQuery
        mode = "qa"
        retrieval_mode = "rag"
        include_images = $false
    } | ConvertTo-Json -Depth 8

    $tabularBody = @{
        query = $TabularQuery
        mode = "qa"
        retrieval_mode = "rag"
        include_images = $false
    } | ConvertTo-Json -Depth 8

    $driveResp = Invoke-RestMethod -Method Post -Uri ("{0}/query" -f $ApiUrl.TrimEnd('/')) -Headers $headers -Body $driveBody -TimeoutSec 300
    $tabularResp = Invoke-RestMethod -Method Post -Uri ("{0}/query" -f $ApiUrl.TrimEnd('/')) -Headers $headers -Body $tabularBody -TimeoutSec 300

    Write-JsonFile -Path (Join-Path $out "04_drive_query.json") -Object $driveResp
    Write-JsonFile -Path (Join-Path $out "05_tabular_query.json") -Object $tabularResp

    $queryResults.drive = [ordered]@{
        refusal_reason = $driveResp.refusal_reason
        citation_count = @($driveResp.citations).Count
        allow = $driveResp.policy_decision.allow
        policy_reason = $driveResp.policy_decision.reason
        answer_preview = Truncate-Text -Text $driveResp.answer
    }
    $queryResults.tabular = [ordered]@{
        refusal_reason = $tabularResp.refusal_reason
        citation_count = @($tabularResp.citations).Count
        allow = $tabularResp.policy_decision.allow
        policy_reason = $tabularResp.policy_decision.reason
        answer_preview = Truncate-Text -Text $tabularResp.answer
    }
}

$uiInfo = [ordered]@{
    ui_url = $UiUrl
    api_url = $ApiUrl
    expected_navigation = @("Workspace", "Ingestion")
    expected_generation_controls = @("Model", "Temperature", "Top-p", "Max tokens")
}
Write-JsonFile -Path (Join-Path $out "06_ui_expectations.json") -Object $uiInfo

$ollamaModels = Run-And-Capture `
    -Name "ollama list" `
    -OutputFile (Join-Path $out "08_ollama_list.txt") `
    -Script {
        docker compose exec ollama ollama list
    }

$ollamaAccessDenied = Test-AccessDeniedDockerCapture -Path $ollamaModels.file
if ($ollamaAccessDenied) {
    $warnings.Add("ollama list could not access Docker Desktop pipe from this shell; model availability is inferred from app runtime rather than this capture.")
}

$deployManifest = [ordered]@{
    source_of_truth = "local"
    generated_at = (Get-Date).ToString("o")
    git = [ordered]@{
        commit = $gitCommit
        branch = $gitBranch
        working_tree_clean = ($gitStatus.Count -eq 0)
    }
    deploy = [ordered]@{
        expected_vm_checkout = $gitCommit
        expected_services = @("api", "ui", "qdrant", "postgres", "keycloak", "opa", "ollama")
        validate_terminal_first = $true
        validate_ui_second = $true
    }
    settings = $deploySettings
    ui = $uiInfo
    ollama = [ordered]@{
        list_ok = $ollamaModels.ok
        list_file = $ollamaModels.file
    }
}
Write-JsonFile -Path (Join-Path $out "09_deploy_manifest.json") -Object $deployManifest

$phase5Capture = Run-And-Capture `
    -Name "capture phase 5 artifacts" `
    -OutputFile (Join-Path $out "07_capture_phase5_stdout.txt") `
    -Script {
        powershell -ExecutionPolicy Bypass -File scripts/capture_phase5_artifacts.ps1 `
            -ApiUrl $ApiUrl `
            -TokenUrl $TokenUrl `
            -ClientId $ClientId `
            -Username $Username `
            -Password $Password `
            -LoadRequests 10 `
            -LoadConcurrency 1 `
            -MaxFailureRate 0.20
    }

$phase5Stdout = ""
$phase5StdoutPath = Join-Path $out "07_capture_phase5_stdout.txt"
if (Test-Path $phase5StdoutPath) {
    $phase5Stdout = Get-Content $phase5StdoutPath -Raw
}
$phase5OutputDir = $null
if ($phase5Stdout -match "Capture completed:\s*(.+)") {
    $phase5OutputDir = $Matches[1].Trim()
}
$phase5EffectiveOk = $phase5Capture.ok -or (-not [string]::IsNullOrWhiteSpace($phase5OutputDir))
$baselineReady = (
    $live.status -eq "ok" -and
    $ready.status -eq "ok" -and
    $uiCheck.StatusCode -eq 200 -and
    (-not [string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($Username)) -and
    ($queryResults.Count -eq 0 -or (
        $queryResults.drive.allow -eq $true -and
        $queryResults.drive.citation_count -ge 1 -and
        $queryResults.tabular.allow -eq $true -and
        $queryResults.tabular.citation_count -ge 1
    )) -and
    $phase5EffectiveOk
)

$summary = [ordered]@{
    timestamp = $ts
    output_dir = (Resolve-Path $out).Path
    git_commit = $gitCommit
    git_branch = $gitBranch
    health = [ordered]@{
        liveness = $live.status
        readiness = $ready.status
        ui_status_code = [int]$uiCheck.StatusCode
        metrics_file = $metricsPath
    }
    compose = [ordered]@{
        ps_ok = $composePs.ok
        config_ok = $composeConfig.ok
        images_ok = $composeImages.ok
        ps_access_denied_warning = $composePsAccessDenied
        images_access_denied_warning = $composeImagesAccessDenied
    }
    auth = $authSummary
    query_checks = $queryResults
    baseline_ready = $baselineReady
    warnings = @($warnings)
    deploy_manifest_file = "09_deploy_manifest.json"
    phase5_capture = [ordered]@{
        ok = $phase5EffectiveOk
        exit_code = $phase5Capture.exit_code
        stdout_file = $phase5Capture.file
        output_dir = $phase5OutputDir
    }
    files = @(
        "00_git_commit.txt",
        "00_git_branch.txt",
        "00_git_status.txt",
        "01_compose_ps.txt",
        "01_compose_config.txt",
        "01_compose_images.txt",
        "02_health_liveness.json",
        "02_health_readiness.json",
        "02_ui_health.json",
        "02_metrics.prom",
        "03_auth_summary.json",
        "03_token_claims.json",
        "04_drive_query.json",
        "05_tabular_query.json",
        "06_ui_expectations.json",
        "07_capture_phase5_stdout.txt",
        "08_ollama_list.txt",
        "09_deploy_manifest.json"
    )
}
Write-JsonFile -Path (Join-Path $out "summary.json") -Object $summary

Write-Host "Local baseline captured at: $out"
if ($live.status -ne "ok" -or $ready.status -ne "ok") {
    exit 1
}
if (-not $composeConfig.ok) {
    exit 1
}
if (-not [string]::IsNullOrWhiteSpace($Username) -and -not $authSummary.token_acquired) {
    exit 1
}
if (-not $baselineReady) {
    exit 1
}
