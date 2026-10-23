import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from talkabout.api import create_app
from conftest import control
from mcp_contract import assert_tool_contract


def test_mcp_stdio_process_interoperability(fixture,tmp_path):
    store,core,rid=fixture
    other=core.create('fixture-b',{'topic':'private other fixture'},'other')
    # Remove A from the second room: only its creator has membership.
    sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(64)
    port=sock.getsockname()[1]
    origin='http://127.0.0.1:'+str(port)
    server=uvicorn.Server(uvicorn.Config(create_app(store,start_scheduler=False),host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline: time.sleep(.01)
    assert server.started
    config=tmp_path/'mcp.local.json'
    config.write_text(json.dumps({'base_url':origin,'allowed_origin':origin,'token_env':'TALKABOUT_TEST_MCP_TOKEN'}))
    environment={k:os.environ[k] for k in ('SYSTEMROOT','SystemRoot','PATH','TEMP','TMP') if k in os.environ}
    environment['TALKABOUT_TEST_MCP_TOKEN']='fixture-a'
    process=subprocess.Popen([sys.executable,'-m','talkabout.cli','mcp','--config',str(config)],cwd=Path(__file__).parent.parent,
                             env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    replies=queue.Queue(); transcript=[]
    def read():
        for line in process.stdout:
            transcript.append(line)
            replies.put(json.loads(line))
    reader=threading.Thread(target=read,daemon=True); reader.start()
    def call(identifier,method,params=None):
        request={'jsonrpc':'2.0','id':identifier,'method':method}
        if params is not None: request['params']=params
        process.stdin.write(json.dumps(request)+'\n'); process.stdin.flush()
        response=replies.get(timeout=5)
        assert response['id']==identifier and response['jsonrpc']=='2.0'
        return response
    try:
        initialized=call(1,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'local-synthetic-test-client','version':'1'}})
        assert initialized['result']['protocolVersion']=='2025-11-25'
        process.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'})+'\n'); process.stdin.flush()
        assert_tool_contract(call(2,'tools/list')['result']['tools'])
        room=call(3,'tools/call',{'name':'read_room','arguments':{'room_id':rid}})
        assert json.loads(room['result']['content'][0]['text'])['id']==rid
        denied=call(4,'tools/call',{'name':'read_room','arguments':{'room_id':other['id']}})
        assert denied['result']['isError']
        invalid=call(5,'tools/call',{'name':'read_room','arguments':{'room_id':rid,'token':'forbidden-argument'}})
        assert invalid['error']['code']==-32602
        args={'room_id':rid,'text':'합성 MCP 텍스트','generation':1,'membership_revision':2,'client_mutation_id':'fixture-stdio-write'}
        first=call(6,'tools/call',{'name':'propose_text','arguments':args})
        retry=call(7,'tools/call',{'name':'propose_text','arguments':args})
        assert first['result']==retry['result'] and not first['result']['isError']
        control(core,rid,'pause')
        rejected=call(8,'tools/call',{'name':'propose_text','arguments':{**args,'client_mutation_id':'after-pause'}})
        assert rejected['result']['isError']
        assert call(9,'ping')['result']=={}
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)
        reader.join(timeout=1)
        server.should_exit=True; thread.join(timeout=5); sock.close()
    assert process.returncode==0
    assert 'fixture-a' not in ''.join(transcript)
    assert process.stderr.read()==''
    assert not thread.is_alive()
