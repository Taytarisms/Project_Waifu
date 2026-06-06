param(
    [Parameter(Mandatory = $true)]
    [string]$LogPath
)

$ErrorActionPreference = "Continue"

function Write-SetupLog {
    param([string]$Message)

    Write-Host $Message
    Add-Content -Path $LogPath -Value $Message -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Invoke-LoggedNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-SetupLog ("Running: {0} {1}" -f $FilePath, ($Arguments -join " "))
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Write-Host $line
        Add-Content -Path $LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    if ($null -ne $LASTEXITCODE) {
        return [int]$LASTEXITCODE
    }
    return 0
}

function Get-GitInstallerAsset {
    $releaseUrl = "https://api.github.com/repos/git-for-windows/git/releases/latest"
    $headers = @{ "User-Agent" = "AI Companion Setup" }
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers -UseBasicParsing
    $isArm64 = $env:PROCESSOR_ARCHITECTURE -eq "ARM64"
    $pattern = if ($isArm64) { "^Git-.*-arm64\.exe$" } else { "^Git-.*-64-bit\.exe$" }
    $asset = $release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1

    if ($null -eq $asset -and $isArm64) {
        $asset = $release.assets | Where-Object { $_.name -match "^Git-.*-64-bit\.exe$" } | Select-Object -First 1
    }

    return $asset
}

Write-SetupLog "Git install helper started."

$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($null -ne $winget) {
    $wingetExit = Invoke-LoggedNative -FilePath $winget.Source -Arguments @(
        "install",
        "--id", "Git.Git",
        "-e",
        "--source", "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--silent"
    )

    if ($wingetExit -eq 0) {
        Write-SetupLog "Git install completed with winget."
        exit 0
    }

    Write-SetupLog "winget returned $wingetExit. Trying the official Git for Windows release installer."
}
else {
    Write-SetupLog "winget was not found. Trying the official Git for Windows release installer."
}

try {
    $asset = Get-GitInstallerAsset
    if ($null -eq $asset) {
        throw "Could not find a Git for Windows installer asset in the latest release."
    }

    $installerPath = Join-Path $env:TEMP $asset.name
    Write-SetupLog ("Downloading {0} from official Git for Windows releases." -f $asset.name)
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $installerPath -UseBasicParsing

    $installerExit = Invoke-LoggedNative -FilePath $installerPath -Arguments @(
        "/VERYSILENT",
        "/NORESTART",
        "/NOCANCEL",
        "/SP-",
        "/SUPPRESSMSGBOXES"
    )

    if ($installerExit -eq 0 -or $installerExit -eq 3010) {
        Write-SetupLog "Git install completed with the official installer."
        exit 0
    }

    Write-SetupLog "Git installer returned $installerExit."
    exit $installerExit
}
catch {
    Write-SetupLog ("ERROR: Git install helper failed: {0}" -f $_.Exception.Message)
    exit 1
}
