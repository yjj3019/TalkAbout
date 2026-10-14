import json
import os
import sys
import threading
import pytest
from talkabout.adapters import AdapterError, CLIRuntime, child_environment, execute_bounded


@pytest.mark.parametrize('kind',['codex','claude'])
def test_concrete_cli_commands_disable_tools_and_do_not_put_prompt_in_argv(kind):
    adapter=CLIRuntime(kind,executable=sys.executable)
    command=adapter.command()
    assert '--dangerously-bypass-approvals-and-sandbox' not in command and '--dangerously-skip-permissions' not in command
    if kind=='codex':assert '--ignore-user-config' in command and 'features.shell_tool=false' in command and '--ephemeral' in command
    else:assert command[command.index('--tools')+1]=='' and '--strict-mcp-config' in command and '--no-session-persistence' in command


def test_platform_secrets_are_removed_from_child_environment(monkeypatch):
    monkeypatch.setenv('TALKABOUT_TOKEN','synthetic-secret-only')
    monkeypatch.setenv('TALKABOUT_DATABASE_URL','synthetic-dsn-only')
    monkeypatch.setenv('UNRELATED_SECRET','synthetic-unrelated-only')
    assert not {'TALKABOUT_TOKEN','TALKABOUT_DATABASE_URL','UNRELATED_SECRET'} & child_environment().keys()
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-misconfigured-platform-credential')
    assert 'OPENAI_API_KEY' not in child_environment({'OPENAI_API_KEY'})
    assert 'OPENAI_API_KEY' not in child_environment(blocked_values={'synthetic-misconfigured-platform-credential'})


def test_actual_synthetic_child_reads_stdin_and_failure_is_sanitized():
    command=[sys.executable,'-c','import sys; print(sys.stdin.read())']
    assert execute_bounded(command,'synthetic-input',threading.Event(),2).strip()=='synthetic-input'
    with pytest.raises(AdapterError,match='^runtime_failed$'):
        execute_bounded([sys.executable,'-c',"import sys; print('synthetic-stderr-secret',file=sys.stderr); sys.exit(1)"],'',threading.Event(),2)


def test_child_timeout_and_cancel_are_bounded():
    command=[sys.executable,'-c','import time; time.sleep(20)']
    with pytest.raises(AdapterError,match='runtime_timeout'):execute_bounded(command,'',threading.Event(),1)
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(AdapterError,match='runtime_cancelled'):execute_bounded(command,'',cancelled,2)


@pytest.mark.parametrize('kind,output,text',[('claude',{'type':'result','is_error':False,'result':'Synthetic response'},'Synthetic response'),('codex',{'type':'item.completed','item':{'type':'agent_message','text':'Synthetic response'}},'Synthetic response')])
def test_output_protocol_parsing_without_model_invocation(monkeypatch,kind,output,text):
    monkeypatch.setattr('talkabout.adapters.execute_bounded',lambda *args:json.dumps(output))
    assert CLIRuntime(kind,executable=sys.executable).run({'topic':'Synthetic'},threading.Event())==text


@pytest.mark.parametrize('event',['item.started','item.updated','item.completed'])
def test_tool_event_is_rejected_and_invalid_result_never_published(monkeypatch,event):
    monkeypatch.setattr('talkabout.adapters.execute_bounded',lambda *args:json.dumps({'type':event,'item':{'type':'command_execution'}}))
    with pytest.raises(AdapterError,match='runtime_tool_policy_violation'):CLIRuntime('codex',executable=sys.executable).run({},threading.Event())
