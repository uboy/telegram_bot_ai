param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "start_stack.py"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "python command not found in PATH."
    exit 127
}

& $pythonCmd.Source $pythonScript @Args
exit $LASTEXITCODE
