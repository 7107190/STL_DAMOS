(() => {
  const panel=document.getElementById('calculationReference');
  if(!panel)return;
  const $=id=>document.getElementById(id),history=$('history'),selector=$('sourceSection');
  let ticket=0,lastKey='',shownKey='',rendered='';
  const currentRun=()=>{
    const value=$('runId')?.textContent.trim()||history.value;
    return /^live_metric[23]_[0-9TZ_]+[a-f0-9]{8}$/.test(value)?value:null;
  };
  async function load(force=false){
    if(!panel.open)return;
    const run=currentRun(),section=selector.value,key=(run||'')+':'+section;
    if(!force&&key===lastKey)return;
    lastKey=key;const request=++ticket;
    if(shownKey!==key){
      $('sourceStatus').textContent='코드 확인 중';$('sourceStatus').className='source-status';
      $('sourceCode').replaceChildren();$('sourceFile').textContent='—';$('sourceFunction').textContent='—';
      $('sourceCurrentHash').textContent=$('sourceRecordedHash').textContent='—';rendered='';shownKey=key;
    }
    $('sourceError').hidden=true;
    $('sourceRun').textContent=run||'실행 미선택';
    try{
      const query=new URLSearchParams();if(run)query.set('run',run);if(section)query.set('section',section);
      const response=await fetch('/api/source?'+query,{cache:'no-store'}),data=await response.json();
      if(!response.ok)throw Error(data.error||response.status);
      if(request!==ticket||run!==currentRun())return;
      if(JSON.stringify([...selector.options].map(o=>({id:o.value,label:o.textContent})))!==JSON.stringify(data.sections)){
        selector.replaceChildren(...data.sections.map(s=>{const option=document.createElement('option');option.value=s.id;option.textContent=s.label;return option;}));
      }
      selector.value=data.section;lastKey=shownKey=(run||'')+':'+data.section;
      $('sourceFile').textContent=data.file;
      $('sourceFunction').textContent=`${data.function} · ${data.start_line}–${data.end_line}행`;
      $('sourceCurrentHash').textContent=data.sha256;$('sourceRecordedHash').textContent=data.recorded_sha256||'기록 없음';
      const status=data.matches_selected_run;
      $('sourceStatus').textContent=status===true?'선택 실행과 코드 일치':status===false?'선택 실행과 코드 다름':'실행 코드 해시 기록 대기';
      $('sourceStatus').className='source-status '+(status===true?'matched':status===false?'different':'');
      const signature=data.sha256+':'+data.start_line+':'+data.end_line;
      if(rendered===signature)return;
      const lines=data.code.split('\n').map((text,index)=>{
        const line=document.createElement('span');line.className='source-line';
        const number=document.createElement('span');number.className='source-number';number.textContent=data.start_line+index;number.setAttribute('aria-hidden','true');
        const code=document.createElement('code');code.textContent=text||' ';
        line.append(number,code);return line;
      });
      $('sourceCode').replaceChildren(...lines);
      rendered=signature;
    }catch(error){if(request===ticket){$('sourceStatus').textContent='코드 확인 필요';$('sourceStatus').className='source-status different';$('sourceError').textContent=error.message;$('sourceError').hidden=false;}}
  }
  panel.addEventListener('toggle',()=>{if(panel.open)load(true);});
  selector.addEventListener('change',()=>load(true));
  history.addEventListener('change',()=>load(true));
  new MutationObserver(()=>load()).observe(history,{childList:true});
  if($('runId'))new MutationObserver(()=>load()).observe($('runId'),{childList:true});
  setInterval(()=>load(true),10000);
})();
