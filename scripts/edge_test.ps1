param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('audit', 'benchmark', 'stress', 'report')]
    [string]$Command,
    [ValidateSet('pi3', 'pi4', 'pi5')]
    [string]$Profile = 'pi4',
    [string]$PythonExe = $(if ($env:PYTHON_EXECUTABLE) { $env:PYTHON_EXECUTABLE } else { 'python' })
)
$root = Split-Path -Parent $PSScriptRoot
& $PythonExe "$root/scripts/edge_runner.py" $Command --profile $Profile
if ($LASTEXITCODE -ne 0) {
    throw "Edge runner failed. Supply -PythonExe with a real Python interpreter or set PYTHON_EXECUTABLE."
}
