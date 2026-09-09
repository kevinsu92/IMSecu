# -*- coding: utf-8 -*-
"""IMRL 시간 트리거 작업들에 '작업 실행을 위해 절전 모드 해제' 를 켠다.
execute.py --run-tool wake_to_run 으로 승격돼서 돈다.

24시간 운영의 전제는 PC 가 깨어 있는 것이다. 절전을 끄는 것(사람이 전원 설정에서)이
1순위이고, 이것은 2순위 안전망이다 — 절전에 들어가 있어도 08:30(감시자)·15:05(계획)·
15:10(매도)·15:21(매수)·16:00(리포트)·매시간(헬스체크) 에 PC 를 깨운다.
완전히 꺼진 PC 는 어떤 설정으로도 깨우지 못한다.
로그온 트리거뿐인 IMRL_Control 과 수동 실행용 IMRL_Execute 는 대상이 아니다.
"""
import subprocess, sys

PS = r"""
$svc = New-Object -ComObject Schedule.Service; $svc.Connect()
$f = $svc.GetFolder('\')
$names = @($f.GetTasks(0) | Where-Object { $_.Name -like 'IMRL_*' -and $_.Name -ne 'IMRL_Control' -and $_.Name -ne 'IMRL_Execute' } | ForEach-Object { $_.Name })
$done = @()
foreach ($n in $names) {
  try {
    $t = $f.GetTask($n); $d = $t.Definition
    $d.Settings.WakeToRun = $true
    $lt = $d.Principal.LogonType; if ($lt -eq 0 -or $lt -eq 1) { $lt = 3 }
    $f.RegisterTaskDefinition($n, $d, 4, $null, $null, $lt) | Out-Null
    $done += ($n + '=' + $f.GetTask($n).Definition.Settings.WakeToRun)
  } catch { $done += ($n + '=ERR ' + $_.Exception.Message) }
}
'WakeToRun ' + ($done -join ' ')
"""


def main() -> int:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "").strip(); err = (r.stderr or "").strip()
    print(out or "(stdout 없음)")
    if err:
        print("stderr:", err[:400])
    return 0 if out.startswith("WakeToRun") and "False" not in out and "ERR" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
