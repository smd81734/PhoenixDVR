# Embedded in PhoenixSetup.exe. $s is the selected device snapshot decoded by C#.
$diskNumber=[int]$s.Number
$maximum=64GB
if ([long]$s.Size -le 0 -or [long]$s.Size -gt $maximum) { throw 'USB media above 64 GiB are not eligible' }
function Assert-UsbIdentity {
 $physical=Get-CimInstance Win32_DiskDrive -Filter ('Index='+$diskNumber) -OperationTimeoutSec 5
 if ($null -eq $physical -or $physical.InterfaceType -ne 'USB' -or $physical.PNPDeviceID -ne $s.UniqueId -or [long]$physical.Size -ne [long]$s.Size -or [long]$physical.Size -gt $maximum) {throw 'USB identity or size changed'}
 $disk=Get-Disk -Number $diskNumber
 if ($disk.Number -ne $diskNumber -or [string]$disk.BusType -ne 'USB' -or $disk.IsBoot -or $disk.IsSystem) {throw 'Selected disk is not an eligible USB data disk'}
 if ($disk.IsReadOnly -or $disk.IsOffline) {throw 'Selected USB is offline or read-only'}
 return $disk
}
$d=Assert-UsbIdentity
Clear-Disk -Number $diskNumber -RemoveData -RemoveOEM -Confirm:$false
$d=Assert-UsbIdentity
switch ([string]$d.PartitionStyle) {
 'RAW' {
  try {Initialize-Disk -Number $diskNumber -PartitionStyle MBR -ErrorAction Stop | Out-Null}
  catch {
   # Some removable devices are initialized automatically between these commands.
   $d=Assert-UsbIdentity
   if ([string]$d.PartitionStyle -ne 'MBR') {throw}
  }
 }
 'MBR' { } # Clearing a removable device may preserve its initialized MBR state.
 'GPT' {Set-Disk -Number $diskNumber -PartitionStyle MBR -ErrorAction Stop | Out-Null}
 default {throw 'Unsupported partition state after clearing the selected USB'}
}
$d=Assert-UsbIdentity
if ([string]$d.PartitionStyle -ne 'MBR') {throw 'Could not establish an MBR partition table'}
if (@(Get-Partition -DiskNumber $diskNumber -ErrorAction SilentlyContinue).Count -ne 0) {throw 'The cleared USB still reports partitions; refresh and review before retrying'}
$size=[math]::Min([long]$d.LargestFreeExtent,[long](32GB-1MB))
if ($size -lt 256MB) {throw 'USB free extent is too small for the installer'}
$p=New-Partition -DiskNumber $diskNumber -Size $size -AssignDriveLetter
$v=$p | Format-Volume -FileSystem FAT32 -NewFileSystemLabel PHOENIX -Confirm:$false -ErrorAction Stop
if ([string]$v.FileSystem -ne 'FAT32' -or [string]$v.DriveLetter -notmatch '^[D-Z]$') {throw 'FAT32 formatting did not return an eligible drive letter'}
[string]$v.DriveLetter
