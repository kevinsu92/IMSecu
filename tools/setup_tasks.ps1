# NOTE: 이 파일은 반드시 UTF-8 with BOM 으로 저장해야 한다.
# Windows PowerShell 5.1 은 BOM 이 없는 UTF-8 .ps1 을 시스템 ANSI(CP949)로 읽어
# 한글이 깨지고 문자열 종결자 오류가 난다. (2026-09-05 실제 발생)
# 작업 스케줄러 등록.
#
# HTS 가 관리자 권한으로 실행되므로 자동화 스크립트도 관리자 권한이어야 한다.
# 매번 UAC 를 누르는 것은 무인 운영과 맞지 않으므로, "가장 높은 수준의 권한으로
# 실행" 작업으로 등록해 둔다. 이 스크립트를 한 번만 관리자로 실행하면 이후
# schtasks /run 으로 UAC 없이 호출할 수 있다.

$ErrorActionPreference = "Stop"
$Root   = (Resolve-Path "$PSScriptRoot\..").Path
$Python = (Get-Command python).Source
$User   = "$env:USERDOMAIN\$env:USERNAME"

Write-Output "python: $Python"
Write-Output "user:   $User"
Write-Output ""

function New-ImrlTask {
    param(
        [string]$Name,
        [string]$Script,
        # 주의: 파라미터 이름을 $Args 로 쓰면 안 된다. PowerShell 자동 변수와
        # 충돌해 **값이 바인딩되지 않고 조용히 빈 문자열이 된다.**
        # 실제로 그 상태로 등록돼 IMRL_AutoTrade 가 --live 없이(=드라이런) 돌 뻔했다.
        [string]$TaskArgs,
        $Trigger,
        [string]$Desc,
        # 실행 시간 제한(분). 다음 의존 작업이 도는 간격보다 짧아야 한다.
        # 20분 고정이던 때, 로그인 안 된 HTS 에서 헬스체크가 멈추자 이후 실행이
        # 전부 "이미 실행 중" 으로 거부됐다. 매도(15:10)가 멈추면 재시도(15:13)가
        # 같은 방식으로 막힌다 - 멈춘 작업 하나가 그날 매매를 통째로 삼킨다.
        [int]$LimitMinutes = 5,
        # 감시자처럼 하루 종일 살아 있어야 하는 작업용.
        # 놓친 실행을 나중에 시작하고(StartWhenAvailable), 이미 돌고 있으면
        # 새 인스턴스를 무시한다(IgnoreNew). 반복 트리거와 짝이다.
        [switch]$Resident
    )
    $action = New-ScheduledTaskAction -Execute $Python `
        -Argument "`"$Root\$Script`" $TaskArgs" -WorkingDirectory $Root
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
    # WakeToRun: 절전 상태에서도 깨어나 실행한다. 없으면 장중에 실행되지 않는다.
    # StartWhenAvailable 은 끈다 — 놓친 실행을 나중에 뒤늦게 돌리면 장이 끝난 뒤
    # 스테일 주문서로 주문이 나갈 수 있다.
    if ($Resident) {
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -WakeToRun -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes $LimitMinutes) `
            -MultipleInstances IgnoreNew `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    } else {
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -WakeToRun `
            -ExecutionTimeLimit (New-TimeSpan -Minutes $LimitMinutes) `
            -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2)
    }

    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    if ($Trigger) {
        Register-ScheduledTask -TaskName $Name -Action $action -Principal $principal `
            -Settings $settings -Trigger $Trigger -Description $Desc | Out-Null
    } else {
        Register-ScheduledTask -TaskName $Name -Action $action -Principal $principal `
            -Settings $settings -Description $Desc | Out-Null
    }
    Write-Output "등록: $Name"
}

# 대회 시작일. 트리거 StartBoundary 를 여기에 못박는다.
#
# New-ScheduledTaskTrigger -Weekly 는 StartBoundary 를 **오늘**로 잡는다. 그래서
# 09/05(토)에 등록하면 다음 실행이 09/07(월)이 되는데, 대회는 09/08(화) 시작이다.
# 하루 먼저 실주문이 나가면 09/08 시작 시점의 보유가 0 이 아니게 되고, 그러면
# "1일차는 매수만 발생한다"는 전제가 깨져 **체결가 판별 관측을 통째로 잃는다.**
# 그 관측은 소급 취득이 불가능하다.
$CONTEST_START = "2026-09-08"

# 15:05 주문서 생성 (평일)
# 집행을 종가 동시호가(15:20~15:30)로 옮겼다. 09:05 집행은 오버나잇 갭을 놓쳐
# 백테스트 대비 알파의 2/3가 사라진다.
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:05"
New-ImrlTask -Name "IMRL_Plan" -Script "run.py" -TaskArgs "plan" -Trigger $t1 `
    -Desc "iM Rookie League: 주문서 생성 후 텔레그램 전송" `
    -LimitMinutes 4

# 14:50 사전 점검 (평일)
#
# 무인 운영의 실패는 대부분 조용하다. 실제로 HTS 가 "화면잠금" 상태였던 적이 있는데,
# 프로세스도 살아 있고 주문창도 열려 있어 창 열거는 전부 성공하고 캡처만 실패했다.
# 15:21 에 그 상태를 발견하면 고칠 시간이 없다. 30분 전에 미리 확인하고
# 문제가 있으면 텔레그램으로 알린다.
# 08:40 / 12:30 / 14:50 세 번 돈다.
#
# 14:50 한 번만 보면 문제를 집행 10분 전에 알게 되는데, 사용자가 부재 중이면
# 그때는 고칠 수가 없다. 아침(08:40)과 점심(12:30)에도 확인해 두면
# 틈틈이 대응할 여지가 생긴다. 점검은 클릭을 하지 않는 읽기 전용이라
# 장중에 돌아도 매매에 영향이 없다.
# 08:40 부터 14:50 까지 매시간. 원래 3회였는데 두 가지 이유로 늘렸다.
#   (1) 사용자가 08:40 로그인 후 15:10 집행까지 6시간 30분을 무조작으로 둔다.
#       국내 HTS 는 보통 이 구간에서 세션이 끊긴다. 주기적 창 조작이 유휴 타이머를
#       리셋하는지는 UNVERIFIED 지만, 최소한 조기 탐지 간격은 짧아진다.
#   (2) 14:50 에 실패를 알아도 15:05 까지 15분뿐이다.
$hcTriggers = @()
foreach ($h in 8,9,10,11,12,13,14) {
    $at = if ($h -eq 8) { "08:40" } elseif ($h -eq 14) { "14:50" } else { "{0:00}:30" -f $h }
    $hcTriggers += New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START $at"
}
New-ImrlTask -Name "IMRL_HealthCheck" -Script "execute.py" -TaskArgs "--healthcheck" -Trigger $hcTriggers `
    -Desc "iM Rookie League: HTS 상태 점검 (08:40~14:50 매시간, 7회)" `
    -LimitMinutes 3

# 15:10 매도 먼저 (평일) — 연속매매 구간
#
# 종가 동시호가는 주문을 전부 접수한 뒤 15:30 에 한꺼번에 체결한다. 즉 15:21 에
# 매수를 넣는 시점에는 매도가 아직 체결되지 않아 **매도대금이 예수금에 없다.**
# 90% 투자 상태에서 종목을 교체하려면 90% 를 팔고 90% 를 사야 하는데 그 시점
# 현금은 10% 뿐이다 — 실측 계산에서 4,500만원 매수에 3,500만원이 모자랐다.
#
# 그래서 매도는 연속매매에 먼저 내보내 즉시 체결시킨다. 매뉴얼 p13 의 총평가금액
# 산식이 "당일매도금액"을 당일 반영하므로 대금은 같은 날 쓸 수 있다.
# 부수 효과도 있다 — 실측상 동시호가 매도 체결률이 51.8% 라 매도를 빼는 편이 낫다.
$t5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:10"
New-ImrlTask -Name "IMRL_Sell" -Script "execute.py" -TaskArgs "--live --side SELL" -Trigger $t5 `
    -Desc "iM Rookie League: 매도 선집행 (연속매매)" `
    -LimitMinutes 2

# 15:13 / 15:16 매도 재시도.
#
# 재시도가 매수에만 붙어 있었는데 비대칭 비용은 **매도** 쪽에 있다. 매수 실패는
# 그날 현금으로 남고 끝이지만, 매도 실패는 15:21 매수 자금을 없애 리밸런싱 전체를
# 무산시킨다. 분할 조각별 중복 키(execute.order_key)가 들어갔으므로 재시도는
# 이미 전송된 조각을 건너뛰고 남은 조각만 보낸다.
$t8 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:13"
New-ImrlTask -Name "IMRL_SellRetry1" -Script "execute.py" -TaskArgs "--live --side SELL" -Trigger $t8 `
    -Desc "iM Rookie League: 매도 재시도 1 (미전송 조각만)" `
    -LimitMinutes 2

$t9 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:16"
New-ImrlTask -Name "IMRL_SellRetry2" -Script "execute.py" -TaskArgs "--live --side SELL" -Trigger $t9 `
    -Desc "iM Rookie League: 매도 재시도 2 (미전송 조각만)" `
    -LimitMinutes 3


# 15:21 매수 자동 전송 (평일). 휴장일·대회기간 밖에서는 execute.py 가 스스로 거부한다.
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:21"
New-ImrlTask -Name "IMRL_AutoTrade" -Script "execute.py" -TaskArgs "--live --side BUY" -Trigger $t3 `
    -Desc "iM Rookie League: 매수 종가 동시호가 전송" `
    -LimitMinutes 2

# 15:24 / 15:27 매수 재시도 (평일)
#
# 전송 창이 15:21 하나뿐이었다. 공지 팝업 하나로 assert_focus 가 거부하면
# 전량 중단 -> 그날 매매 0 이다. 매매일수 5일·회전율 500% 가 하드요건이라
# 실패일은 곧 상금 리스크다.
# 중복 방지는 submitted_YYYYMMDD.json 이 이미 하므로 재실행이 안전하다.
$t6 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:24"
New-ImrlTask -Name "IMRL_BuyRetry1" -Script "execute.py" -TaskArgs "--live --side BUY" -Trigger $t6 `
    -Desc "iM Rookie League: 매수 재시도 1" `
    -LimitMinutes 2

$t7 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:27"
New-ImrlTask -Name "IMRL_BuyRetry2" -Script "execute.py" -TaskArgs "--live --side BUY" -Trigger $t7 `
    -Desc "iM Rookie League: 매수 재시도 2" `
    -LimitMinutes 5

# 15:40 정합성 대조 (평일)
#
# 주문서·전송기록·체결기록·보유 넷이 갈라지면, 다음 날 없는 포지션을 팔고
# 있는 포지션을 또 산다. 부분체결도 여기서만 드러난다(전송 수량 - 체결 수량).
# 자동 보정은 하지 않는다 - 차이를 보여주고 사람이 확인한다.
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 15:40"
New-ImrlTask -Name "IMRL_Reconcile" -Script "run.py" -TaskArgs "reconcile" -Trigger $t4 `
    -Desc "iM Rookie League: 주문·전송·체결·보유 대조" `
    -LimitMinutes 5

# 16:00 자격요건 리포트 (평일)
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "$CONTEST_START 16:00"
New-ImrlTask -Name "IMRL_Status" -Script "run.py" -TaskArgs "status --push" -Trigger $t2 `
    -Desc "iM Rookie League: 수상 자격 요건 진척도 보고" `
    -LimitMinutes 5

# 08:30 장중 감시자 — 상태 기반 실행
#
# 스케줄러는 시각 기반이라 15:10 에 HTS 가 로그인 전이면 그 단계를 놓친다.
# 사람이 15:25 에 로그인해도 15:21 매수는 이미 지나간 뒤다. 감시자는 계속
# 돌면서 "시각이 지났고 HTS 가 준비됐고 아직 안 했으면" 그때 실행한다.
# 중복은 주문 원장이 막으므로 기존 작업과 함께 돌아도 안전하다.
# 트리거가 셋이다. 하나로는 무인 운영이 안 된다.
#
#   (1) 매일 08:30 시작 + 10분마다 반복
#       감시자가 죽어도 10분 안에 다시 뜬다. 이미 돌고 있으면 IgnoreNew 로
#       새 인스턴스가 무시되므로 중복 실행이 아니다.
#   (2) 로그온 시
#       재부팅(윈도우 업데이트 포함) 후 사람이 로그인하면 그 자리에서 뜬다.
#       주 1회 트리거만 있으면 14시에 재부팅한 날은 그날이 통째로 날아간다.
#   (3) StartWhenAvailable
#       08:30 에 PC 가 꺼져 있었으면 켜진 뒤에 시작한다.
$twDaily = New-ScheduledTaskTrigger -Daily -At "$CONTEST_START 08:30"
$twDaily.Repetition = (New-ScheduledTaskTrigger -Once -At "$CONTEST_START 08:30" `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Hours 8)).Repetition
$twLogon = New-ScheduledTaskTrigger -AtLogOn -User $User

New-ImrlTask -Name "IMRL_Watchdog" -Script "watchdog.py" -TaskArgs "" `
    -Trigger @($twDaily, $twLogon) -Resident `
    -Desc "iM Rookie League: 장중 감시자 - HTS 가 준비되면 밀린 단계를 실행" `
    -LimitMinutes 480

# 주문 입력 (수동 호출 전용, 트리거 없음) — 기본 드라이런
New-ImrlTask -Name "IMRL_Execute" -Script "execute.py" -TaskArgs "" -Trigger $null `
    -Desc "iM Rookie League: 주문서를 HTS 에 입력 (드라이런)" `
    -LimitMinutes 10

# IMRL_ExecuteLive 는 등록하지 않는다. 이미 있으면 지운다.
#
# 트리거가 없어 저절로 돌지는 않았지만 --side 필터가 없어서, 작업 스케줄러에서
# 실행 한 번이면 **매수·매도 주문서 전체가 한꺼번에 나간다.** 매도와 매수를
# 11분 떼어 놓은 설계 전체를 한 번의 오조작으로 무효화할 수 있다.
# 수동 실전송이 필요하면 --side 를 명시해 콘솔에서 직접 실행한다.
Unregister-ScheduledTask -TaskName "IMRL_ExecuteLive" -Confirm:$false -ErrorAction SilentlyContinue
Write-Output "제거: IMRL_ExecuteLive (--side 없는 실전송은 위험하다)"

Write-Output ""
Write-Output "=== 등록된 작업 ==="
Get-ScheduledTask -TaskName "IMRL_*" | Select-Object TaskName, State | Format-Table -AutoSize
Write-Output ""
Write-Output "이후 사용법 (UAC 없이):"
Write-Output "  schtasks /run /tn IMRL_Execute        드라이런"
Write-Output "  python execute.py --live --side SELL   실전송 매도 (수동, --side 필수)"
Write-Output ""
Write-Output "자동 운영 일정 (평일, $CONTEST_START 부터):"
Write-Output "  08:30~16:15  IMRL_Watchdog   장중 감시자 (HTS 준비되면 실행)"
Write-Output "  08:40~14:50 매시간  IMRL_HealthCheck HTS 상태 점검"
Write-Output "  15:05  IMRL_Plan       주문서 생성 + 텔레그램"
Write-Output "  15:10  IMRL_Sell       매도 선집행(연속매매)"
Write-Output "  15:21  IMRL_AutoTrade  매수 종가 동시호가 전송"
Write-Output "  15:13/15:16  IMRL_SellRetry1/2 매도 재시도"
Write-Output "  15:24/15:27  IMRL_BuyRetry1/2  매수 재시도"
Write-Output "  15:40  IMRL_Reconcile  주문·전송·체결·보유 대조"
Write-Output "  16:00  IMRL_Status     자격요건 리포트"
