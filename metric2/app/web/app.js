/* All displayed values originate from benchmark artifacts returned by the server. */
const $ = id => document.getElementById(id);
const ui = {token:null, run:null, state:null, detail:null, selected:null, online:false, busy:false, descending:false, detailKey:null, polling:false, pendingPoll:false, lastHistory:0, history:[]};
const f = (value, digits=3) => Number.isFinite(value) ? value.toFixed(digits) : '—';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = () => window.lucide?.createIcons();
const active = state => state && ['running','preparing','stopping'].includes(state.status);
const showError = message => { $('error').textContent=message || ''; $('error').hidden=!message; };

async function api(path, options={}) {
  const response=await fetch(path,{cache:'no-store',...options});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function connection(ok) {
  ui.online=ok;
  $('connectionDot').className='dot '+(ok?'online':'offline');
  $('connectionText').textContent=ok?'서버 연결 정상':'연결 끊김 · 마지막 조회 결과';
  controls();
}
function controls() {
  const running=active(ui.state) || ui.history.some(r=>r.active);
  $('start').disabled=!ui.online || running || ui.busy;
  $('stop').disabled=!ui.online || !ui.state || !['running','preparing'].includes(ui.state.status) || ui.busy;
  $('runScenario').disabled=document.querySelector('input[name=mode]:checked').value==='full';
}
function historyLabel(row) {
  const stamp=row.created_utc?new Date(row.created_utc).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):row.id;
  return `${row.active?'실행 중 · ':''}${stamp} · ${row.full_scope?'전체 135회':`대표 ${row.expected}회`}${row.complete?' · 완료':''}`;
}
async function loadHistory() {
  ui.history=await api('/api/history');
  ui.lastHistory=Date.now();
  $('history').innerHTML=ui.history.map(r=>`<option value="${esc(r.id)}">${esc(historyLabel(r))}</option>`).join('');
  if(ui.run) $('history').value=ui.run;
  controls();
}
function labels(state) {
  if(state.status==='complete') return state.full_scope?['기준 충족','success']:[`${state.expected}회 누적 완료`,'neutral'];
  if(state.status==='cancelled') return ['실행 중단','neutral'];
  if(state.status==='stopping') return ['중지 요청 중','neutral'];
  if(state.status==='failed') return ['실행 확인 필요','failed'];
  return ['시험 실행 중','running'];
}
function phaseText(state) {
  const current=state.current;
  const phase={carla_starting:'CARLA 시작',scene_starting:'Scenic 이상상황·감시자 생성',acquiring:'새 센서 프레임 수신',preparing:'입력 검증·준비',warmup:'모델·키 준비 / 워밍업',processing:'추론·공유·BEV 병합',audit:'저장 증명 재검증',complete:'측정·사후 검증 완료',failed:'실행 오류',cancelled:'사용자 중지'}[state.phase] || '실행 기록';
  if(state.status==='complete') return state.full_scope?'S1–S9 실제 생성 × 3차수 × 새 입력 5회':`단일 실행 · 실제 CARLA 새 입력 ${state.expected}회 처리 완료`;
  return current?`${current.scenario || ''} ${current.session?`${current.session}차${current.repeat?` · ${current.repeat}회`:''}`:'준비'} · ${phase}`:phase;
}
function renderSummary(state) {
  $('processed').textContent=state.processed;
  $('expected').textContent='/ '+state.expected;
  $('scope').textContent=state.full_scope?'S1–S9 · 각 15회':`단일 실행 · ${state.expected}회 누적`;
  $('maximum').textContent=f(state.max_seconds);
  $('average').textContent='전체 평균 '+f(state.mean_seconds,6)+'초';
  $('normal').textContent=state.normal;
  $('within').textContent='18초 이내 '+state.within_limit+'회';
  $('proofs').textContent=state.audited*2;
  $('proofExpected').textContent='/ '+state.expected*2;
  const [text,style]=labels(state);
  $('statusBadge').textContent=text;
  $('statusBadge').className='status-badge '+style;
  $('phase').textContent=phaseText(state);
  $('runDate').textContent=state.created_kst?'실행 '+new Date(state.created_kst).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):'시작 기록 대기';
  $('timeCoverage').textContent=`실제 시각 ${state.clock_coverage}/${state.processed}회 기록`;
  $('progressFill').style.width=Math.min(100,state.processed/state.expected*100)+'%';
  $('minimum').textContent=f(state.min_seconds)+'초';
  $('trendAverage').textContent=f(state.mean_seconds)+'초';
  $('trendMaximum').textContent=f(state.max_seconds)+'초';
  $('modelHash').textContent=state.protocol.checkpoint_sha256 || '초기 검증 중';
  $('sourcePath').textContent=state.server_root;
  $('updatedAt').textContent='조회 '+new Date().toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false});
  $('csvDownload').href='/api/download?run='+encodeURIComponent(state.id)+'&format=csv';
  $('jsonDownload').href='/api/download?run='+encodeURIComponent(state.id)+'&format=json';
  if(state.job_error && state.status==='failed') showError(state.job_error);
}
function filteredRows() {
  const scenario=$('filterScenario').value,status=$('filterStatus').value;
  let rows=(ui.state?.rows || []).filter(r=>(scenario==='all'||r.scenario===scenario)&&(status==='all'||r.status===status));
  if(ui.descending) rows=rows.slice().sort((a,b)=>b.elapsed_ms-a.elapsed_ms);
  return rows;
}
function clock(value) { return value?value.split(' ')[1]:'미기록'; }
function renderRows() {
  const rows=filteredRows();
  $('recordCount').textContent=rows.length===ui.state?.processed?`${rows.length}회`:`${rows.length}/${ui.state?.processed || 0}회`;
  $('rows').innerHTML=rows.length?rows.map(r=>`<tr tabindex="0" data-index="${r.index}" aria-selected="${r.index===ui.selected}" class="${r.index===ui.selected?'selected':''}"><td>${String(r.index+1).padStart(3,'0')}</td><td>${esc(r.scenario)}</td><td>${r.session}차 · ${r.repeat}회</td><td title="${esc(r.start_kst || '과거 실행: 실제 세계시각 미기록')}" class="${r.start_kst?'':'unknown'}">${clock(r.start_kst)}</td><td title="${esc(r.end_kst || '과거 실행: 실제 세계시각 미기록')}" class="${r.end_kst?'':'unknown'}">${clock(r.end_kst)}</td><td class="numeric">${f(r.elapsed_ms/1000,6)}</td><td>${r.audit_pass?'2/2 확인':'검증 대기'}</td><td><span class="row-status ${r.status}">${{passed:'통과',auditing:'검증 중',failed:'확인 필요'}[r.status]}</span></td></tr>`).join(''):'<tr><td colspan="8" class="empty">해당 측정 기록 없음</td></tr>';
  if($('follow').checked){
    const selected=$('rows').querySelector('tr.selected'), viewport=document.querySelector('.table-scroll');
    if(selected){const row=selected.getBoundingClientRect(), box=viewport.getBoundingClientRect();if(row.bottom>box.bottom) viewport.scrollTop+=row.bottom-box.bottom;else if(row.top<box.top+35) viewport.scrollTop-=box.top+35-row.top;}
  }
}
function renderTrend() {
  const rows=ui.state?.rows || [];
  if(!rows.length){$('trend').innerHTML='<div class="empty">측정 대기</div>';return;}
  const w=280,h=135,left=27,right=9,top=12,bottom=22,iw=w-left-right,ih=h-top-bottom;
  const max=Math.max(0.5,Math.ceil(Math.max(...rows.map(r=>r.elapsed_ms/1000))*1.12*2)/2);
  const x=index=>left+(rows.length<2?iw/2:index/(rows.length-1)*iw);
  const y=value=>top+ih-value/max*ih;
  const points=rows.map((r,i)=>`${x(i)},${y(r.elapsed_ms/1000)}`).join(' ');
  const selected=rows.findIndex(r=>r.index===ui.selected);
  const lines=[0,.5,1].map(fraction=>{const value=max*fraction;return `<line x1="${left}" x2="${w-right}" y1="${y(value)}" y2="${y(value)}" stroke="#e6ecee" stroke-dasharray="3 3"/><text x="${left-6}" y="${y(value)+3}" text-anchor="end">${value.toFixed(1)}</text>`;}).join('');
  $('trend').innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Ego 전체 처리시간 ${rows.length}회"><g>${lines}</g><polyline points="${points}" fill="none" stroke="#197e76" stroke-width="1.6" vector-effect="non-scaling-stroke"/>${selected>=0?`<line x1="${x(selected)}" x2="${x(selected)}" y1="${top}" y2="${h-bottom}" stroke="#a6bcb7"/><circle cx="${x(selected)}" cy="${y(rows[selected].elapsed_ms/1000)}" r="3.5" fill="#fff" stroke="#147d75" stroke-width="2"/>`:''}<text x="${left}" y="${h-4}">1</text><text x="${w-right}" y="${h-4}" text-anchor="end">${rows.length}회</text><rect x="${left}" y="${top}" width="${iw}" height="${ih}" fill="transparent" class="chart-hit"/></svg>`;
  const hit=$('trend').querySelector('.chart-hit');
  hit.addEventListener('click',event=>{const rect=$('trend').querySelector('svg').getBoundingClientRect();const pos=(event.clientX-rect.left)/rect.width*w;selectRound(rows[Math.max(0,Math.min(rows.length-1,Math.round((pos-left)/iw*(rows.length-1))))].index,true);});
}
function assetUrl(kind, index=ui.detail?.row.index ?? ui.selected) { return `/api/asset?run=${encodeURIComponent(ui.run)}&index=${index}&kind=${kind}`; }
async function selectRound(index,manual=false) {
  if(index===null || index===undefined) return;
  if(manual) $('follow').checked=false;
  ui.selected=Number(index);
  renderRows();renderTrend();
  const key=ui.run+':'+ui.selected;
  if(ui.detailKey===key) return;
  try {
    const detail=await api('/api/round?run='+encodeURIComponent(ui.run)+'&index='+ui.selected);
    if(key!==ui.run+':'+ui.selected) return;
    // Commit the four decoded images together; never relabel a previous frame.
    const images=await Promise.all([['inputScene','scene_input'],['egoCamera','ego_camera'],['observerCamera','observer_camera'],['mergedBev','merged_bev']].map(async([id,kind])=>{
      const img=$(id).cloneNode(false);img.src=assetUrl(kind,detail.row.index);img.dataset.kind=kind;
      await img.decode();return [id,img];
    }));
    if(key!==ui.run+':'+ui.selected) return;
    for(const [id,img] of images) $(id).replaceWith(img);
    ui.detail=detail;ui.detailKey=key;renderDetail(detail);
  } catch(error) {showError('회차 상세 조회: '+error.message);}
}
function renderDetail(detail) {
  const row=detail.row;
  $('selectedRound').textContent=`${String(row.index+1).padStart(3,'0')}회차 · ${row.scenario} · ${row.session}차/${row.repeat}회`;
  const ego=detail.nodes.find(n=>n.role==='ego'),observer=detail.nodes.find(n=>n.role==='observer');
  $('egoActor').textContent=ego?'#'+ego.actor_id:'—';
  $('observerActor').textContent=observer?'#'+observer.actor_id:'—';
  $('egoTotal').textContent=f(ego?.elapsed_ms/1000)+'초';
  $('observerTotal').textContent=f(observer?.elapsed_ms/1000)+'초';
  const nodes=[ego,observer];
  const all=nodes.flatMap(n=>Object.values(n?.stages_ms || {}));
  const ceiling=Math.max(1,...all);
  const draw=(node,key,residual=false)=>{const value=residual?node?.residual_ms:node?.stages_ms[key];const width=Number.isFinite(value)?Math.max(0,value)/ceiling*100:0;return `<div class="stage-value ${node?.role==='observer'?'observer':''} ${residual?'residual':''}"><span><i style="width:${Math.min(100,width)}%"></i></span><b>${f(value,3)}</b></div>`;};
  $('stages').innerHTML=detail.stages.map(s=>`<div class="stage-row"><div class="stage-name" title="${s.key==='inference'?'입력검사·GPU 전송·모델 추론·출력 보정 포함':s.key==='protect'?'BEV 직렬화·문맥 결합·Groth16 증명 생성 포함':s.key==='verify'?'문맥 검사·Groth16 검증·BEV 복원 포함':s.label}">${esc(s.label)}</div>${draw(ego,s.key)}${draw(observer,s.key)}</div>`).join('')+`<div class="stage-row"><div class="stage-name residual" title="전체 처리시간에서 개별 계측 단계의 합을 뺀 값. 독립 단계의 직접 계측값이 아님.">미분해 구간 · 차이값</div>${draw(ego,'',true)}${draw(observer,'',true)}</div>`;
  $('subtimings').innerHTML=nodes.map(n=>`<div><strong>${n?.role==='ego'?'Ego':'감시자'}</strong><br>Groth16 증명 생성 ${f(n?.proof_ms)} ms<br>Groth16 증명 검증 ${f(n?.proof_verify_ms)} ms<br><span>상위 생성·검증 시간에 포함</span></div>`).join('');
  $('backend').textContent=nodes.every(n=>n?.backend==='REAL_GROTH16')?'REAL Groth16':'확인 필요';
  $('frameLabel').textContent=`${row.scenario} · 프레임 ${row.frame_id} · 동일 입력 시점`;
  $('processingFrame').textContent=`${row.scenario} · F${row.frame_id}`;
}
async function poll(force=false) {
  if(!ui.run) return;
  if(ui.polling){if(force) ui.pendingPoll=true;return;}
  ui.polling=true;
  const requestedRun=ui.run;
  try {
    const state=await api('/api/state?run='+encodeURIComponent(requestedRun));
    if(ui.run!==requestedRun) return;
    const previous=ui.state;ui.state=state;connection(true);showError('');renderSummary(state);
    if(state.rows.length) {
      let selected=ui.selected;
      if($('follow').checked) selected=state.rows.at(-1).index;
      else if(selected===null || selected>=state.rows.length) selected=state.worst_index;
      renderRows();renderTrend();await selectRound(selected);
    } else {renderRows();renderTrend();}
    if(!previous || previous.status!==state.status || force || Date.now()-ui.lastHistory>10000) await loadHistory();
    controls();
  } catch(error) {connection(false);showError('서버 조회 실패: '+error.message);}
  finally {ui.polling=false;if(ui.pendingPoll){ui.pendingPoll=false;queueMicrotask(()=>poll(true));}}
}
async function chooseRun(name) {
  ui.run=name;ui.state=null;ui.detail=null;ui.selected=null;ui.detailKey=null;
  $('selectedRound').textContent='회차 계측 대기';
  $('stages').innerHTML='<div class="empty">회차 계측 대기</div>';
  $('egoActor').textContent=$('observerActor').textContent='—';
  $('egoTotal').textContent=$('observerTotal').textContent='—초';
  $('subtimings').textContent='—';$('backend').textContent='—';$('frameLabel').textContent='프레임 —';
  for(const id of ['inputScene','egoCamera','observerCamera','mergedBev','liveScene']) $(id).removeAttribute('src');
  $('sceneEmpty').hidden=false;$('sceneStatus').textContent='대기';$('processingFrame').textContent='—';
  $('follow').checked=ui.history.find(r=>r.id===name)?.active || false;
  $('history').value=name;
  await poll(true);
  const mode=ui.state?.expected===3?'three':ui.state?.expected===5?'five':ui.state?.expected===1?'single':'full';
  document.querySelector(`input[name=mode][value="${mode}"]`).checked=true;
  if(ui.state?.protocol.scenarios.length===1)$('runScenario').value=ui.state.protocol.scenarios[0];
  controls();
}
$('history').addEventListener('change',event=>chooseRun(event.target.value));
document.querySelectorAll('input[name=mode]').forEach(input=>input.addEventListener('change',controls));
$('refresh').addEventListener('click',()=>poll(true));
$('rows').addEventListener('click',event=>{const row=event.target.closest('tr[data-index]');if(row) selectRound(Number(row.dataset.index),true);});
$('rows').addEventListener('keydown',event=>{if(event.key==='Enter'){const row=event.target.closest('tr[data-index]');if(row) selectRound(Number(row.dataset.index),true);}});
for(const id of ['filterScenario','filterStatus']) $(id).addEventListener('change',renderRows);
$('sortTime').addEventListener('click',()=>{ui.descending=!ui.descending;$('sortTime').setAttribute('aria-pressed',ui.descending);renderRows();});
$('follow').addEventListener('change',()=>{if($('follow').checked&&ui.state?.rows.length) selectRound(ui.state.rows.at(-1).index);});
$('download').addEventListener('click',()=>{$('downloadMenu').hidden=!$('downloadMenu').hidden;$('download').setAttribute('aria-expanded',!$('downloadMenu').hidden);});
document.addEventListener('click',event=>{if(!event.target.closest('.download-control')){$('downloadMenu').hidden=true;$('download').setAttribute('aria-expanded','false');}});
$('start').addEventListener('click',async()=>{
  ui.busy=true;controls();showError('');
  try {
    const mode=document.querySelector('input[name=mode]:checked').value;
    const result=await api('/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':ui.token},body:JSON.stringify({mode,scenario:$('runScenario').value})});
    await loadHistory();await chooseRun(result.run);$('follow').checked=true;
  } catch(error) {showError('실행 요청 실패: '+error.message);}
  finally {ui.busy=false;controls();}
});
$('stop').addEventListener('click',async()=>{
  ui.busy=true;controls();
  try {await api('/api/stop',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':ui.token},body:JSON.stringify({run:ui.run})});await poll(true);}
  catch(error){showError('중지 요청 실패: '+error.message);}
  finally{ui.busy=false;controls();}
});
document.querySelectorAll('.zoom').forEach(button=>button.addEventListener('click',()=>{
  if(!ui.detail) return;
  $('dialogImage').src=assetUrl(button.dataset.kind);$('dialogTitle').textContent=button.title+' · 프레임 '+ui.detail.row.frame_id;
  $('imageDialog').showModal();
}));
$('closeDialog').addEventListener('click',()=>$('imageDialog').close());
$('imageDialog').addEventListener('click',event=>{if(event.target===$('imageDialog')) $('imageDialog').close();});
async function init(){
  icons();
  try {
    const config=await api('/api/config');ui.token=config.token;ui.history=config.history;
    $('serverHost').textContent=config.hostname+' · '+config.user;
    const requested=new URLSearchParams(window.location.search).get('run');
    const selected=ui.history.some(r=>r.id===requested)?requested:config.default_run;
    await loadHistory();if(selected) await chooseRun(selected);else {connection(true);$('statusBadge').textContent='시험 대기';$('phase').textContent='실행 기록 없음';}
  } catch(error){connection(false);showError(error.message);}
  setInterval(()=>poll(),2000);
  setInterval(()=>pollScene(),1000);
}
let scenePolling=false,lastScene='';
async function pollScene(){
  if(!ui.run || scenePolling) return;
  scenePolling=true;const requested=ui.run;
  try{
    const scene=await api('/api/live?run='+encodeURIComponent(requested));
    if(ui.run!==requested) return;
    if(scene.source==='live_carla_rgb_sensor'){
      const key=requested+':'+scene.frame_id;
      if(lastScene!==key){
        const img=$('liveScene').cloneNode(false);
        img.src='/api/live-image?run='+encodeURIComponent(requested)+'&frame='+scene.frame_id;
        await img.decode();
        if(ui.run!==requested) return;
        $('liveScene').replaceWith(img);lastScene=key;
      }
      $('sceneEmpty').hidden=true;
      $('sceneFrame').textContent='현재 장면 F'+scene.frame_id;
      $('sceneScenario').textContent=scene.scenario;
      $('sceneEgo').textContent='#'+scene.ego_actor_id;
      $('sceneTime').textContent=f(scene.capture_sim_ns/1e9,1)+'초';
      const age=(Date.now()-scene.updated_wall_ns/1e6)/1000;
      const fresh=scene.active && age<4;
      $('sceneStatus').textContent=fresh?'실행 중':scene.active?'장면 지연':'종료 장면';
      $('sceneFreshness').textContent='장면 갱신 '+f(Math.max(0,age),1)+'초 전';
    }else{
      $('liveScene').removeAttribute('src');$('sceneEmpty').hidden=false;
      $('sceneStatus').textContent=scene.active?'장면 준비':'대기';
      $('sceneScenario').textContent=scene.scenario || ui.state?.current?.scenario || '—';
      $('sceneFrame').textContent='현재 장면 프레임 —';
      $('sceneEgo').textContent=$('sceneTime').textContent=$('sceneFreshness').textContent='—';lastScene='';
    }
  }catch(error){$('sceneStatus').textContent='장면 확인 필요';}
  finally{scenePolling=false;}
}
init();
