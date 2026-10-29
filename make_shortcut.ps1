$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$desktop\MRidge.lnk")
$lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
$lnk.Arguments = '"' + "$here\start_hidden.vbs" + '"'
$lnk.WorkingDirectory = $here
$lnk.IconLocation = "$here\mridge.ico"
$lnk.Description = "MRidge stock chat"
$lnk.Save()
Write-Host "Done. Look for MRidge on your desktop."
