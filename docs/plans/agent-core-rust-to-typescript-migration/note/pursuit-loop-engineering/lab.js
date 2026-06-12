/* ===== FIG.11 · L-1 lab ===== */
(function(){
  const $ = (id)=>document.getElementById(id);
  const esc = (s)=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const TRANS = {
    loop:      {label:'re-enter — failed pass cleared', kind:'d-amber'},
    loop_adv:  {label:'re-enter + advisor reminder injected', kind:'d-amber'},
    exit_ok:   {label:'exit loop — success', kind:'d-green'},
    exit_fail: {label:'exit loop — failure surfaced to caller', kind:'d-red'},
  };
  const MODES = [
    {id:'none',    nm:'no terminal tool', ds:'Phase 04.10 text mode — a gated bare-text turn finishes the run; the verdict is prose'},
    {id:'boolean', nm:'boolean submit',   ds:'submit_main_outcome(success, summary) — L-1 is a while-loop'},
    {id:'custom',  nm:'custom schema',    ds:'user-defined outcome states — L-1 becomes a programmable automaton'},
  ];
  const lab = {
    mode:'custom',
    states:[
      {name:'done',            t:'exit_ok'},
      {name:'needs_more_work', t:'loop'},
      {name:'needs_review',    t:'loop_adv'},
      {name:'blocked',         t:'exit_fail'},
    ],
    advisor:'Submit only when the full intent is satisfied. If verification failed, submit a failing status — findings in prose under a passing status is a verification pass.',
    iter:0, running:false,
  };
  let passEl=null;

  const statesFor = ()=> lab.mode==='boolean'
    ? [{name:'success: true',t:'exit_ok'},{name:'success: false',t:'loop'}]
    : (lab.mode==='none' ? [] : lab.states);

  function renderModes(){
    $('labModes').innerHTML = MODES.map(m=>
      '<div class="lab-mode'+(lab.mode===m.id?' sel':'')+'" data-m="'+m.id+'"><span class="nm">'+m.nm+'</span><span class="ds">'+m.ds+'</span></div>').join('');
    [...$('labModes').children].forEach(el=>el.onclick=()=>{lab.mode=el.dataset.m;resetSim();renderAll();});
  }
  function renderStates(){
    if(lab.mode!=='custom'){
      $('labStates').innerHTML = '<div class="lab-fixed">'+(lab.mode==='boolean'
        ? 'fixed alphabet: success ∈ {true, false} · true → exit, false → re-enter'
        : 'no terminal tool — no alphabet, no δ. Text mode (04.10): a gated bare-text turn finishes the run; the final text IS the submission.')+'</div>';
      $('labAdd').style.display='none'; return;
    }
    $('labAdd').style.display='';
    $('labStates').innerHTML = lab.states.map((s,i)=>
      '<div class="lab-st"><input type="text" value="'+esc(s.name)+'" data-i="'+i+'" maxlength="24">'+
      '<select data-i="'+i+'">'+Object.entries(TRANS).map(([k,v])=>'<option value="'+k+'"'+(s.t===k?' selected':'')+'>'+v.label+'</option>').join('')+'</select>'+
      '<button class="lab-rm" data-i="'+i+'" title="remove">&times;</button></div>').join('');
    $('labStates').querySelectorAll('input').forEach(el=>el.oninput=()=>{
      lab.states[+el.dataset.i].name=(el.value.trim().replace(/\s+/g,'_'))||('state_'+(+el.dataset.i+1));
      renderCard();renderDelta();});
    $('labStates').querySelectorAll('select').forEach(el=>el.onchange=()=>{lab.states[+el.dataset.i].t=el.value;renderCard();renderDelta();});
    $('labStates').querySelectorAll('.lab-rm').forEach(el=>el.onclick=()=>{lab.states.splice(+el.dataset.i,1);resetSim();renderAll();});
  }
  function renderCard(){
    let schema;
    if(lab.mode==='none') schema='— no terminal tool —';
    else if(lab.mode==='boolean') schema='{ success: boolean, summary: string }';
    else schema='{ status: '+(lab.states.map(s=>'"'+s.name+'"').join(' | ')||'∅')+', summary: string, …your fields }';
    $('labCard').innerHTML = lab.mode==='none'
      ? '<div><span class="k2">tool</span>—</div><div><span class="k2">note</span>the gate relocates into the loop (04.10): bare text finishes when no steers are pending and no sessions are open; the final text IS the submission</div>'
      : '<div><span class="k2">tool</span>submit_main_outcome</div>'+
        '<div><span class="k2">docstring</span>Terminate this run and report the outcome. The verdict rides the gate field, never prose.</div>'+
        '<div><span class="k2">schema</span><span class="s2">'+esc(schema)+'</span></div>'+
        '<div><span class="k2">advisor</span>'+esc(lab.advisor)+'</div>';
  }
  function renderDelta(){
    const sts=statesFor();
    $('labDelta').innerHTML = sts.length
      ? sts.map(s=>'<tr data-s="'+esc(s.name)+'"><td>σ = "'+esc(s.name)+'"</td><td class="'+TRANS[s.t].kind+'">δ(σ) → '+TRANS[s.t].label+'</td></tr>').join('')
      : '<tr><td>—</td><td class="d-amber">text exit (04.10): gated bare-text turn → completed · submission is prose — δ has nothing to look up</td></tr>';
  }
  function renderAll(){renderModes();renderStates();renderCard();renderDelta();}

  const trEl=$('labTr');
  function addLine(html,cls){const d=document.createElement('div');d.className='pl-line'+(cls?' '+cls:'');d.innerHTML=html;(passEl||trEl).appendChild(d);trEl.scrollTop=trEl.scrollHeight;return d;}
  const chip=(k,t)=>'<span class="wl-k '+k+'">'+t+'</span>';
  function later(d,f){setTimeout(()=>{if(lab.running)f();},d);}
  function hist(name,mark){$('labHist').innerHTML+=' iter '+lab.iter+' → '+esc(name)+' '+mark+' ·';}
  function banner(kind,txt){addLine('<span class="pl-banner '+kind+'">'+txt+'</span>');}
  function stop(){lab.running=false;$('labRun').disabled=false;}

  function startIter(){
    lab.iter++;$('labIter').textContent='iteration '+lab.iter;
    passEl=document.createElement('div');trEl.appendChild(passEl);
    addLine('<span class="pl-it">L0 · iteration '+lab.iter+'</span>');
    later(300,()=>addLine(chip('k-think','think')+' <span class="pl-dim">'+(lab.iter===1?'frame the intent as one verifiable pursuit_goal':'amend the goal with what the last pass taught — read the context store')+'</span>'));
    later(900,()=>addLine(chip('k-tool','tool_call')+' delegate_pursuit(pursuit_goal'+(lab.iter>1?'&prime;':'')+')'));
    later(1700,()=>addLine(lab.iter===1
      ?'<span class="pl-red">&#10229; settle(): Failed · leg_2 attempt budget exhausted</span>'
      :'<span class="pl-green">&#10229; settle(): Success · all legs success</span>'));
    later(2300,()=>addLine(chip('k-think','think')+' <span class="pl-dim">review outcome vs intent — decide what to submit</span>'));
    later(2800,askChoice);
  }
  function askChoice(){
    addLine('<span class="pl-dim">your move — what does the agent submit?</span>');
    const wrap=addLine('','');
    const opts = lab.mode==='none' ? [{name:'reply with final text (no tool call)',t:'__none'}] : statesFor();
    opts.forEach(s=>{
      const b=document.createElement('button');b.className='pl-chip';b.textContent=s.name;
      b.onclick=()=>{wrap.remove();s.t==='__none'?onNone():onSubmit(s);};
      wrap.appendChild(b);wrap.appendChild(document.createTextNode(' '));
    });
  }
  function highlight(name){
    $('labDelta').querySelectorAll('tr').forEach(r=>r.classList.toggle('act',r.dataset.s===name));
    setTimeout(()=>$('labDelta').querySelectorAll('tr').forEach(r=>r.classList.remove('act')),1600);
  }
  function onSubmit(s){
    const payload = lab.mode==='boolean' ? '{ '+s.name+', summary: "…" }' : '{ status: "'+s.name+'", summary: "…" }';
    addLine(chip('k-tool','tool_call')+' submit_main_outcome('+esc(payload)+')');
    highlight(s.name);
    later(500,()=>{
      if(s.t==='exit_ok'){hist(s.name,'✓');banner('ok','accepted · acceptance state → L-1 stops re-invoking · loop exits');stop();}
      else if(s.t==='exit_fail'){hist(s.name,'✗');banner('fail','δ(σ) = exit·failure → L-1 stops · failure surfaced to the caller');stop();}
      else{
        hist(s.name,'↺');
        addLine('<span class="pl-amber">&#10229; returns continue · δ("'+esc(s.name)+'") = re-enter · failed pass cleared</span>');
        if(s.t==='loop_adv')addLine('<span class="pl-cyan">L-1 ▸ transition function injects the advisor reminder into the next turn</span>');
        if(lab.iter>=5)addLine('<span class="pl-dim">L-1 note: no budget at this layer — the caller must self-limit (<a href="index.html#scorecard">§08</a>)</span>');
        later(900,()=>{passEl.classList.add('pl-fade');later(450,()=>{passEl.remove();startIter();});});
      }
    });
  }
  function onNone(){
    addLine('<span class="pl-amber">… bare-text turn · step-6 gate: steer queue empty ✓ · openBackgroundSessionCount == 0 ✓</span>');
    later(500,()=>{hist('(text)','✓');banner('amb','completed · submission = assistantText(final_message) — clean exit through the relocated gate (<a href="operator-harness.html#native-loop">§09</a>), but the verdict is prose: to loop on it, L-1 needs judgment, not lookup');stop();});
  }
  function resetSim(){lab.running=false;lab.iter=0;passEl=null;trEl.innerHTML='';$('labHist').innerHTML='';$('labIter').textContent='';$('labRun').disabled=false;}

  $('labAdv').value=lab.advisor;
  $('labAdv').oninput=()=>{lab.advisor=$('labAdv').value;renderCard();};
  $('labAdd').onclick=()=>{if(lab.states.length<6){lab.states.push({name:'state_'+(lab.states.length+1),t:'loop'});renderStates();renderCard();renderDelta();}};
  $('labRun').onclick=()=>{
    if(lab.running)return;
    if(lab.mode==='custom'&&!lab.states.length){resetSim();banner('amb','add at least one outcome state — an empty alphabet gates nothing');return;}
    resetSim();lab.running=true;$('labRun').disabled=true;startIter();
  };
  $('labReset').onclick=resetSim;
  renderAll();
})();
