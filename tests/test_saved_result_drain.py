"""Synthetic saved-result recovery under a zero remaining model budget."""
import httpx
import pytest

from conftest import LocalClient,control
from talkabout.worker import Journal,SyntheticRuntime,Worker


class NoRuntime:
    def run(self,*args):raise AssertionError('no model invocation allowed')


@pytest.mark.parametrize('state',['ready','claimed','call_started','uncertain'])
def test_saved_only_never_polls_claims_or_replays_other_states(fixture,tmp_path,state):
    store,_,rid=fixture;journal=Journal(tmp_path/'worker.sqlite');client=LocalClient(store)
    try:
        worker=Worker(client,journal,NoRuntime());worker.poll()
        task,_,payload=journal.next();journal.save(task,state,payload)
        before=journal.status();cursor=journal.setting('cursor');client.calls.clear()
        assert worker.step(saved_result_only=True)==('waiting' if state=='uncertain' else 'model_call_budget_exhausted')
        assert journal.status()==before and journal.setting('cursor')==cursor and client.calls==[]
    finally:journal.close();client.http.close()


def prepare_saved_result(fixture,path):
    store,_,_=fixture;journal=Journal(path);client=LocalClient(store);request=client.request
    def offline_complete(method,resource,body=None,key=None,params=None):
        if method=='POST' and resource.endswith('/complete'):raise httpx.ConnectError('synthetic submission outage')
        return request(method,resource,body,key,params)
    client.request=offline_complete
    try:
        with pytest.raises(httpx.ConnectError):Worker(client,journal,SyntheticRuntime()).step()
        assert journal.next()[1]=='result_saved'
    except BaseException:
        journal.close();client.http.close();raise
    client.request=request
    return journal,client


def test_saved_only_obeys_stop_without_submitting_or_polling(fixture,tmp_path):
    journal,client=prepare_saved_result(fixture,tmp_path/'worker.sqlite')
    try:
        worker=Worker(client,journal,NoRuntime())
        with journal.db:journal.set_setting('stop',True)
        client.calls.clear()
        assert worker.step(saved_result_only=True)=='stopped'
        assert journal.status()=={'result_saved':1} and client.calls==[]
    finally:journal.close();client.http.close()


def test_saved_only_still_rejects_a_result_fenced_by_pause(fixture,tmp_path):
    _,core,rid=fixture;journal,client=prepare_saved_result(fixture,tmp_path/'worker.sqlite')
    try:
        control(core,rid,'pause');worker=Worker(client,journal,NoRuntime());client.calls.clear()
        with pytest.raises(httpx.HTTPStatusError) as error:worker.step(saved_result_only=True)
        assert error.value.response.status_code==409 and journal.status()=={'failed':1}
        assert len(client.calls)==1 and client.calls[0][1].endswith('/complete')
        assert core.read('fixture-human',rid)['messages']==[]
    finally:journal.close();client.http.close()
