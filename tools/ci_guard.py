"""Offline validation guards; no provider credentials, executables or remote sockets."""
import os,re,socket,sys
from contextlib import contextmanager
from pathlib import Path

_ALLOW_SYNTHETIC_POWERSHELL = False

@contextmanager
def synthetic_powershell_launch():
    """Temporarily allow the offline PowerShell contract-test subprocess."""
    global _ALLOW_SYNTHETIC_POWERSHELL
    prior = _ALLOW_SYNTHETIC_POWERSHELL
    _ALLOW_SYNTHETIC_POWERSHELL = True
    try:
        yield
    finally:
        _ALLOW_SYNTHETIC_POWERSHELL = prior

def install():
    private=[Path.home()/name for name in ('.codex','.claude','.grok','.aws','.claude.json')]
    if os.environ.get('CODEX_HOME'):private.append(Path(os.environ['CODEX_HOME']))
    prefixes=[str(path.resolve()).casefold() for path in private]
    allowed={'python','pythonw','node','git','chrome'}
    def audit(event,args):
        if event in {'open','os.listdir','os.scandir'} and isinstance(args[0],(str,bytes,os.PathLike)):
            path=os.path.abspath(os.fsdecode(args[0])).casefold()
            if any(path==prefix or path.startswith(prefix+os.sep) for prefix in prefixes):
                raise RuntimeError('real_account_path_blocked')
        if event=='subprocess.Popen':
            exe=args[0]
            if not exe:
                match=re.match(r'(?:(?:"([^"]+)")|(\S+))',str(args[1]));exe=next(x for x in match.groups() if x)
            executable = Path(str(exe)).stem.casefold()
            if (executable in {'pwsh', 'powershell'} and _ALLOW_SYNTHETIC_POWERSHELL):
                return
            if executable not in allowed:
                raise RuntimeError('non_fixture_executable_blocked')
        if event in {'socket.connect','socket.bind','socket.getaddrinfo'}:
            address=args[1] if event!='socket.getaddrinfo' else (args[0],args[1])
            if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost',None):
                raise RuntimeError('non_loopback_network_blocked')
    sys.addaudithook(audit)

