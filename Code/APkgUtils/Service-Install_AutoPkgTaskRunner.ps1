$nssm = (Get-Command .\nssm.exe).Source
$serviceName = 'AutoPkgTaskRunner'
$powershell = (Get-Command powershell).Source
$scriptPath = 'C:\Tools\AutoPKG\APkgTools\AutoPkgTaskRunner.ps1'
$arguments = '-ExecutionPolicy Bypass -NoProfile -File "{0}"' -f $scriptPath
& $nssm install $serviceName $powershell $arguments
& $nssm status $serviceName
Start-Service $serviceName
Get-Service $serviceName