param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = 'Stop'
$rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
$names = @('python.exe', 'pythonw.exe')
$processes = @()

try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -in $names -and
        $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)
    })
}
catch {
    $processes = @(Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

$ids = @($processes | ForEach-Object {
    if ($_.ProcessId) { $_.ProcessId } else { $_.Id }
} | Select-Object -Unique)

foreach ($processId in $ids) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds(10)
do {
    $remaining = @($ids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if (-not $remaining) { exit 0 }
    Start-Sleep -Milliseconds 200
} while ((Get-Date) -lt $deadline)

Write-Error "应用进程仍在运行：$($remaining -join ', ')"
exit 1
