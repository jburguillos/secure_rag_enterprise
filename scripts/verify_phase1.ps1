param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$DriveFolderId = "",
    [string]$DriveEmail = "",
    [string]$DriveDomain = "",
    [string[]]$DriveGroups = @(),
    [switch]$SkipDrive
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

if ([string]::IsNullOrWhiteSpace($DriveFolderId)) {
    $DriveFolderId = $env:DRIVE_FOLDER_ID
}
if ([string]::IsNullOrWhiteSpace($DriveEmail)) {
    $DriveEmail = $env:VERIFY_DRIVE_EMAIL
}
if ([string]::IsNullOrWhiteSpace($DriveDomain)) {
    $DriveDomain = $env:VERIFY_DRIVE_DOMAIN
}
if ($DriveGroups.Count -eq 0 -and -not [string]::IsNullOrWhiteSpace($env:VERIFY_DRIVE_GROUPS)) {
    $DriveGroups = @($env:VERIFY_DRIVE_GROUPS -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$checks = New-Object System.Collections.Generic.List[object]

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

function Invoke-JsonPost {
    param(
        [string]$Path,
        [hashtable]$Body,
        [int]$TimeoutSec = 600
    )
    $uri = "{0}{1}" -f $ApiUrl.TrimEnd('/'), $Path
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json" -Body $json -TimeoutSec $TimeoutSec
}

function Extract-DocIds {
    param($Response)
    if (-not $Response -or -not $Response.citations) { return @() }
    return @($Response.citations | ForEach-Object { $_.doc_id })
}

Write-Host "[1/6] Health checks"
try {
    $live = Invoke-RestMethod -Method Get -Uri ("{0}/health/liveness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
    $ready = Invoke-RestMethod -Method Get -Uri ("{0}/health/readiness" -f $ApiUrl.TrimEnd('/')) -TimeoutSec 30
    Add-Check "Liveness endpoint" ($live.status -eq "ok") ("status={0}" -f $live.status)
    Add-Check "Readiness endpoint" ($ready.status -eq "ok") ("status={0}" -f $ready.status)
} catch {
    Add-Check "API health" $false (Get-HttpErrorDetails $_)
}

Write-Host "[2/6] Ingest local sample docs"
try {
    $localIngest = Invoke-JsonPost -Path "/ingest/local" -Body @{
        path = "./tests/data/sample_docs"
        acl_sidecar_path = "./tests/data/sample_docs/acl_map.yaml"
        dry_run = $false
        dataset_source = "local_folder"
    }
    $localPass = (($localIngest.added + $localIngest.updated) -ge 1)
    Add-Check "Local ingest" $localPass ("added={0} updated={1} skipped={2}" -f $localIngest.added, $localIngest.updated, $localIngest.skipped)
} catch {
    Add-Check "Local ingest" $false (Get-HttpErrorDetails $_)
}

Write-Host "[3/6] ACL regression (HR vs Finance)"
try {
    $hrResp = Invoke-JsonPost -Path "/query" -Body @{
        query = "Summarize available local documents in two bullets with citations."
        mode = "summarize"
        top_k = 12
        include_images = $false
        filters = @{ sources = @("local_folder") }
        user_context = @{
            email = "hr.user@example.com"
            domain = "example.com"
            groups = @("HR")
        }
    }
    $hrDocs = Extract-DocIds $hrResp
    $hrPass = ($hrResp.citations.Count -ge 1) -and ($hrDocs -contains "hr_only.txt") -and -not ($hrDocs -contains "finance_only.txt")
    Add-Check "HR sees HR+public, not Finance" $hrPass ("cited={0}" -f (($hrDocs | Sort-Object -Unique) -join ", "))

    $finResp = Invoke-JsonPost -Path "/query" -Body @{
        query = "Summarize available local documents in two bullets with citations."
        mode = "summarize"
        top_k = 12
        include_images = $false
        filters = @{ sources = @("local_folder") }
        user_context = @{
            email = "finance.user@example.com"
            domain = "example.com"
            groups = @("Finance")
        }
    }
    $finDocs = Extract-DocIds $finResp
    $finPass = ($finResp.citations.Count -ge 1) -and ($finDocs -contains "finance_only.txt") -and -not ($finDocs -contains "hr_only.txt")
    Add-Check "Finance sees Finance+public, not HR" $finPass ("cited={0}" -f (($finDocs | Sort-Object -Unique) -join ", "))
} catch {
    Add-Check "ACL regression" $false (Get-HttpErrorDetails $_)
}

Write-Host "[4/6] Google Drive ingest (optional)"
if ($SkipDrive -or [string]::IsNullOrWhiteSpace($DriveFolderId)) {
    Add-Check "Drive ingest" $true "skipped (use -DriveFolderId <id> or env DRIVE_FOLDER_ID)"
} else {
    try {
        $driveIngest = Invoke-JsonPost -Path "/ingest/gdrive" -Body @{
            folder_id = $DriveFolderId
            auth_mode = "oauth"
            dry_run = $false
            dataset_source = "google_drive"
        }
        Add-Check "Drive ingest" $true ("added={0} updated={1} skipped={2}" -f $driveIngest.added, $driveIngest.updated, $driveIngest.skipped)
    } catch {
        Add-Check "Drive ingest" $false (Get-HttpErrorDetails $_)
    }
}

Write-Host "[5/6] Drive query check (optional)"
if ($SkipDrive -or [string]::IsNullOrWhiteSpace($DriveFolderId) -or [string]::IsNullOrWhiteSpace($DriveEmail) -or [string]::IsNullOrWhiteSpace($DriveDomain)) {
    Add-Check "Drive query" $true "skipped (set Drive identity: -DriveEmail/-DriveDomain)"
} else {
    try {
        $groups = if ($DriveGroups.Count -gt 0) { @($DriveGroups) } else { @("HR") }
        $driveResp = Invoke-JsonPost -Path "/query" -Body @{
            query = "Summarize Google Drive documents with citations."
            mode = "summarize"
            top_k = 20
            include_images = $false
            filters = @{ sources = @("google_drive") }
            user_context = @{
                email = $DriveEmail
                domain = $DriveDomain
                groups = @($groups)
            }
        }
        $driveWeb = @($driveResp.citations | Where-Object { $_.webViewLink })
        $drivePass = ($driveResp.refusal_reason -eq $null) -and ($driveWeb.Count -ge 1)
        Add-Check "Drive query returns Drive citations" $drivePass ("citations={0}" -f $driveResp.citations.Count)
    } catch {
        Add-Check "Drive query returns Drive citations" $false (Get-HttpErrorDetails $_)
    }
}

Write-Host "[6/6] Results"
$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { $_.status -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Error ("Phase 1 verification failed: {0} checks failed." -f $failed.Count)
    exit 1
}
Write-Host "Phase 1 verification passed." -ForegroundColor Green
