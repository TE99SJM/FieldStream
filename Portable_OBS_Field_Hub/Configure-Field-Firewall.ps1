# Run once in an elevated Windows PowerShell on the hub connected to its field LAN.
# Only this kit's named rules are created or replaced.
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$ip = '192.168.77.10'
$field = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ip | Where-Object { $_.AddressState -eq 'Preferred' })
if ($field.Count -ne 1) { throw "Set the hub field adapter to $ip/24 and connect it first." }
$nic = $field[0].InterfaceAlias
$group = 'Field Hub local camera rig'
foreach ($item in @(@('fieldhub-rtmp', 1935), @('fieldhub-panel', 8088))) {
    $name, $port = $item
    $existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if ($existing -and $existing.Group -ne $group) { throw "Firewall rule name already in use: $name" }
    if ($existing) { Remove-NetFirewallRule -Name $name }
    New-NetFirewallRule -Name $name -DisplayName $name -Group $group -InterfaceAlias $nic -Profile Any -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $ip -LocalPort $port -RemoteAddress '192.168.77.0/24' | Out-Null
}
$name = 'fieldhub-obs-ws-lan-block'
$existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Group -ne $group) { throw "Firewall rule name already in use: $name" }
if ($existing) { Remove-NetFirewallRule -Name $name }
New-NetFirewallRule -Name $name -DisplayName $name -Group $group -InterfaceAlias $nic -Profile Any -Direction Inbound -Action Block -Protocol TCP -LocalPort 4455 | Out-Null
Write-Host "Field LAN rules active on $nic. Cameras use TCP 1935 and controllers use TCP 8088."
