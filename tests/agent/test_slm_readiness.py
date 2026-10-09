import io
import json
from threading import Event, Thread
from urllib.request import Request

import pytest
from concierge_kiosk.runtime import local_ai, local_http
from concierge_kiosk.runtime.admission import AudioAdmission
from tools.runtime.agent_stabilization_smoke import CallBoundary, prompt_diagnostics

PIN='a'*64
BASE='http://127.0.0.1:11434'


@pytest.mark.parametrize('installed,resident,expected', [
    ([{'name':'model','digest':PIN}],[], 'MODEL_NOT_LOADED'),
    ([{'name':'model','digest':PIN}],[{'name':'model','digest':PIN}], 'MODEL_READY'),
    (None,[], 'MODEL_UNAVAILABLE'),
    ([{'name':'model','digest':'b'*64}],[], 'MODEL_UNAVAILABLE'),
    ([{'name':'model','digest':PIN}],[{'name':'model','digest':'b'*64}], 'MODEL_UNAVAILABLE'),
    ([{'name':'model','digest':PIN}],None, 'MODEL_UNAVAILABLE'),
    ([{'name':'model','digest':None}],[], 'MODEL_UNAVAILABLE'),
    ([{'name':'model','digest':PIN}],[{'name':'model','digest':None}], 'MODEL_UNAVAILABLE'),
])
def test_residency_requires_identity_and_ps(monkeypatch,installed,resident,expected):
    calls=[]
    def get(base,endpoint,timeout):
        calls.append(endpoint)
        return installed if endpoint=='/api/tags' else resident
    monkeypatch.setattr(local_ai,'_ollama_models',get)
    assert local_ai.model_residency(BASE,'model',PIN)['state']==expected
    assert calls==['/api/tags','/api/ps']


def test_loading_is_registered_and_cleared_on_failure(monkeypatch):
    monkeypatch.setattr(local_ai,'_ollama_models',lambda b,e,t: [{'name':'model','digest':PIN}] if e=='/api/tags' else [])
    with pytest.raises(TimeoutError):
        with local_ai._model_loading(BASE,'model'):
            assert local_ai.model_residency(BASE,'model',PIN)['state']=='MODEL_LOADING'
            with pytest.raises(RuntimeError):
                with local_ai._model_loading(BASE,'model'): pass
            raise TimeoutError()
    assert local_ai.model_residency(BASE,'model',PIN)['state']=='MODEL_NOT_LOADED'


def test_startup_and_guest_share_the_existing_lane(monkeypatch):
    entered,release=Event(),Event();admission=AudioAdmission()
    def preload(*a,**kw):
        entered.set()
        assert not admission.try_enter_slm('guest')
        assert release.wait(2)
        return {'done':True}
    monkeypatch.setattr(local_ai,'preload_local_slm',preload)
    worker=Thread(target=local_ai.warm_local_slm,args=(BASE,'model'),kwargs={'admission':admission})
    worker.start()
    try:
        assert entered.wait(2)
        assert not admission.try_enter_slm('guest')
    finally:
        release.set();worker.join(2)
    assert admission.try_enter_slm('guest')
    admission.leave_slm()
    assert admission.try_enter_slm('guest')
    assert local_ai.warm_local_slm(BASE,'model',admission=admission) is False
    admission.leave_slm()


class Response(io.BytesIO):
    status=200
    headers={}


def test_preload_is_one_empty_message_call_with_finite_residency(monkeypatch):
    requests=[]
    def opened(request,timeout):
        requests.append((json.loads(request.data),timeout))
        return Response(b'{"done":true,"load_duration":123}')
    monkeypatch.setattr(local_http._OPENER,'open',opened)
    result=local_ai.preload_local_slm(BASE,'model',timeout=30,num_gpu=0)
    assert result['load_duration']==123
    assert len(requests)==1
    payload,timeout=requests[0]
    assert payload['messages']==[] and payload['keep_alive']=='5m'
    assert payload['options']=={'num_predict':1,'num_ctx':4096,'num_gpu':0}
    assert timeout==30


def test_preload_cancellation_before_http_and_admission_cleanup(monkeypatch):
    monkeypatch.setattr(local_http._OPENER,'open',lambda *a,**kw: pytest.fail('No cancelled HTTP'))
    with pytest.raises(InterruptedError):
        local_ai.preload_local_slm(BASE,'model',should_cancel=lambda: True)
    admission=AudioAdmission()
    monkeypatch.setattr(local_ai,'preload_local_slm',lambda *a,**kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError): local_ai.warm_local_slm(BASE,'model',admission=admission)
    assert admission.try_enter_slm('guest');admission.leave_slm()


def request(model='model',path='/api/chat'):
    return Request(BASE+path,data=json.dumps({'model':model,'messages':[],
        'options':{'num_gpu':0}}).encode(),method='POST')


def test_accounting_holds_lane_through_stream_and_denies_extra_calls():
    opened=[]
    def open_request(req,timeout):
        opened.append(req)
        return Response(b'{"done":true,"eval_count":2,"eval_duration":100}\n')
    boundary=CallBoundary(open_request,'model');boundary.phase='PRELOAD'
    with boundary.open(request(),timeout=30) as response:
        boundary.phase='NLU'
        with pytest.raises(ValueError): boundary.open(request(),timeout=3)
        assert len(list(response))==1
    with boundary.open(request(),timeout=3) as response: list(response)
    with pytest.raises(ValueError): boundary.open(request(),timeout=3)
    assert len(opened)==len(boundary.calls)==2
    assert boundary.calls[1]['timings']['eval_count']==2


@pytest.mark.parametrize('model,path',[('other','/api/chat'),('model','/api/embed'),('model','/api/generate')])
def test_boundary_blocks_hidden_models_and_other_endpoints(model,path):
    boundary=CallBoundary(lambda *a,**kw: pytest.fail('Unexpected HTTP'), 'model');boundary.phase='NLU'
    with pytest.raises(ValueError): boundary.open(request(model,path),timeout=3)
    assert not boundary.calls and boundary.errors


def test_stream_timeout_is_recorded_and_closes_response():
    response=Response()
    boundary=CallBoundary(lambda *a,**kw: response,'model');boundary.phase='NLU'
    with pytest.raises(TimeoutError):
        with boundary.open(request(),timeout=3): raise TimeoutError('body read')
    assert response.closed
    assert boundary.errors[-1]['failure_class']=='TIMEOUT'
    assert boundary.calls[0]['http_elapsed_seconds']>=0


def test_complete_goal_and_slot_contract_survives_shortlist(monkeypatch):
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS,SERVICE_DEFINITIONS,accepted_slots
    payloads=[]
    monkeypatch.setattr(commands,'_chat',lambda b,p,t,c: payloads.append(p) or None)
    commands.model_commands(query='guest request',language='en',base_url=BASE,model='model',
        enabled_request_kinds=ACTION_REQUEST_KINDS,service_candidates=[{'service_mode':'wake_up_call'}])
    diagnostic=prompt_diagnostics(payloads[0])
    expected={code for code,d in SERVICE_DEFINITIONS.items() if d.request_kind in ACTION_REQUEST_KINDS and d.request_kind!='directions'}
    assert set(diagnostic['goal_slots'])==expected
    assert diagnostic['candidate_order'][0]=='wake_up_call'
    for code in expected: assert set(diagnostic['goal_slots'][code])==set(accepted_slots(code))
    assert {'requested_item','unit'}<=set(diagnostic['goal_slots']['amenity_delivery'])
    assert 'requested_date' in diagnostic['goal_slots']['wake_up_call']
