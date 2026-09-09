# -*- coding: utf-8 -*-
"""감시자 작업의 실행 시간 제한을 넓힌다. execute.py --run-tool 로 승격돼서 돈다.

감시자는 로그온 때도 뜬다. 06:30 에 로그인하면 8시간 제한이 14:30 에 감시자를 죽여
15:05 창을 못 본다. 스스로 16:15 에 끝나는 프로세스에 제한은 안전장치일 뿐이니
하루를 넉넉히 덮는 값으로 둔다. Set-ScheduledTask 가 조용히 실패해 COM 으로 한다.
"""
import subprocess, sys

PS = r"""
$svc = New-Object -ComObject Schedule.Service; $svc.Connect()
$f = $svc.GetFolder('\'); $t = $f.GetTask('IMRL_Watchdog'); $d = $t.Definition
$d.Settings.ExecutionTimeLimit = 'PT14H'
$d.Settings.StartWhenAvailable = $true
$d.Settings.DisallowStartIfOnBatteries = $false
$d.Settings.StopIfGoingOnBatteries = $false
$f.RegisterTaskDefinition('IMRL_Watchdog', $d, 4, $null, $null, 3) | Out-Null
$t2 = $f.GetTask('IMRL_Watchdog')
'IMRL_Watchdog 제한=' + $t2.Definition.Settings.ExecutionTimeLimit + ' 놓친건=' + $t2.Definition.Settings.StartWhenAvailable + ' 권한=' + $t2.Definition.Principal.RunLevel
"""


def main() -> int:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    out = (r.stdout or "").strip(); err = (r.stderr or "").strip()
    print(out or "(stdout 없음)")
    if err:
        print("stderr:", err[:400])
    return 0 if "PT14H" in out else 1


if __name__ == "__main__":
    sys.exit(main())
