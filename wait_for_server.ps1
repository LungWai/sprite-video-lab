[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][int]$Port,
    [int]$TimeoutSeconds = 30,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$normalizedHost = $HostName.Trim()
if ($normalizedHost.StartsWith("[") -and $normalizedHost.EndsWith("]")) {
    $normalizedHost = $normalizedHost.Substring(1, $normalizedHost.Length - 2)
}
$probeHost = switch ($normalizedHost) {
    "0.0.0.0" { "127.0.0.1" }
    "::" { "::1" }
    default { $normalizedHost }
}
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$hostForUrl = if ($probeHost.Contains(":")) { "[$probeHost]" } else { $probeHost }
$uri = "http://${hostForUrl}:$Port/api/app-version"

do {
    try {
        $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            if ($OpenBrowser) {
                try {
                    Start-Process -FilePath $uri -ErrorAction Stop
                } catch {
                    Write-Host "Sprite Video Lab is ready, but the browser could not be opened: $($_.Exception.Message)" -ForegroundColor Red
                    exit 1
                }
            }
            exit 0
        }
    } catch {
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

Write-Host "Sprite Video Lab did not become ready at $uri within $TimeoutSeconds seconds." -ForegroundColor Red
exit 1
