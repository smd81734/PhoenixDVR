# All storage commands are mocked; this suite never accesses physical disks.
$ErrorActionPreference='Stop'
$recipe=Join-Path $PSScriptRoot '..\src\format-usb.ps1'
$global:passed=0
$global:results=@()
function Assert($value,$message) {if(-not $value){throw $message}}
function Get-CimInstance {param($ClassName,$Filter,$OperationTimeoutSec)
 [pscustomobject]@{InterfaceType='USB';PNPDeviceID=$global:identity;Size=$global:actualSize}
}
function Get-Disk {param($Number)
 Assert ($Number -eq 99) 'Unexpected disk number'
 [pscustomobject]@{Number=99;BusType='USB';IsBoot=$global:boot;IsSystem=$global:system;IsReadOnly=$false;IsOffline=$false;PartitionStyle=$global:style;LargestFreeExtent=($global:actualSize-1MB)}
}
function Clear-Disk {param($Number,[switch]$RemoveData,[switch]$RemoveOEM,$Confirm)
 Assert ($Number -eq 99) 'Unexpected clear target';$global:clears++;$global:style=$global:afterClear
}
function Initialize-Disk {param($Number,$PartitionStyle,$ErrorAction)
 $global:inits++;$global:style='MBR';if($global:race){throw 'The disk has already been initialized.'}
}
function Set-Disk {param($Number,$PartitionStyle,$ErrorAction)
 $global:converts++;$global:style=$PartitionStyle
}
function Get-Partition {param($DiskNumber,$ErrorAction)}
function New-Partition {param($DiskNumber,$Size,[switch]$AssignDriveLetter)
 Assert ($global:style -eq 'MBR') 'Partitioning without MBR';$global:creates++;$global:partitionSize=$Size;[pscustomobject]@{DriveLetter='Z'}
}
function Format-Volume {param([Parameter(ValueFromPipeline=$true)]$InputObject,$FileSystem,$NewFileSystemLabel,$Confirm)
 process{$global:formats++;if($global:failFormat){throw 'Synthetic format failure'};[pscustomobject]@{FileSystem=$FileSystem;DriveLetter='Z'}}
}
function Case($name,$setup,$verify,$expectedError='') {
 $global:identity='FAKE-USB';$global:actualSize=16GB;$global:boot=$false;$global:system=$false;$global:style='MBR';$global:afterClear='MBR';$global:race=$false;$global:failFormat=$false
 $global:clears=0;$global:inits=0;$global:converts=0;$global:creates=0;$global:formats=0;$global:partitionSize=0
 $s=[pscustomobject]@{Number=99;UniqueId='FAKE-USB';Size=16GB}
 . $setup
 $failure='';try{$output=& $recipe}catch{$failure=$_.Exception.Message}
 if($expectedError){Assert ($failure -like $expectedError) ('Wrong error in '+$name+': '+$failure)}else{Assert ($failure -eq '') ($name+': '+$failure);Assert ($output -eq 'Z') 'Wrong formatted drive';Assert ($global:clears -eq 1 -and $global:creates -eq 1 -and $global:formats -eq 1) 'Incorrect write sequence'}
 . $verify
 $global:passed++;$global:results+=$name
}
Case 'Retained MBR skips initialization' {} {Assert ($global:inits -eq 0) 'Reinitialized MBR'}
Case 'RAW initializes once' {$global:afterClear='RAW'} {Assert ($global:inits -eq 1) 'Initialization missing'}
Case 'Auto-initialization race accepted only after MBR readback' {$global:afterClear='RAW';$global:race=$true} {Assert ($global:inits -eq 1) 'Race not exercised'}
Case 'GPT converted to MBR' {$global:afterClear='GPT'} {Assert ($global:converts -eq 1) 'Conversion missing'}
Case '64 GiB media creates bounded FAT32 partition' {$s.Size=64GB;$global:actualSize=64GB} {Assert ($global:partitionSize -eq (32GB-1MB)) 'Partition too large'}
Case 'Oversized snapshot refused before clear' {$s.Size=64GB+1;$global:actualSize=$s.Size} {Assert ($global:clears -eq 0) 'Oversized disk cleared'} '*above 64 GiB*'
Case 'Identity swap refused before clear' {$global:identity='OTHER-USB'} {Assert ($global:clears -eq 0) 'Wrong disk cleared'} '*identity or size changed*'
Case 'Boot disk refused before clear' {$global:boot=$true} {Assert ($global:clears -eq 0) 'Boot disk cleared'} '*eligible USB data disk*'
Case 'System disk refused before clear' {$global:system=$true} {Assert ($global:clears -eq 0) 'System disk cleared'} '*eligible USB data disk*'
Case 'Size change refused before clear' {$global:actualSize=128GB} {Assert ($global:clears -eq 0) 'Changed disk cleared'} '*identity or size changed*'
Case 'Formatting failure is reported' {$global:failFormat=$true} {Assert ($global:formats -eq 1) 'Failure not exercised'} '*Synthetic format failure*'
@{passed=$global:passed;tests=$global:results;physical_disk_access=$false}|ConvertTo-Json -Depth 4|Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot '..\build\format-tests.json')
Write-Output "Passed $global:passed simulated USB formatting checks"
