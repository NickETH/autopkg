Param(
    [Parameter(Mandatory=$True)]
    [String]
    $AutoPKGPath,

    [Parameter(Mandatory=$False)]
    [string]
    $AutopkgTaskrunnerName = "AutopkgTaskrunner",

    [Parameter(Mandatory=$False)]
    [string]
    $logfile
    )
$Logfile = "C:\Tools\AutoPKG\log\AutopkgTaskrunner.log"
# $AutoPKGPath = 'C:\Tools\AutoPKG'
# $AutopkgTaskrunnerName = "AutopkgTaskrunner"
if(!$Logfile)
   {
      $logpath = (join-path $AutoPKGPath "log")
      $logfile = (join-path $logpath ($AutopkgTaskrunnerName + ".log"))
   }
#$AutoPKGUtilsPath = Split-Path $script:MyInvocation.MyCommand.Path

Function Write-Log {
    [CmdletBinding()]
    Param(
    [Parameter(Mandatory=$False)]
    [ValidateSet("INFO","WARN","ERROR","FATAL","DEBUG")]
    [String]
    $Level = "INFO",

    [Parameter(Mandatory=$True)]
    [string]
    $Message,

    [Parameter(Mandatory=$False)]
    [string]
    $logfile
    )

    $Stamp = (Get-Date).toString("yyyy/MM/dd HH:mm:ss")
    $Line = "$Stamp $Level $Message"
    If($logfile) {
        Add-Content $logfile -Value $Line
    }
    Else {
        Write-Output $Line
    }
}

$APfsWatcherDirPath = (join-path $AutoPKGPath "APkgUtils")
$APfsWatcherFilter = 'AutopkgTaskrunnerCMD.apkcmd'
$APfsWatcherFullPath = (join-path $APfsWatcherDirPath $APfsWatcherFilter)
#Create a hashtable for our FileSystemWatcher object properties
$param = @{
    Path = $APfsWatcherDirPath;
    Filter = $APfsWatcherFilter;
    IncludeSubDirectories = $False;
    EnableRaisingEvents = $true
}

#Create the .NET FileSystemWatcher object and passing our properties
[IO.FileSystemWatcher]$scriptAccessWatcher = New-Object IO.FileSystemWatcher -property $param


#Register the object to the Changed Event
Register-ObjectEvent $scriptAccessWatcher "Changed" -SourceIdentifier 'FSChanged' {$global:FileChanged = $true} > $null

# Set the split parameters up
$cmdarg_seprtr = "~~~"
$split_optn = [System.StringSplitOptions]::RemoveEmptyEntries

Write-Log "INFO" "AutopkgTaskrunner started" $Logfile

try {
   do {
      $global:FileChanged = $false # dirty... any better suggestions?
      # Write-Log "INFO" "Start Loop" $Logfile

      while ($global:FileChanged -eq $false){
         # We need this to block the IO thread until there is something to run 
         # so the script doesn't finish. If we call the action directly from 
         # the event it won't be able to write to the console
         # the time could be lowered to 100ms if we loose an event. 1s has worked perfect yet.
         Start-Sleep -Milliseconds 1000
        }

      $valueComnd = Get-Content $APfsWatcherFullPath
      $valueComndSplit = $valueComnd.Split($cmdarg_seprtr, $split_optn)
      $ComndFile = "`"{0}`"" -f $valueComndSplit[0]
      Write-Log "INFO" $valueComnd $Logfile

      if($valueComndSplit[1])
      {
         $ComndArgs = "{0}" -f $valueComndSplit[1]
         Start-Process -FilePath $ComndFile -ArgumentList $ComndArgs -Verb RunAs -Wait
      }
      else
      {
         Start-Process -FilePath $ComndFile -Verb RunAs -Wait
      }
      [GC]::Collect()

      # give the control back to the AutoPkg processor
      Set-Content $APfsWatcherFullPath -Value "0"

      # reset and go again
      $global:FileChanged = $false
   } while ($true)
}finally {
   # unregister
   Unregister-Event -SourceIdentifier 'FSChanged'
   
   # dispose
   $scriptAccessWatcher.Dispose()
   $global:FileChanged = $null
   Write-Host "`r`nEvent Handler removed."
   Write-Log "INFO" "AutopkgTaskrunner stopped" $Logfile
}