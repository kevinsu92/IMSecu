# -*- coding: utf-8 -*-
"""상황판의 화면. 데이터 수집은 dashboard.py 가 한다.

파일 하나에 데이터까지 박아 넣는다. 서버도 인터넷도 필요 없고, 브라우저로 열기만
하면 된다. 대회 기간 내내 띄워둘 물건이라 의존성을 만들지 않는 편이 낫다.
"""

HTML = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>iM Rookie League 상황판</title>
<style>
:root{
  --bg:#f7f7f5; --card:#fff; --ink:#1a1a18; --dim:#6b6b66; --line:#e4e4df;
  --ok:#1f7a3d; --okbg:#e8f5ec; --bad:#b3261e; --badbg:#fdeceb;
  --wait:#8a6a1f; --waitbg:#fdf4de; --accent:#2f5fd0;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#141410; --card:#1d1d19; --ink:#ecece6; --dim:#9b9b93; --line:#2f2f29;
  --ok:#6fd18f; --okbg:#16301f; --bad:#f0827a; --badbg:#331a18;
  --wait:#e0c268; --waitbg:#2e2612; --accent:#7fa3f0;
}}
:root[data-theme=dark]{
  --bg:#141410; --card:#1d1d19; --ink:#ecece6; --dim:#9b9b93; --line:#2f2f29;
  --ok:#6fd18f; --okbg:#16301f; --bad:#f0827a; --badbg:#331a18;
  --wait:#e0c268; --waitbg:#2e2612; --accent:#7fa3f0;
}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{background:var(--bg);color:var(--ink);margin:0;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:14px;margin:34px 0 10px;color:var(--dim);font-weight:600;letter-spacing:.03em}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin-bottom:12px}
.grid{display:grid;gap:12px}
.g3{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.g2{grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}
.big{font-size:30px;font-weight:650;letter-spacing:-.02em;line-height:1.2}
.lab{color:var(--dim);font-size:12px;margin-bottom:3px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;
  font-weight:600;white-space:nowrap}
.p-ok{background:var(--okbg);color:var(--ok)}
.p-bad{background:var(--badbg);color:var(--bad)}
.p-wait{background:var(--waitbg);color:var(--wait)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--dim);font-weight:600;padding:6px 8px;
  border-bottom:1px solid var(--line);font-size:12px}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.bar{height:7px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:99px}
.bar>i.full{background:var(--ok)}
.muted{color:var(--dim)}
.tiny{font-size:12px;line-height:1.5}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.flag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;
  background:var(--badbg);color:var(--bad);margin-left:6px;font-weight:600}
.log{font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;
  color:var(--dim);white-space:pre-wrap;word-break:break-word;margin:0}
.hr{height:1px;background:var(--line);margin:12px 0}
.note{font-size:12.5px;color:var(--dim);margin-top:10px;line-height:1.7}
button{font:inherit;font-size:13px;padding:6px 12px;border-radius:7px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button.danger{border-color:var(--bad);color:var(--bad)}
input{font:inherit;font-size:13px;padding:5px 8px;border-radius:7px;border:1px solid var(--line);
  background:var(--bg);color:var(--ink)}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);padding:10px 16px;border-radius:9px;
  font-size:13.5px;opacity:0;transition:opacity .25s;pointer-events:none;z-index:9;
  background:var(--card);border:1px solid var(--line);max-width:80vw}
.toast.ok{border-color:var(--ok);color:var(--ok)} .toast.bad{border-color:var(--bad);color:var(--bad)}
ul.news{margin:8px 0 0;padding-left:18px}
ul.news li{margin-bottom:8px}
</style>
<div class="wrap" id="app"></div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
var D = JSON.parse(document.getElementById('DATA').textContent);

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function pct(v,dp){if(v==null||isNaN(v))return '—';
  dp=dp==null?2:dp;return (v>0?'+':'')+Number(v).toFixed(dp)+'%';}
function num(v){return v==null?'—':Number(v).toLocaleString('ko-KR');}
function pill(s,t){return '<span class="pill p-'+s+'">'+esc(t)+'</span>';}

function head(){
  var c=D.contest, b=D.board||{}, rd=D.readiness||{}, g=D.gap;
  var st=rd.status||'—';
  var k=st==='실행 준비'?'ok':(st==='신규 주문 중지'?'bad':'wait');
  var reasons=(rd.reasons||[]).length
    ?'<ul class="news" style="margin-top:6px">'+(rd.reasons||[]).map(function(r){return '<li class="tiny">'+esc(r)+'</li>';}).join('')+'</ul>'
    :'<div class="tiny muted" style="margin-top:6px">막는 것 없음 — 15:05 계획 → 15:10 매도 → 15:21 매수 → 15:40 대조</div>';
  var gapCard=g
    ?('<div class="card"><div class="lab">1위 격차</div><div class="big">'+esc((g.pp>0?'뒤 ':'앞 ')+Math.abs(g.pp).toFixed(2))+'%p</div>'+
      '<div class="tiny muted">1위 '+pct(g.leader)+' · 선두가 멈춘다면 추가 '+esc(g.need_pct.toFixed(2))+'% 필요 (일 '+esc(g.per_day_pct.toFixed(2))+'%, '+g.days_left+'일) — 계산 예시, 예측 아님</div></div>')
    :'<div class="card"><div class="lab">1위 격차</div><div class="big">—</div><div class="tiny muted">1위·내 수익률이 둘 다 관측돼야 계산한다</div></div>';
  return '<h1>'+esc(c.name||'iM Rookie League')+'</h1>'+
  '<div class="sub">'+esc(c.start)+' ~ '+esc(c.end)+
  ' · 원금 '+num(c.principal)+'원'+
  ' · 참가 '+num(c.n_field)+'명'+
  ' · 상금 '+(c.prize_places||'?')+'위까지'+
  ' · 생성 '+esc(String(D.generated).replace('T',' '))+'</div>'+
  '<div class="card" style="margin-bottom:12px"><div class="lab">실행 판정</div><div>'+pill(k,st)+
    ' <span class="tiny muted">엔진 모드 '+esc(rd.mode||'—')+' · 오늘 대조 '+esc(rd.reconcile||'미실행')+
    ' · 접수 불명 '+esc(rd.unresolved||0)+'건'+((rd.kill_paths||[]).length?' · 정지 파일 '+esc(rd.kill_paths.join(', ')):'')+'</span></div>'+reasons+'</div>'+
  '<div class="grid g3">'+
    '<div class="card"><div class="lab">내 수익률</div><div class="big">'+
      pct(b.my_return_pct)+'</div><div class="tiny muted">'+
      (b.my_return_pct==null?'중계실에 아직 내 행이 없다 (매매 전이면 정상)':'중계실 잠정값 · 갱신 '+esc(String(b.updated||'').replace('T',' ').slice(5,16)))+
      '</div></div>'+
    '<div class="card"><div class="lab">잠정 순위</div><div class="big">'+
      (b.my_rank?b.my_rank+'위':'—')+'</div><div class="tiny muted">'+
      (c.n_field?('전체 '+c.n_field+'명 · 최종 심사 전 잠정'):'')+'</div></div>'+
    gapCard+
    '<div class="card"><div class="lab">남은 영업일</div><div class="big">'+
      c.days_left+'일</div><div class="tiny muted">전체 '+c.days_total+
      '일 · 휴장 '+((c.holidays||[]).join(', ')||'없음')+'</div></div>'+
  '</div>';
}

function today(){
  var h=D.hts,r=D.rest,rows='';
  if(r.is){
    rows='<tr><td colspan="3">'+pill('wait','휴장')+' '+esc(r.why)+
      ' — 오늘은 실행하지 않는다</td></tr>';
  } else {
    rows=D.steps.map(function(s){
      var k=s.state==='ok'?'ok':(s.state==='bad'?'bad':'wait');
      var t=s.state==='ok'?'완료':(s.state==='bad'?'문제':'대기');
      return '<tr><td style="width:110px">'+esc(s.name)+'</td><td style="width:80px">'+
        pill(k,t)+'</td><td class="muted">'+esc(s.note)+'</td></tr>';}).join('');
  }
  var kill=D.kill?('<div class="card">'+pill('bad','긴급정지')+
    ' KILL 파일이 있다 — 주문이 나가지 않는다</div>'):'';
  return '<h2>오늘</h2>'+kill+'<div class="card"><div>'+
    pill(h.ok?'ok':'bad',h.ok?'HTS 주문창 감지':'HTS 확인 필요')+' '+esc(h.text)+
    ' <span class="tiny muted">(창 감지는 계좌·체결 대조가 아니다 — 대조 상태는 위 실행 판정)</span>'+
    '</div><div class="hr"></div><table>'+rows+'</table>'+
    '<div class="note">HTS 세션 — '+esc(D.session.text)+'</div></div>';
}

function qual(){
  var q=D.qual; if(!q||!q.turnover) return '';
  function bar(label,got,want,unit){
    var p=Math.min(100,want?got/want*100:0);
    return '<div class="card"><div class="lab">'+label+'</div><div class="big">'+
      got.toLocaleString('ko-KR',{maximumFractionDigits:0})+unit+
      '</div><div class="tiny muted">필요 '+want.toLocaleString('ko-KR')+unit+
      '</div><div class="bar"><i class="'+(p>=100?'full':'')+'" style="width:'+p+'%"></i></div></div>';
  }
  return '<h2>수상 자격</h2><div class="grid g3">'+
    bar('회전율',q.turnover[0],q.turnover[1],'%')+
    bar('매매일수',q.days[0],q.days[1],'일')+
    bar('매매종목수',q.symbols[0],q.symbols[1],'종목')+
    '</div><div class="note">기준: <b>'+esc(q.source||'')+'</b>'+
    ((q.official||{}).at?' (공식 갱신 '+esc(String(q.official.at).replace('T',' ').slice(5,16))+')':'')+
    ' · 내부 원장: 회전율 '+esc((q.internal||{}).turnover!=null?Number(q.internal.turnover).toFixed(0)+'%':'—')+
    ' / 일수 '+esc((q.internal||{}).days)+' / 종목 '+esc((q.internal||{}).symbols)+
    ((q.official||{}).turnover!=null
      ?' · 공식: 회전율 '+esc(Number(q.official.turnover).toFixed(0))+'% / 일수 '+esc(q.official.days)+' / 종목 '+esc(q.official.symbols)
      :' · 공식값 없음 (매매 전이면 정상)')+
    '<br>ETF 규정: 종목수 산입 '+esc((q.etf_rule||{}).symbols==null?'미확인':((q.etf_rule||{}).symbols?'확인':'제외'))+
    ' · 회전율 산입 '+esc((q.etf_rule||{}).turnover==null?'미확인':((q.etf_rule||{}).turnover?'가정 → 공식 회전율로 검증':'제외'))+
    ((q.etf_rule||{}).evidence?' ('+esc(q.etf_rule.evidence)+')':'')+
    '. 셋 다 채워야 상금 대상이다. 하나라도 미달이면 등수와 무관하게 0원. '+
    '수익률이 음수여도 제외된다. 중계실 수치는 최종 심사 전 잠정값이다.</div>';
}

function chart(){
  var s=(D.series||[]).filter(function(x){return x.mine!=null;});
  if(s.length<2) return '<h2>수익률 추이</h2><div class="card muted">'+
    '관측이 '+s.length+'건이다 — 이틀치가 쌓이면 그려진다.</div>';
  var W=1000,H=250,P=36,vals=[];
  s.forEach(function(x){[x.mine,x.max,x.avg].forEach(function(v){if(v!=null)vals.push(v);});});
  var lo=Math.min.apply(null,vals.concat([0])),hi=Math.max.apply(null,vals.concat([0]));
  function X(i){return P+i*(W-2*P)/Math.max(1,s.length-1);}
  function Y(v){return H-P-((v||0)-lo)/((hi-lo)||1)*(H-2*P);}
  function line(key,color,w){return '<polyline fill="none" stroke="'+color+
    '" stroke-width="'+w+'" stroke-linejoin="round" points="'+
    s.map(function(x,i){return X(i)+','+Y(x[key]);}).join(' ')+'"/>';}
  return '<h2>수익률 추이</h2><div class="card scroll">'+
    '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;min-width:520px">'+
    '<line x1="'+P+'" y1="'+Y(0)+'" x2="'+(W-P)+'" y2="'+Y(0)+'" stroke="var(--line)"/>'+
    line('max','var(--dim)',1.5)+line('avg','var(--line)',1.5)+line('mine','var(--accent)',2.5)+
    '<text x="'+P+'" y="17" fill="var(--dim)" font-size="12">'+
    '파랑 = 나 · 회색 = 1위 · 옅은 선 = 평균</text>'+
    '<text x="'+P+'" y="'+(H-8)+'" fill="var(--dim)" font-size="11">'+esc(s[0].date)+'</text>'+
    '<text x="'+(W-P)+'" y="'+(H-8)+'" fill="var(--dim)" font-size="11" text-anchor="end">'+
    esc(s[s.length-1].date)+'</text></svg></div>';
}

function trades(){
  if(!D.history.length) return '<h2>매매 이력</h2>'+
    '<div class="card muted">아직 주문서가 없다.</div>';
  var blocks=D.history.slice(0,12).map(function(h){
    var rows=h.orders.map(function(o){
      var st=o.status.indexOf('불명')>=0?pill('bad',o.status)
        :(o.status.indexOf('접수')>=0?pill('ok',o.status):pill('wait',o.status));
      return '<tr><td>'+pill(o.side==='BUY'?'ok':'wait',o.side==='BUY'?'매수':'매도')+
        '</td><td>'+esc(o.name)+'<div class="tiny muted">'+esc(o.code)+'</div></td>'+
        '<td class="num">'+num(o.qty)+'주</td><td class="num">'+num(o.price)+'원</td>'+
        '<td class="num">'+num(o.amount)+'원</td><td>'+st+'</td>'+
        '<td class="tiny muted">'+esc(o.reason)+'</td></tr>';}).join('');
    return '<div class="card"><div class="lab">'+esc(h.date)+'</div><div class="scroll">'+
      '<table><thead><tr><th></th><th>종목</th><th class="num">수량</th>'+
      '<th class="num">지정가</th><th class="num">금액</th><th>전송</th>'+
      '<th>사유</th></tr></thead><tbody>'+rows+'</tbody></table></div></div>';}).join('');
  return '<h2>매매 이력</h2>'+blocks;
}

function risk(){
  var r=D.risk||{};
  return '<h2>위험 옵션</h2><div class="grid g2">'+
  '<div class="card"><div class="lab">익절</div><div>'+
    pill(r.take_profit_enabled?'ok':'wait',
         r.take_profit_enabled?('켜짐 +'+r.take_profit_pct+'%'):'꺼짐')+
    '</div><div class="note">수익률이 문턱에 닿으면 전량 '+
    '현금화하고 남은 기간 쉰다. <b>과거 모형 실험</b>(2026-09-06, 참가 약 100명·필드 σ 25%p 가정, '+
    'tools/selftest_options.py)에서는 +20% 고정 익절이 입상 빈도를 46.3%→50.8% 로 올리고 '+
    '1등 빈도를 2.62%→0.00% 로 없앴다 (+30% 0.03%, +50% 0.86%, +80% 2.46%). '+
    '0회는 확률 0 의 증명이 아니고, 현재 순위·잔여기간에서의 조건부 비교는 별도다.</div></div>'+
  '<div class="card"><div class="lab">손실 복구</div><div>'+
    pill(r.recover_enabled?'ok':'wait',
         r.recover_enabled?('켜짐 '+r.recover_below_pct+'% 아래 '+r.recover_exposure_mult+'배'):'꺼짐')+
    '</div><div class="note">뒤져 있으면 비중과 노출을 키운다. '+
    '원금 기준 50% 규정과 총노출 100%는 그대로 지킨다. '+
    '<b>기대를 낮게 잡을 것</b> — 실측 효과가 잡음 수준이다'+
    '(P10 −21.0%→−20.2%). 평시 95% 투자에 종목당 원금 49% 상한(2026-09-07 매뉴얼 기준 적용)이라 '+
    '늘릴 여지가 약 2.5%p 뿐이다.</div></div></div>';
}

function holdings(){
  var p=D.positions||{},k=Object.keys(p);
  var rows=k.length?k.map(function(c){return '<tr><td>'+esc(p[c].name||c)+
    '<div class="tiny muted">'+esc(c)+'</div></td><td class="num">'+num(p[c].qty)+
    '주</td><td class="num">'+num(p[c].avg_price)+'원</td></tr>';}).join('')
    :'<tr><td colspan="3" class="muted">보유 종목 없음</td></tr>';
  return '<h2>보유</h2><div class="card"><table><thead><tr><th>종목</th>'+
    '<th class="num">수량</th><th class="num">평단</th></tr></thead><tbody>'+
    rows+'</tbody></table></div>';
}

// ------------------------------------------------ 파일로 열렸으면 살아 있는 페이지로
// 카드에서 받은 파일이나 더블클릭은 file:// 로 열린다. 그 페이지는 제어 서버에
// 닿지 못해 손잡이가 없다. 서버가 살아 있으면 그쪽으로 옮겨 앉는다 — 클로드에서
// 보는 것과 크롬에서 보는 것이 같아지는 유일한 방법이다. 서버가 없으면 그 사실을
// 위에 띄운다. (no-cors 로 찔러 보기만 하므로 서버가 CORS 를 열 필요가 없다.)
(function(){
  if (location.protocol !== 'file:' || !D.control_url) return;
  var bar = document.createElement('div');
  bar.id = 'filebar';
  bar.style.cssText = 'position:sticky;top:0;z-index:8;padding:10px 16px;font-size:13.5px;'+
    'background:var(--waitbg);color:var(--wait);border-bottom:1px solid var(--line)';
  bar.textContent = '파일로 열린 상황판이다 — 제어 서버를 확인하는 중…';
  document.body.insertBefore(bar, document.body.firstChild);
  var ctrl = setTimeout(function(){}, 0);
  fetch(D.control_url + 'api/state', {mode:'no-cors', cache:'no-store'})
    .then(function(){ bar.textContent = '제어 서버가 살아 있다 — 손잡이가 있는 페이지로 옮긴다…';
      setTimeout(function(){ location.replace(D.control_url); }, 400); })
    .catch(function(){ bar.innerHTML = '파일로 열려서 <b>보기만</b> 된다. 손잡이(긴급정지·건너뛰기·익절·제안)를 쓰려면 '+
      '<code>scripts/dashboard.bat</code> 을 실행하거나 <code>'+D.control_url+'</code> 로 열 것. '+
      '(제어 서버는 로그온 시 자동으로 뜬다 — 이 표시가 보이면 서버가 죽어 있다는 뜻이다)'; });
})();

function intradaySec(){
  var I=D.intraday||[]; if(!I.length) return '';
  var rows=I.map(function(r){return '<tr><td>'+esc(r.time.slice(0,2)+':'+r.time.slice(2))+'</td>'+
    '<td class="num">'+num(r.n_field)+'</td><td class="num">'+pct(r.max_pct)+'</td><td class="num">'+pct(r.avg_pct)+'</td>'+
    '<td class="num">'+(r.rank?r.rank+'위':'—')+'</td><td class="num">'+pct(r.ret_pct)+'</td>'+
    '<td class="num">'+(r.turnover_pct!=null?Math.round(r.turnover_pct)+'%':'—')+'</td><td class="num muted">'+r.rows+'</td></tr>';}).join('');
  return '<h2>오늘 중계실 (시간별)</h2><div class="card scroll"><table><thead><tr><th>시각</th>'+
    '<th class="num">참가</th><th class="num">1위</th><th class="num">평균</th><th class="num">내 순위</th>'+
    '<th class="num">내 수익률</th><th class="num">회전율</th><th class="num">행</th></tr></thead><tbody>'+rows+
    '</tbody></table><div class="note">감시자가 장중 한 시간마다 긁는다. 결정 엔진은 언제나 가장 최신 한 장을 읽는다.</div></div>';
}

function relaySec(){
  var ex=D.relay_extra||{}, rows=D.relay_rows||[], me=D.relay_me||null, cand=D.candidates||{}, pos=D.positions||{};
  var tt=ex.trade_top||[], info=ex.info||{}, mt=ex.memetop10||[];
  var candSet={}; (cand.codes||[]).forEach(function(c){candSet[String(c)]=1;});
  var held={}; Object.keys(pos).forEach(function(c){held[String(c)]=1;});
  if(!tt.length && !Object.keys(info).length && !rows.length) return '';
  function won(v){v=Number(v||0); return v>=1e8? (v/1e8).toFixed(1)+'억' : v>=1e4? Math.round(v/1e4)+'만' : num(v);}
  var h='<h2>중계실 — 필드가 지금 무엇을 하나</h2>';
  // 대회 통계
  if(Object.keys(info).length){
    var g=function(k){return info[k]==null?'—':info[k];};
    h+='<div class="card"><div class="lab">대회 통계 (기준일 '+esc(g('기준일자'))+', 중계실 리포트)</div>'+
      '<div style="display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px"><div><div class="tiny muted">총참가</div><div>'+esc(g('총참가자'))+'명</div></div>'+
      '<div><div class="tiny muted">거래자 / 무거래</div><div>'+esc(g('거래자'))+' / '+esc(g('무거래자'))+'</div></div>'+
      '<div><div class="tiny muted">이익실현 / 손실실현</div><div>'+esc(g('이익실현'))+' / '+esc(g('손실실현'))+'</div></div>'+
      '<div><div class="tiny muted">평균수익률 / 평균회전율</div><div>'+esc(g('평균수익률'))+'% / '+esc(g('평균회전율'))+'%</div></div>'+
      '<div><div class="tiny muted">총거래금액</div><div>'+won(info['총거래금액'])+'</div></div>'+
      '<div><div class="tiny muted">인당 평균 거래금액</div><div>'+won(info['인당평균거래금액'])+'</div></div>'+
      '<div><div class="tiny muted">총수익금</div><div>'+won(info['총수익금'])+'</div></div>'+
      '<div><div class="tiny muted">미거래자(명단 세기만)</div><div>'+esc(ex.untraded==null?'—':ex.untraded)+'명</div></div></div>'+
      '<div class="note">기준일이 어제인 이유: 중계실 리포트는 D+1 에 확정된다. 오늘 매매상위만 실시간이다.</div></div>';
  }
  // 오늘 매매상위 (실시간)
  if(tt.length){
    var tr=tt.map(function(t){var c=String(t.code); var mark=held[c]?pill('warn','보유'):(candSet[c]?pill('ok','후보'):'');
      return '<tr><td class="num">'+esc(t.rank)+'</td><td>'+esc(t.name)+' <span class="muted tiny">'+esc(c)+'</span> '+mark+'</td>'+
        '<td class="num">'+num(t.qty)+'</td><td class="num">'+won(t.amount)+'</td></tr>';}).join('');
    h+='<div class="card scroll"><div class="lab">오늘 참가자 매매상위 ('+esc(tt[0].date||'')+' 실시간 · 군중이 지금 사고파는 종목)</div>'+
      '<table><thead><tr><th class="num">#</th><th>종목</th><th class="num">매매수량</th><th class="num">매매금액</th></tr></thead><tbody>'+tr+'</tbody></table>'+
      '<div class="note">판단이 아니라 관측이다. 우리 후보·보유와 겹치면 표식이 붙고 전문가 점검(05 시장 레짐)이 강조한다. 점수에는 들어가지 않는다 — 검증 전 신호는 제안 채널로만.</div></div>';
  }
  // 누적 매매상위 10 (D+1)
  if(mt.length){
    var keys=Object.keys(mt[0]).filter(function(k){return k!=='순위';}).slice(0,5);
    h+='<div class="card scroll"><div class="lab">대회 누적 매매상위 10 (기준일 '+esc(info['기준일자']||'')+')</div><table><thead><tr><th class="num">#</th>'+
      keys.map(function(k){return '<th>'+esc(k)+'</th>';}).join('')+'</tr></thead><tbody>'+
      mt.slice(0,10).map(function(r){return '<tr><td class="num">'+esc(r['순위']||'')+'</td>'+keys.map(function(k){return '<td>'+esc(r[k])+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>';
  }
  // 순위표 상위
  if(rows.length){
    var rr=rows.slice(0,10).map(function(r){return '<tr><td class="num">'+esc(r.rank)+'</td><td>'+esc(r.name||'')+'</td><td class="num">'+pct(r.ret_pct)+'</td>'+
      '<td class="num">'+(r.turnover_pct!=null?Math.round(r.turnover_pct)+'%':'—')+'</td><td class="num">'+(r.days!=null?r.days:'—')+'</td><td class="num">'+(r.symbols!=null?r.symbols:'—')+'</td></tr>';}).join('');
    var meRow=me?'<div class="note">내 행: '+esc(me.rank)+'위 · '+pct(me.ret_pct)+' · 회전율 '+(me.turnover_pct!=null?Math.round(me.turnover_pct)+'%':'—')+'</div>':'<div class="note">내 행 없음 (매매 전이면 정상)</div>';
    h+='<div class="card scroll"><div class="lab">순위표 상위 10</div><table><thead><tr><th class="num">#</th><th>필명</th><th class="num">수익률</th><th class="num">회전율</th><th class="num">일수</th><th class="num">종목</th></tr></thead><tbody>'+rr+'</tbody></table>'+meRow+'</div>';
  }
  return h;
}

function cycleSec(){
  var C=D.cycles||[], cy=D.cycle||{}, cand=D.candidates||{};
  var last=C[0];
  function t(s){return esc(String(s||'').replace('T',' ').slice(5,16));}
  var now_='<div class="card"><div class="lab">지금</div>';
  if(!last){
    now_+='<div class="muted">아직 회차 기록이 없다 — 제어 서버 추적기가 한 시간 안에 첫 회차를 돈다.</div>';
  } else {
    var cnt=last.opinions||{};
    now_+='<div>'+pill('ok','마지막 '+t(last.at))+' '+esc(last.source)+' · '+
      esc(last.mode==='full'?'정밀(전체 점수 재계산)':'시간별(후보 캐시)')+
      ' · 종목 '+esc(last.names)+' · 경성 '+(last.hard||[]).length+' · 제안 '+(last.proposals||[]).length+
      ' · 의견 '+esc(cnt.info||0)+'/'+esc(cnt.warn||0)+'/'+esc(cnt.alert||0)+' (info/warn/alert) · '+
      esc(last.elapsed_s)+'초</div>';
  }
  now_+='<div class="tiny muted" style="margin-top:6px">다음 회차 '+(cy.next_due?t(cy.next_due):'—')+
    (cy.blackout?' · 지금은 14:30~15:35 정밀 창(시간별 회차 쉼)':'')+
    (cy.active&&cy.active[0]===false?' · '+esc(cy.active[1]):'')+'</div>';
  if(cand.codes&&cand.codes.length){
    now_+='<div class="tiny" style="margin-top:6px">감시 후보 '+cand.codes.length+'종목 (기준 '+esc(cand.date)+' '+esc(cand.phase||'')+'): '+
      cand.codes.map(function(c){return esc((cand.names||{})[c]||c);}).join(', ')+' + 보유 종목</div>';
  } else {
    now_+='<div class="tiny muted" style="margin-top:6px">감시 후보 없음 — 14:45 정밀 회차(또는 15:05 계획)가 후보를 남긴다. 그 전에는 보유 종목만 본다.</div>';
  }
  now_+='</div>';
  var rows=C.slice(0,12).map(function(c){var o=c.opinions||{};
    return '<tr><td>'+t(c.at)+'</td><td>'+esc(c.source)+'</td><td>'+esc(c.mode==='full'?'정밀':'시간별')+'</td>'+
      '<td class="num">'+esc(c.names)+'</td><td>'+((c.hard||[]).length?pill('bad','경성 '+c.hard.length):'—')+'</td>'+
      '<td>'+((c.proposals||[]).join(', ')||'—')+'</td><td>'+esc(o.info||0)+'/'+esc(o.warn||0)+'/'+esc(o.alert||0)+'</td>'+
      '<td>'+((c.alerts_sent||[]).join(', ')||'—')+'</td><td class="num">'+esc(c.elapsed_s)+'s</td></tr>';}).join('');
  var table=rows?'<div class="card scroll"><table><thead><tr><th>시각</th><th>실행자</th><th>모드</th>'+
    '<th class="num">종목</th><th>경성</th><th>제안</th><th>의견</th><th>경보 전송</th><th class="num">소요</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table></div>':'';
  return '<h2>사이클 — 뉴스 → 전문가 점검 → 제외 제안 → 15:05 계획 (매시간, 24시간)</h2>'+now_+table+
    '<div class="note">낮에는 감시자가, 밤과 재부팅 직후에는 제어 서버 추적기가 같은 마커를 보고 한 시간에 한 번 돈다. '+
    '14:45 정밀 회차만 전체 유니버스 점수를 다시 계산한다(시세 출처 부담). 시간별 회차는 그 후보 목록을 재사용해 뉴스만 새로 긁는다. '+
    '매매에 닿는 통로는 둘뿐이다 — 경성 위험 <b>제외 제안</b>(15:05 관문 통과 필요)과 중계실 σ_f. '+
    '전문가 의견은 기록·강조·텔레그램 경보이지 주문이 아니다. 대회 마지막 날 +1일까지 돈다.</div>';
}

function newsHistory(){
  var H=D.news_history||[]; if(!H.length) return '';
  var it=D.news_intraday||[];
  var strip2=it.length?'<div class="tiny muted" style="margin-bottom:8px">오늘 회차: '+
    it.map(function(x){return esc(x.time.slice(0,2)+':'+x.time.slice(2))+((x.hard||[]).length?' <b>경성 '+x.hard.length+'</b>':'');}).join(' · ')+'</div>':'';
  var rows=H.map(function(e){
    var strip=e.days.map(function(d){
      var k=d.hard.length?'p-bad':(d.soft.length?'p-wait':'p-ok');
      var t=d.hard.length?d.hard.join(','):(d.soft.length?d.soft.join(','):'—');
      return '<span class="pill '+k+'" title="'+esc(d.date)+' 기사 '+d.n+'건">'+esc(d.date.slice(4,6)+'/'+d.date.slice(6,8))+' '+esc(t)+'</span> ';
    }).join('');
    return '<tr><td>'+esc(e.name)+'<div class="tiny muted">'+esc(e.code)+'</div></td><td>'+strip+'</td></tr>';
  }).join('');
  return '<h2>뉴스 기록 (매시간 사이클 · 14:45 정밀)</h2><div class="card scroll">'+strip2+'<table><thead><tr><th>종목</th>'+
    '<th>날짜별 표식</th></tr></thead><tbody>'+rows+'</tbody></table>'+
    '<div class="note">빨강 = <b>경성</b>(거래정지·관리종목·투자경고·상장폐지·횡령 등, 최근 5일 기사만) — '+
    '오늘 후보(top-2)에 있으면 그 종목을 뺀 배분이 <b>제안</b>으로 15:05 계획에 올라간다. '+
    '관문을 통과해야 실행된다. 노랑 = 주의(테마·과열·급락 등) — 기록만 한다. '+
    '방향(호재/악재) 판단이 아니라 <b>매매제한 위험</b>이다.</div></div>';
}

function observations(){
  var O=D.observations||[]; if(!O.length) return '';
  var rows=O.slice().reverse().slice(0,25).map(function(o){
    var kv=Object.keys(o).filter(function(k){return k!=='at'&&k!=='kind';})
      .map(function(k){return esc(k)+'='+esc(typeof o[k]==='number'?(Math.round(o[k]*100)/100):o[k]);}).join(' · ');
    return '<tr><td class="tiny muted" style="white-space:nowrap">'+esc(String(o.at).replace('T',' ').slice(5,16))+'</td>'+
      '<td>'+pill(o.kind==='me'?'ok':(o.kind==='etf_rule'?'bad':'wait'),o.kind)+'</td><td class="tiny">'+kv+'</td></tr>';
  }).join('');
  return '<h2>관측 기록</h2><div class="card scroll"><table><thead><tr><th>시각</th><th>종류</th><th>값</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table><div class="note">대회가 스스로 재는 값이다 — 참가자수·필드 분산 σ_f·내 순위·ETF 산입. '+
    '전문가 가설 중 몇 개는 이 줄들이 답한다(RSK-H001 ← σ_f, HYP-002 ← ETF). '+
    '문서에 적힌 채로 두면 답이 와도 아무도 대조하지 않는다. 그래서 여기 쌓인다.</div></div>';
}

function newsSec(){
  var by=(D.news||{}).by_name||{},names=Object.keys(by);
  if(!names.length) return '';
  var blocks=names.map(function(nm){
    var items=by[nm].map(function(h){
      var flags=(h.flags||[]).map(function(f){return '<span class="flag">'+esc(f)+'</span>';}).join('');
      var t=h.url?('<a href="'+esc(h.url)+'" target="_blank" rel="noopener">'+esc(h.title)+'</a>')
        :esc(h.title);
      return '<li>'+t+flags+'<div class="tiny muted">'+esc(h.when)+'</div></li>';}).join('');
    return '<div class="card"><div class="lab">'+esc(nm)+'</div><ul class="news">'+items+'</ul></div>';
  }).join('');
  return '<h2>뉴스 ('+esc((D.news||{}).source||'실시간 수집')+')</h2>'+blocks+
    '<div class="note"><b>뉴스 알파 점수는 없다.</b> 알파는 가격과 거래량만 본다 — '+
    '뉴스를 점수로 바꾸려면 그 점수가 수익을 예측한다는 검증이 먼저인데, 하지 않았다. '+
    '뉴스가 매매에 닿는 유일한 통로는 <b>경성 위험</b>(거래정지·관리종목·투자경고 등, 최근 5일 기사)이 '+
    '오늘 후보에 붙었을 때의 <b>제외 제안</b>이고, 그것도 15:05 관문을 통과해야 목표가 바뀐다. '+
    '제목 검출은 공식 매매제한 지정과 다르다 — 공식 지정은 계획 때 시장경보 명단으로 따로 거른다.</div>';
}

// ---------------------------------------------------------------- 손잡이
// 제어 서버(control.py)가 이 페이지를 http 로 내줄 때만 살아난다. 파일로 열면
// 쓰기가 불가능하므로 그 사실을 그대로 보여준다.
var CTRL = (location.protocol === 'http:' || location.protocol === 'https:');
function api(path, body){
  // 이 서버가 내준 페이지에만 심어진 토큰. 없으면(파일로 열림·서버 재시작) 서버가 거부한다.
  return fetch(path, {method: body?'POST':'GET',
    headers:{'Content-Type':'application/json','X-IMRL-Token':(window.IMRL_TOKEN||'')},
    body: body?JSON.stringify(body):undefined}).then(function(r){return r.json();});
}
function toast(msg, bad){
  var t=document.getElementById('toast'); if(!t) return;
  t.textContent=msg; t.className='toast '+(bad?'bad':'ok'); t.style.opacity='1';
  clearTimeout(t._h); t._h=setTimeout(function(){t.style.opacity='0';},4000);
}
function act(path, body, confirmMsg){
  if(confirmMsg && !confirm(confirmMsg)) return;
  api(path, body).then(function(r){
    if(r.ok){ toast(r.message||'완료'); setTimeout(function(){location.reload();},900); }
    else toast('거부: '+(r.error||'?'), true);
  }).catch(function(e){ toast('연결 실패: '+e, true); });
}
window.act = act;

// ---------------------------------------------------------------- 연결 표시
// 서버로 열린 페이지는 30초마다 서버를 찌른다. 서버가 죽으면 즉시 빨간불, 다시
// 살아나면 스스로 초록불 — 프로그램을 껐다 켜도 페이지는 그대로 두면 된다.
// 5분마다 새로 그린 상황판을 다시 읽는다 (추적기가 10분마다, 변화가 있으면 즉시 다시 그린다).
function liveIndicator(){
  if(!CTRL) return '';
  var hb=D.heartbeat||{}, f=D.fresh||{}, k=D.kill;
  // 원천마다 나이를 따로 보인다. 서버 연결이 초록이어도 자료는 각각 오래될 수 있다.
  function age(m,warn){
    if(m==null) return '<span class="pill p-wait">기록 없음</span>';
    var t=m<60?Math.round(m)+'분':(m/60).toFixed(1)+'시간';
    return '<span class="pill '+(m>warn?'p-bad':'p-ok')+'">'+esc(t)+' 전</span>';
  }
  var src='추적기 '+age(f.heartbeat,3)+' · 중계실 '+age(f.relay,150)+' · 사이클 '+age(f.cycle,100)+
    ' · 뉴스 '+age(f.news,100)+' · 순위표 '+age(f.leaderboard,1500)+' · 결정 '+age(f.decision,1500);
  var stop='<div id="stopbar" style="position:fixed;right:12px;bottom:12px;z-index:9">'+
    (k?'<button onclick="act(\'/api/kill\',{on:false},\'신규 주문 전송 중지를 해제합니다. 접수 불명 주문이 있으면 먼저 대조하세요.\')">중지 해제</button>'
      :'<button class="danger" onclick="act(\'/api/kill\',{on:true},\'신규 주문 전송을 중지합니다. 이미 접수된 주문은 취소되지 않습니다.\')">신규 주문 중지</button>')+
    '</div>';
  return '<div id="live" class="card" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">'+
    '<span id="live-pill" class="pill p-wait">연결 확인 중…</span>'+
    '<span class="tiny muted" id="live-text">'+src+' · HTS '+esc(hb.hts||'—')+'</span>'+
    '</div>'+stop;
}
(function(){
  if(!CTRL) return;
  var fails=0;
  function ping(){
    fetch('/api/state',{cache:'no-store'}).then(function(r){return r.json();}).then(function(r){
      var p=document.getElementById('live-pill'); if(!p) return;
      p.className='pill p-ok'; p.textContent='서버 연결됨 '+new Date().toTimeString().slice(0,8);
      if(fails>0){ fails=0; setTimeout(function(){location.reload();},500); }   // 복귀하면 새 데이터로
    }).catch(function(){
      var p=document.getElementById('live-pill'); if(!p) return;
      fails++; p.className='pill p-bad'; p.textContent='서버 끊김 — 로그온 작업/감시자가 다시 띄운다 ('+fails+')';
    });
  }
  setInterval(ping,30000); setTimeout(ping,800);
  // 입력 중이면 새로고침하지 않는다 — 작성 중인 제안·설정이 날아간다.
  window._dirty=false;
  document.addEventListener('input',function(){window._dirty=true;});
  setInterval(function(){
    var a=document.activeElement, typing=!!(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'));
    if(fails===0 && !window._dirty && !typing) location.reload();
  },300000);
})();

function controls(){
  if(!CTRL){
    return '<h2>손잡이</h2><div class="card muted">파일로 열어서 보기만 된다. '+
      '바꾸려면 제어 서버로 열 것 — <code>python control.py</code> 뒤 '+
      '<code>http://127.0.0.1:8765</code>. 로그온 시 자동으로 뜨도록 등록돼 있으면 그냥 그 주소로.</div>';
  }
  var k=D.kill, sk=(D.skip_today===true), r=D.risk||{}, rd=D.readiness||{};
  return '<h2>손잡이</h2><div id="toast" class="toast" role="status" aria-live="polite"></div><div class="grid g2">'+
  '<div class="card"><div class="lab">신규 주문 전송 중지</div><div>'+(k?pill('bad','켜짐 — 새 주문 안 나감'):pill('ok','꺼짐'))+
    '</div><div class="note">켜면 지울 때까지 <b>새 주문</b>이 나가지 않는다. 이미 접수된 주문은 취소되지 않는다 (취소는 HTS 에서 직접). '+
    '감시자·집행이 같은 파일을 본다: state/KILL 과 C:/imrl_state/KILL — 해제는 둘 다 지운다.'+
    ((rd.unresolved||0)>0?' <b>접수 불명 주문 '+rd.unresolved+'건이 있다 — 해제 전에 대조할 것.</b>':'')+'</div>'+
    '<div style="margin-top:10px">'+(k?'<button onclick="act(\'/api/kill\',{on:false},\'신규 주문 전송 중지를 해제합니다. 접수 불명 주문이 있으면 먼저 대조하세요.\')">해제</button>'
      :'<button class="danger" onclick="act(\'/api/kill\',{on:true},\'신규 주문 전송을 중지합니다. 이미 접수된 주문은 취소되지 않습니다.\')">신규 주문 중지</button>')+'</div></div>'+
  '<div class="card"><div class="lab">오늘만 건너뛰기</div><div>'+(sk?pill('wait','오늘 매도·매수 안 함'):pill('ok','정상 진행'))+
    '</div><div class="note">오늘('+esc(D.contest&&D.generated?String(D.generated).slice(0,10):'')+') 매도·매수만 건너뛴다. 계획·대조·리포트는 돌고, 자정이 지나면 저절로 풀린다 — 잊어도 안전하다.</div>'+
    '<div style="margin-top:10px">'+(sk?'<button onclick="act(\'/api/skip\',{on:false})">해제</button>'
      :'<button onclick="act(\'/api/skip\',{on:true},\'오늘 하루 매도·매수를 건너뜁니다.\')">오늘 건너뛰기</button>')+'</div></div>'+
  '<div class="card"><div class="lab">익절</div><div>'+(r.take_profit_enabled?pill('ok','켜짐 +'+r.take_profit_pct+'%'):pill('wait','꺼짐'))+
    '</div><div class="note">과거 모형 실험(2026-09-06, 참가 약 100명·σ 25%p 가정)에서는 +20% 고정 익절이 1등 빈도를 2.62%→0.00% 로, 입상 빈도를 46.3%→50.8% 로 바꿨다. '+
    '현재 순위·잔여기간 기준의 조건부 비교는 별도다. 적용은 다음 15:05 계획부터.</div>'+
    '<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">'+
    '<label class="tiny" for="tp">문턱</label><input id="tp" aria-label="익절 문턱 퍼센트" type="number" min="5" max="200" step="1" value="'+r.take_profit_pct+'" style="width:80px">% '+
    (r.take_profit_enabled?'<button onclick="act(\'/api/risk\',{take_profit_enabled:false})">끄기</button>'
      :'<button onclick="act(\'/api/risk\',{take_profit_enabled:true,take_profit_pct:+document.getElementById(\'tp\').value},\'익절을 켭니다. 과거 실험에서는 고정 익절이 1등 빈도를 없앴습니다. 다음 15:05 계획부터 적용됩니다.\')">켜기</button>')+
    '<button onclick="act(\'/api/risk\',{take_profit_pct:+document.getElementById(\'tp\').value})">문턱만 저장</button></div></div>'+
  '<div class="card"><div class="lab">손실 복구</div><div>'+(r.recover_enabled?pill('ok','켜짐 '+r.recover_below_pct+'% 아래 목표 '+r.recover_exposure_mult+'배'):pill('wait','꺼짐'))+
    '</div><div class="note">손실 구간 노출 확대 규칙. 배수는 목표이고 실제 노출은 규정 상한(종목당 원금 49%·총노출 97.5%)에 잘린다 — 여지는 약 2.5%p. 실측 효과는 잡음 수준(P10 −21.0→−20.2%, 45%·8%p 시절 측정).</div>'+
    '<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">'+
    '<label class="tiny" for="rb">문턱</label><input id="rb" aria-label="손실 복구 문턱 퍼센트" type="number" min="-60" max="-1" step="1" value="'+r.recover_below_pct+'" style="width:80px">% 아래 '+
    '<label class="tiny" for="rm">배수</label><input id="rm" aria-label="손실 복구 노출 배수" type="number" min="1" max="2" step="0.1" value="'+r.recover_exposure_mult+'" style="width:70px">배 '+
    (r.recover_enabled?'<button onclick="act(\'/api/risk\',{recover_enabled:false})">끄기</button>'
      :'<button onclick="act(\'/api/risk\',{recover_enabled:true})">켜기</button>')+
    '<button onclick="act(\'/api/risk\',{recover_below_pct:+document.getElementById(\'rb\').value,recover_exposure_mult:+document.getElementById(\'rm\').value})">저장</button></div></div>'+
  '<div class="card" style="grid-column:1/-1"><div class="lab">제안 제출</div>'+
    '<div class="note">종목코드와 비중을 적으면 다음 15:05 계획에서 기준선과 나란히 시뮬레이션된다. '+
    '확인용 경로에서 이겨야만 실행된다 — 제출이 곧 주문이 아니다. 한 항목이라도 형식이 틀리면 전체가 거부된다.</div>'+
    '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">'+
    '<input id="pw" aria-label="제안 종목코드와 비중" placeholder="003010 0.45, 092870 40%" style="flex:1;min-width:240px">'+
    '<input id="pn" aria-label="제안 메모" placeholder="메모 (왜 이 배분인가)" style="flex:1;min-width:200px">'+
    '<input id="pe" aria-label="제안 만료일 YYYYMMDD" placeholder="만료 YYYYMMDD" style="width:130px">'+
    '<button onclick="act(\'/api/proposal\',{weights:document.getElementById(\'pw\').value,note:document.getElementById(\'pn\').value,expires:document.getElementById(\'pe\').value,source:\'dashboard\'})">제출</button>'+
    '</div></div>'+
  '<div class="card" style="grid-column:1/-1"><button onclick="act(\'/api/rebuild\',{})">상황판 다시 그리기</button>'+
    ' <span class="tiny muted">읽기 전용 재계산이다 — 주문을 보내지 않는다. 추적기가 10분마다, 상태가 바뀌면 즉시 다시 그린다.</span></div>'+
  '</div>';
}

function rules(){
  var rows=(D.rules||[]).map(function(r){
    var k=r[1]==='ok'?'ok':'wait', t=r[1]==='ok'?'구현됨':'1일차 확인';
    return '<tr><td style="width:36%">'+esc(r[0])+'</td><td style="width:90px">'+pill(k,t)+
      '</td><td class="tiny muted">'+esc(r[2])+'</td></tr>';}).join('');
  return '<h2>대회 규정 대조</h2><div class="card scroll"><table><thead><tr>'+
    '<th>규정</th><th></th><th>코드에서 지키는 곳</th></tr></thead><tbody>'+rows+
    '</tbody></table><div class="note">“구현됨”은 검사 로직이 코드에 있다는 뜻이지 오늘 실제로 통과했다는 뜻이 아니다 — '+
    '오늘의 통과 여부는 15:05 계획 로그와 실행 판정에 남는다. '+
    '“1일차 확인”은 규정 문면으로는 정할 수 없어 첫날 체결로 판별하는 항목이다. 미확정 해석을 준수로 표시하지 않는다.</div></div>';
}

function experts(){
  var ex=D.experts||[]; if(!ex.length) return '';
  var ops=(D.opinions||{}).opinions||[], byE={};
  ops.forEach(function(o){(byE[o.expert]=byE[o.expert]||[]).push(o);});
  var when=(D.opinions||{}).at?String(D.opinions.at).replace('T',' ').slice(5,16):'';
  var cards=ex.map(function(e){
    var mine=(byE[e.id]||[]).map(function(o){return '<li>'+pill(o.level==='alert'?'bad':(o.level==='warn'?'wait':'ok'),o.level)+' '+esc(o.text)+'</li>';}).join('');
    var st=Object.keys(e.by_status||{}).map(function(k){
      return '<span class="pill '+(k==='IDEA'?'p-wait':(k==='REJECTED'?'p-bad':'p-ok'))+'">'+
        esc(k)+' '+e.by_status[k]+'</span> ';}).join('');
    var live=(e.live||[]).map(function(h){return '<li><b>'+esc(h.id)+'</b> '+esc(h.title)+
      '<div class="tiny muted">'+esc(h.evidence)+'</div></li>';}).join('');
    var ho=(e.handoff||[]).map(function(t){return '<li class="tiny">'+esc(t)+'</li>';}).join('');
    return '<div class="card"><div class="lab">'+esc(e.id)+' · '+esc(e.label)+
      ' · 가설 '+e.n_hyp+'건</div><div>'+st+'</div>'+
      (mine?'<div class="tiny muted" style="margin-top:8px">지금 점검 ('+esc(when)+')</div><ul class="news">'+mine+'</ul>':'')+
      (live?'<div class="tiny muted" style="margin-top:8px">코드에 들어간 것</div><ul class="news">'+live+'</ul>':'')+
      (ho?'<div class="tiny muted" style="margin-top:8px">개발 인계 요지</div><ul class="news">'+ho+'</ul>':'')+
      '</div>';}).join('');
  var m=D.measured||{}, mk=Object.keys(m);
  var meas=mk.length?'<div class="card"><div class="lab">이 저장소에서 직접 잰 것</div><ul class="news">'+
    mk.map(function(k){return '<li><b>'+esc(k)+'</b> '+pill(m[k].status==='REJECTED'?'bad':'ok',m[k].status)+
      '<div class="tiny muted">'+esc(m[k].evidence)+'</div></li>';}).join('')+'</ul></div>':'';
  return '<h2>전문가 — 매시간 점검</h2>'+meas+'<div class="grid g2">'+cards+'</div>'+
    '<div class="note">매시간 사이클이 각 전문가의 점검표(문서에서 코드로 옮긴 결정론적 규칙, LLM 아님)를 돌려 의견을 남긴다. '+
    'info 기록 · warn 강조 · alert 텔레그램(같은 건 하루 한 번). '+
    '<b>전문가가 썼다는 것은 근거가 아니다.</b> 7명 중 7명이 동의해도 '+
    '데이터 검증 전에는 hypothesis 다 — 전문가들 스스로 세운 원칙이다. IDEA 는 아직 '+
    '아무 검증도 거치지 않았다는 뜻이고, 그 상태의 의견은 매매에 들어가지 않는다.</div>';
}

function proposals(){
  var ps=D.proposals||[], ld=D.last_decision;
  var rows=ps.length?ps.map(function(p){
    var w=Object.keys(p.weights||{}).map(function(c){return c+' '+Math.round(p.weights[c]*100)+'%';}).join(', ');
    return '<tr><td>'+esc(p.id)+'<div class="tiny muted">'+esc(p.source)+(p.hypothesis?' · '+esc(p.hypothesis):'')+
      '</div></td><td class="tiny">'+esc(w)+'</td><td>'+(p.expired?pill('wait','만료'):pill('ok','대기'))+
      '</td><td class="tiny muted">'+esc(p.note)+'</td></tr>';}).join('')
    :'<tr><td colspan="4" class="muted">대기 중인 제안 없음</td></tr>';
  var dec='';
  if(ld){
    // 후보 **전부**. 모형 최선(선택 경로) → 실행 후보(확인 경로 결과) → 관문 → 채택은 각각 다른 사실이다.
    var cands=(ld.candidates||[]).slice().sort(function(a,b){return (b.win||0)-(a.win||0);});
    var crows=cands.map(function(q){
      var tags=(q.id===ld.adopted?' '+pill('ok','채택'):'')+(q.id===ld.model_best?' <span class="tiny muted">모형 최선</span>':'');
      return '<tr><td>'+esc(q.id)+tags+'</td><td>'+(q.feasible?pill('ok','가능'):pill('bad','불가'))+'</td>'+
        '<td class="num">'+(q.win==null?'—':(q.win*100).toFixed(2)+'%')+'</td>'+
        '<td class="num">'+(q.worst==null?'—':(q.worst*100).toFixed(2)+'%')+'</td>'+
        '<td class="num">'+(q.qual==null?'—':(q.qual*100).toFixed(0)+'%')+'</td>'+
        '<td class="num">'+(q.p10==null?'—':(q.p10*100).toFixed(1)+'%')+'</td>'+
        '<td class="num">'+(q.exposure==null?'—':(q.exposure*100).toFixed(0)+'%')+'</td>'+
        '<td class="tiny muted">'+esc((q.reasons||[]).join('; '))+'</td></tr>';}).join('');
    dec='<div class="card"><div class="lab">마지막 결정 '+esc(String(ld.at).replace('T',' '))+(ld.gate_at?' · 관문 '+esc(String(ld.gate_at).replace('T',' ').slice(11,16)):'')+'</div>'+
      '<div>모형 최선 <b>'+esc(ld.model_best||'—')+'</b> → 실행 후보 <b>'+esc(ld.executed||'—')+'</b> → 관문 '+
      (ld.eligible===true?pill('ok','통과'):(ld.eligible===false?pill('wait','차단 → '+(ld.fallback||'기준선')):pill('wait','미평가')))+
      ' → 채택 <b>'+esc(ld.adopted||'—')+'</b>'+
      (ld.lower_bound!=null?' <span class="tiny muted">확인경로 쌍별 차이 '+((ld.delta||0)*100).toFixed(2)+'%p, 하한 '+(ld.lower_bound*100).toFixed(2)+'%p</span>':' <span class="tiny muted">확인 경로 미실행(도전자 없음)</span>')+
      ((ld.block_reasons||[]).length?'<div class="tiny muted">관문 사유: '+esc(ld.block_reasons.join(', '))+'</div>':'')+'</div>'+
      (crows?'<div class="scroll" style="margin-top:8px"><table><thead><tr><th>후보</th><th>실행</th><th class="num">가정 승률</th><th class="num">최악 모형</th><th class="num">자격률</th><th class="num">P10</th><th class="num">노출</th><th>제외 사유</th></tr></thead><tbody>'+crows+'</tbody></table></div>':'')+
      '<div class="tiny muted" style="margin-top:6px">경로 '+esc((ld.paths||[]).join('+'))+' · '+esc((ld.assumptions||[]).join(' · '))+
      ' · 가정하 시나리오 비율(scenario_only)이지 실제 우승 확률이 아니다. 0승은 확률 0 의 증명이 아니다. 채택이 곧 접수·체결은 아니다 — 주문 사실은 매매 이력에 있다.</div></div>';
  }
  return '<h2>제안 채널</h2><div class="card scroll"><table><thead><tr><th>제안</th><th>비중</th>'+
    '<th></th><th>메모</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+dec+
    '<div class="note">제안은 <code>research/proposals/*.json</code> 에 파일 하나로 낸다. '+
    '다음 15:05 계획에서 기준선과 같은 경로 위에서 시뮬레이션되고, 확인용 경로에서 우승 확률 '+
    '하한이 0 을 넘고 기준선보다 나을 때만 실행된다. <b>의견이 주문이 되는 다른 통로는 없다.</b> '+
    '실행 불가 판정(유니버스 밖·원금 50% 초과·총노출 초과)은 이유와 함께 여기 남는다.</div>';
}

function system(){
  var u=D.universe||{};
  return '<h2>시스템</h2><div class="grid g2">'+
  '<div class="card"><div class="lab">거래 대상</div><div class="tiny">'+
    ((u.markets||[]).join(' · '))+'</div><div class="note">'+
    '최소가 '+num(u.min_price)+'원 · 20일 평균 거래대금 '+
    num(u.min_avg_amount_krw)+'원 이상 · 제외 '+
    ((u.exclude_name_patterns||[]).join(', '))+' · 우선주 제외.<br>'+
    '대회 규정은 “거래소·코스닥 개별종목(ETF 포함, '+
    '단순종목레버리지 ETF 제외)”이고, 유니버스에는 ETF 가 '+
    '아예 들어가지 않아 레버리지 ETF 를 뽑을 위험이 구조적으로 없다. '+
    '회전율 파밍용 ETF 3종만 따로 쓰며 전부 CD금리·초단기채권이라 '+
    '레버리지가 아니다.</div></div>'+
  '<div class="card"><div class="lab">감시자 로그</div><pre class="log">'+
    ((D.watchdog_log||[]).map(esc).join('\n')||'기록 없음')+'</pre></div></div>';
}

document.getElementById('app').innerHTML =
  head()+liveIndicator()+today()+controls()+qual()+chart()+intradaySec()+relaySec()+cycleSec()+trades()+risk()+proposals()+newsHistory()+observations()+experts()+rules()+holdings()+newsSec()+system();
</script>
"""
