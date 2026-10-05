"use strict";
// Staff credentials live in this tab's memory only. Staff UI must be served on
// a private staff network/VPN in production (the API enforces credentials too).
let staffToken = "";
let queueCursor = null;
let queueEpoch = 0;
let queueRequest = 0;
let queueLoading = 0;
let seenRequests = new Set();
let lastQueueSignal = null, summaryLoading = false;
let reconcileCursor = null;
const PAGE_SIZE = 50;
const $ = id => document.getElementById(id);
const VERIFY = new Set(["housekeeping", "facilities", "dining", "tour"]);
async function call(url, body, idempotencyKey = null) {
  const response = await fetch(url, {
    method: body ? "POST" : "GET", cache: "no-store",
    headers: {Authorization: `Bearer ${staffToken}`,
      ...(body ? {"Content-Type": "application/json"} : {}),
      ...(idempotencyKey ? {"Idempotency-Key": idempotencyKey} : {})},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || `HTTP ${response.status}`);
  return data;
}
async function refresh(reset = true) {
  if(!reset && queueLoading)return;
  if(reset){queueCursor=null;seenRequests=new Set();queueEpoch++;$("queue").replaceChildren();}
  const loadId=++queueRequest;
  queueLoading=loadId;
  try {
    if(reset){
      const emergencyQueue=$("emergency-queue"); emergencyQueue.replaceChildren();
      const alerts=await call('/staff/emergencies?limit=50');
      for(const alert of alerts){
        const card=document.createElement('article');card.className='card';
        card.style.border='2px solid #b91c1c';
        const heading=document.createElement('h2');heading.textContent=`EMERGENCY · priority ${alert.priority} · ${alert.status}`;
        const details=document.createElement('p');details.textContent=alert.details;
        const meta=document.createElement('small');meta.textContent=`${alert.language} · ${alert.source} · ${new Date(alert.created_at*1000).toLocaleString()}`;
        card.append(heading,details,meta);
        if(alert.status!=='resolved'){
          const actions=document.createElement('div');actions.className='actions';
          const action=alert.status==='open'?'acknowledge':'resolve';
          const button=document.createElement('button');button.className='small';button.type='button';
          button.textContent=action==='acknowledge'?'Acknowledge emergency':'Resolve emergency';
          button.onclick=async()=>{button.disabled=true;try{
            await call(`/staff/emergencies/${encodeURIComponent(alert.id)}/transition`,{action,note:''});await refresh(true);
          }catch(error){$('error').textContent=error.message;}finally{button.disabled=false;}};
          actions.append(button);card.append(actions);
        }
        emergencyQueue.append(card);
      }
    }
    const epoch = queueEpoch;
    const requestedCursor = queueCursor;
    const filters = new URLSearchParams({limit:String(PAGE_SIZE)});
    if(requestedCursor)filters.set('cursor',requestedCursor);
    if($("filter-status").value)filters.set("status",$("filter-status").value);
    if($("filter-kind").value)filters.set("kind",$("filter-kind").value);
    if($("filter-reference").value.trim())filters.set('reference',$("filter-reference").value.trim().toLowerCase());
    const page = await call(`/staff/requests/page?${filters}`);
    if(epoch!==queueEpoch || loadId!==queueLoading || requestedCursor!==queueCursor)return;
    queueCursor=page.next_cursor;
    $("load-more").hidden=!queueCursor;
    for (const item of page.items) {
      // Another employee may have moved a previously displayed ticket into a
      // later priority group. Reappearance must not duplicate the card.
      if(seenRequests.has(item.id))continue;
      seenRequests.add(item.id);
      const card = document.createElement("article"); card.className = "card";
      const heading = document.createElement("h2"); heading.textContent = `${item.kind} · ${item.status}`;
      const description = document.createElement("p"); description.textContent = item.details;
      const meta = document.createElement("small");
      meta.textContent = `${item.language} · ${new Date(item.created_at * 1000).toLocaleString()}`;
      card.append(heading, description, meta);
      const ticket=document.createElement('code');ticket.textContent=item.id;
      ticket.style.overflowWrap='anywhere';card.append(ticket);
      if (item.staff_note) {
        const note = document.createElement("p"); note.textContent = `Last staff note: ${item.staff_note}`;
        card.append(note);
      }
      // Audit is fetched only on demand, using the separately authorized staff
      // endpoint. Text is rendered literally, never injected as HTML.
      const historyButton=document.createElement("button");
      historyButton.type="button";historyButton.className="small";
      historyButton.textContent="Request details & audit history";
      historyButton.setAttribute("aria-expanded","false");
      const history=document.createElement("div");history.hidden=true;
      const auditEntries=document.createElement("ol");
      history.append(auditEntries);
      let auditCursor=0;
      const nextEvents=document.createElement("button");
      nextEvents.type="button";nextEvents.className="small";
      nextEvents.textContent="Older audit entries";
      nextEvents.hidden=true;history.append(nextEvents);
      async function loadAudit(){
        try{
          const events=await call(`/staff/requests/${encodeURIComponent(item.id)}/audit?limit=50&after_id=${auditCursor}`);
          for(const event of events){
            const line=document.createElement("li");
            line.textContent=`${new Date(event.at*1000).toLocaleString()} · ${event.action} · ${event.actor} · independently verified: ${Boolean(event.independently_verified)} · ${event.note||"(no note)"}`;
            auditEntries.append(line);auditCursor=event.id;
          }
          nextEvents.hidden=events.length<50;
        }catch(error){$("error").textContent=error.message;}
      }
      historyButton.onclick=async()=>{
        history.hidden=!history.hidden;
        historyButton.setAttribute("aria-expanded",String(!history.hidden));
        if(!history.hidden && !auditCursor){
          try{
            const detail=await call(`/staff/requests/${encodeURIComponent(item.id)}`);
            const description=document.createElement("p");
            description.textContent=`Request ${detail.id} · ${detail.status} · verifier: ${detail.verified_by||"none"}`;
            history.prepend(description);await loadAudit();
          }catch(error){$("error").textContent=error.message;}
        }
      };
      nextEvents.onclick=loadAudit;
      card.append(historyButton,history);
      const changePending = item.guest_change_state === 'cancel_requested' || item.guest_change_state === 'modify_requested';
      if(changePending){
        const panel=document.createElement('div');panel.className='change-review';
        const message=document.createElement('p');
        message.textContent=item.guest_change_state==='cancel_requested' ?
          'Guest requested cancellation. The original request remains active until you review it.' :
          `Guest requested changes: ${JSON.stringify(item.guest_change_payload||{})}`;
        panel.append(message);
        const changeNote=document.createElement('textarea');changeNote.rows=2;changeNote.maxLength=300;
        changeNote.placeholder='Reason for accepting or rejecting this guest change (minimum 8 characters)';
        panel.append(changeNote);
        const changeActions=document.createElement('div');changeActions.className='actions';
        for(const action of ['approve','reject']){
          const button=document.createElement('button');button.type='button';button.className='small';
          button.textContent=action==='approve'?'Accept guest change':'Reject guest change';
          button.onclick=async()=>{
            if(changeNote.value.trim().length<8){$('error').textContent='Please record at least eight characters in the review note.';return;}
            if(!confirm(`${button.textContent}?`))return;button.disabled=true;
            try{await call(`/staff/requests/${encodeURIComponent(item.id)}/guest-change`,{action,note:changeNote.value.trim()});await refresh();}
            catch(error){$('error').textContent=error.message;}finally{button.disabled=false;}
          };
          changeActions.append(button);
        }
        panel.append(changeActions);card.append(panel);
      }
      if(item.guest_change_state === 'modified' && item.effective_payload){
        const approvedChange=document.createElement('p');approvedChange.className='muted';
        approvedChange.textContent=`Approved guest changes: ${JSON.stringify(item.effective_payload)}`;
        card.append(approvedChange);
      }
      const changeFinal = item.guest_change_state === 'cancelled';
      const options = (changePending || changeFinal) ? [] : (item.status === "pending_staff" ? ["approve", "reject"] :
        item.status === "approved" ? ["complete"] : []);
      if (options.length) {
        const note = document.createElement("textarea");
        note.rows = 2; note.maxLength = 300;
        note.placeholder = "Staff review / reason / fulfillment note (no personal identifiers)";
        note.setAttribute("aria-label", `Staff review note for ${item.kind}`);
        card.append(note);
        let verified = null;
        if (item.status === "pending_staff" && VERIFY.has(item.kind)) {
          const label = document.createElement("label");
          verified = document.createElement("input"); verified.type = "checkbox";
          verified.setAttribute("aria-label", `Verified ${item.kind} request`);
          label.append(verified, document.createTextNode(
            item.kind === "housekeeping" || item.kind === "facilities" ?
              " Room and guest independently verified by staff" :
              " Availability and applicable charges independently checked by staff"));
          card.append(label);
        }
        const buttons = document.createElement("div"); buttons.className = "actions";
        let retryOperation = null;
        for (const action of options) {
          const button = document.createElement("button");
          button.textContent = action; button.className = "small";
          button.onclick = async () => {
            if (note.value.trim().length < 8) {
              $("error").textContent = "Please record at least eight characters in the review note.";
              return;
            }
            if (action === "approve" && verified && !verified.checked) {
              $("error").textContent = "Independent staff verification is required.";
              return;
            }
            if (!confirm(`Confirm ${action}? This records a staff decision, not an automatic booking.`)) return;
            const payload = {action, verified: !!(verified && verified.checked), note: note.value.trim()};
            const signature = JSON.stringify(payload);
            if (!retryOperation || retryOperation.signature !== signature) {
              retryOperation = {signature, key: crypto.randomUUID()};
            }
            button.disabled = true;
            try {
              await call(`/staff/requests/${encodeURIComponent(item.id)}/transition`,
                payload, retryOperation.key);
              retryOperation = null;
              await refresh();
            } catch (error) { $("error").textContent = error.message; }
            finally { button.disabled = false; }
          };
          buttons.append(button);
        }
        card.append(buttons);
      }
      $("queue").append(card);
    }
    $("error").textContent = "";
  } catch (error) { if(loadId===queueLoading)$("error").textContent = error.message; }
  finally { if(loadId===queueLoading)queueLoading=0; }
}
// Authenticated bounded polling; preserves an edited/paginated staff view.
async function pollQueue(){
  if(!staffToken || summaryLoading || document.hidden)return;
  summaryLoading=true;const token=staffToken;
  try{
    const summary=await call('/staff/queue/summary');
    if(token!==staffToken)return;
    const signal=`${summary.queue_revision}:${summary.pending_count}:${summary.latest_pending_id||''}:${summary.emergency_revision||0}:${summary.emergency_count||0}:${summary.latest_emergency_id||''}`;
    const oldest=summary.oldest_pending_at===null?'':
      ` · oldest pending ${Math.max(0,Math.floor(Date.now()/1000-summary.oldest_pending_at)/60)} min`;
    const emergencyOldest=summary.oldest_emergency_at===null?'':
      ` · oldest emergency ${Math.max(0,Math.floor(Date.now()/1000-summary.oldest_emergency_at)/60)} min`;
    $("queue-summary").textContent=`EMERGENCY: ${summary.emergency_count||0}${emergencyOldest} · Pending review: ${summary.pending_count} · Approved: ${summary.approved_count}${oldest}`;
    $("queue-summary").hidden=false;
    if(lastQueueSignal!==null && signal!==lastQueueSignal){
      const editing=$("queue").contains(document.activeElement);
      if(!editing && !queueCursor && !queueLoading && !$("filter-reference").value.trim()){
        await refresh(true);$("queue-updates").hidden=true;
      }else{
        $("queue-updates").hidden=false;
        $("queue-updates").textContent='Queue changed — refresh to see current requests';
      }
    }
    lastQueueSignal=signal;
  }catch(error){
    if(token!==staffToken)return;
    $("error").textContent=error.message;
    if(/HTTP (401|403)/.test(error.message)){
      staffToken='';lastQueueSignal=null;$("queue-summary").hidden=true;
      $("queue-updates").hidden=true;
      $("reconcile-button").disabled=true;reconcileCursor=null;
    }
  }finally{summaryLoading=false;}
}
$("login").onsubmit = async event => {
  event.preventDefault(); staffToken = $("token").value; $("token").value = "";
  $("reconcile-button").disabled=!staffToken;reconcileCursor=null;$("reconcile-result").textContent='';
  lastQueueSignal=null;$("queue-updates").hidden=true;
  await refresh();await pollQueue();
};
$("queue-updates").onclick=async()=>{await refresh(true);$("queue-updates").hidden=true;};
setInterval(pollQueue,15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void pollQueue();});
$("refresh").onclick = async () => {await refresh(true);$("queue-updates").hidden=true;};
$("load-more").onclick = () => refresh(false);
$("filter-status").onchange=()=>refresh(true);
$("filter-kind").onchange=()=>refresh(true);
function searchReference(){
  if($("filter-reference").value.trim()){
    // An exact handoff lookup should not disappear behind an unrelated
    // category/status filter previously selected by a receptionist.
    $("filter-status").value='';$("filter-kind").value='';
  }
  refresh(true);
}
$("filter-reference").onchange=searchReference;
$("filter-reference").onkeydown=event=>{
  if(event.key==='Enter'){event.preventDefault();searchReference();}
};
$("reconcile-button").onclick=async()=>{
  if(!staffToken)return;
  const button=$("reconcile-button"), output=$("reconcile-result");
  // A new batch is explicit: do not automatically scan arbitrarily large data.
  if(!confirm('Reconcile up to 25 existing workflow checkpoints from committed SQLite state? No service decision will be changed.'))return;
  const params=new URLSearchParams({limit:'25'});
  if(reconcileCursor){params.set('after_updated_at',String(reconcileCursor.after_updated_at));params.set('after_id',reconcileCursor.after_id);}
  button.disabled=true;
  try{
    const result=await call(`/staff/orchestration/reconcile-deferred?${params}`,{});
    reconcileCursor=result.more?result.next_cursor:null;
    output.textContent=`Checked ${result.checked}; reconciled ${result.reconciled}; current ${result.already_current}; deferred ${result.deferred}. ${result.more?'Additional batch available.':'Scan complete.'}`;
    button.textContent=result.more?'Reconcile next 25':'Reconcile deferred orchestration';
    // No inferred success: a deferred count must be visible for operator review.
  }catch(error){output.textContent=error.message;
    if(/HTTP (401|403)/.test(error.message)){staffToken='';reconcileCursor=null;}
  }finally{button.disabled=!staffToken;}
};
$("metrics-button").onclick = async () => {
  try {
    const data = await call("/staff/metrics");
    $("metrics").textContent = data.counters.map(x =>
      `${x.day} ${x.language} ${x.metric}: ${x.count}`).join("\n") || "No events yet";
    const summary=$("latency-summary");summary.replaceChildren();
    const rows=Array.isArray(data.latency)?data.latency:[];
    if(!rows.length && !(data.slm_throughput||[]).length){summary.textContent="No latency samples recorded yet.";return;}
    for(const row of (data.slm_throughput||[])){
      const card=document.createElement('article'),heading=document.createElement('h3');
      heading.textContent=`SLM tokens/sec · ${row.language}`;
      const values=document.createElement('p');
      values.textContent=`p50 ≤ ${row.p50_upper_tokens_per_sec} · p95 ≤ ${row.p95_upper_tokens_per_sec} · p99 ≤ ${row.p99_upper_tokens_per_sec}`;
      const source=document.createElement('small');
      source.textContent=`${row.samples} observations · model-reported · approximate bucket bounds`;
      card.append(heading,values,source);summary.append(card);
    }
    for(const row of rows){
      const card=document.createElement("article"),heading=document.createElement("h3");
      heading.textContent=`${row.stage} · ${row.language}`;
      const percentiles=document.createElement("p");
      const show=value=>value===null?"> maximum bucket":`≤ ${value} ms`;
      percentiles.textContent=`p50 ${show(row.p50_upper_ms)} · p95 ${show(row.p95_upper_ms)} · p99 ${show(row.p99_upper_ms)}`;
      const source=document.createElement("small");
      source.textContent=`${row.samples} samples · ${row.origin} · approximate bucket bounds`;
      card.append(heading,percentiles,source);summary.append(card);
    }
  } catch (error) { $("error").textContent = error.message; }
};
