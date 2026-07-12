# Перепривязка NCM-драйвера GoPro без участия человека.
# ЗАПУСКАЕТСЯ ЭЛЕВИРОВАННО (через разовую scheduled task GoProNcmRebind — см. setup ниже).
# Логика = то, что оператор делал руками (Device Manager -> UsbNcm Host Device),
# но headless: disable -> enable ре-применяет уже сохранённую ассоциацию драйвера;
# если её нет — форс-инсталл инбокс usbncm.inf на устройство по instance-id.

$log = "C:\Users\andri\AppData\Local\Temp\ncm_rebind.log"
"[$(Get-Date -Format o)] rebind start" | Out-File $log -Append -Encoding utf8

$dev = Get-PnpDevice -Class Net -EA SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_2672' }
if (-not $dev) {
    $dev = Get-PnpDevice -EA SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_2672&PID_....&MI_00' }
}
if (-not $dev) { "no VID_2672 MI_00 device" | Out-File $log -Append -Encoding utf8; exit 2 }

$id = $dev.InstanceId
"device: $id status=$($dev.Status)" | Out-File $log -Append -Encoding utf8

# 1) disable/enable — ре-применяет сохранённую ассоциацию (operator bound it once)
try {
    Disable-PnpDevice -InstanceId $id -Confirm:$false -EA Stop
    Start-Sleep 2
    Enable-PnpDevice -InstanceId $id -Confirm:$false -EA Stop
    Start-Sleep 3
    "disable/enable done" | Out-File $log -Append -Encoding utf8
} catch { "disable/enable failed: $_" | Out-File $log -Append -Encoding utf8 }

# 2) если всё ещё Error — форс-инсталл инбокс NCM по instance-id через pnputil
$dev = Get-PnpDevice -InstanceId $id
if ($dev.Status -ne 'OK') {
    "still $($dev.Status) — forcing usbncm.inf" | Out-File $log -Append -Encoding utf8
    pnputil /add-driver C:\Windows\INF\usbncm.inf /install 2>&1 | Out-File $log -Append -Encoding utf8
    pnputil /restart-device "$id" 2>&1 | Out-File $log -Append -Encoding utf8
    Start-Sleep 3
    $dev = Get-PnpDevice -InstanceId $id
}
"final status: $($dev.Status)" | Out-File $log -Append -Encoding utf8
if ($dev.Status -eq 'OK') { exit 0 } else { exit 1 }
