# Windows 자격증명 vault (R4)

**LOCAL OPT-IN · Windows current-user DPAPI · 2026-10-27**

## 범위

저장 대상은 TalkAbout 서버 접속용 bearer credential 하나다. provider API key·OAuth 로그인·DB 비밀번호는 저장하지 않는다.
기본 동작은 종전대로 환경변수 또는 일회성 숨김 입력이며, 자동 저장·자동 vault 탐색은 없다.

이 기능은 서버 credential 발급·검증·갱신·회수가 아니다. save는 로컬 암호화 작업이며 네트워크나 provider를 호출하지 않는다.
저장한 credential이 유효한지는 명시적 `check`에서 확인한다. 유효기간·계정 신원·원격 installation 신원은 vault가 검증했다고 주장하지 않는다.

## 사용

사용자가 별도로 준비한 **기존 private 로컬 디렉터리**를 지정한다. repository·공유 폴더·cloud sync 폴더에 두지 않는다.
Windows에서는 현재 사용자·SYSTEM·관리자 외 접근을 허용하는 디렉터리를 사용하지 않는다.
vault 명령은 저장 디렉터리를 자동 생성하거나 기존 디렉터리 권한을 자동 수정하지 않는다.

```powershell
# 사용자가 별도 private 디렉터리를 준비한다(예시). 기존 공유 디렉터리 권한을 변경하지 않는다.
$vault = Join-Path $env:LOCALAPPDATA 'TalkAbout\vault'
New-Item -ItemType Directory -Path $vault
$account = whoami
icacls $vault /inheritance:r /grant:r "${account}:(OI)(CI)F"
# 아래 C:\private\talkabout-vault 예시는 이렇게 준비한 실제 경로로 바꾼다.

# credential은 명령 인수가 아니라 숨김 TTY 입력으로 받는다.
talkabout vault save --config agent.json --vault-dir C:\private\talkabout-vault --profile work

# 로컬 alias/version metadata만 출력. credential/ciphertext/hash는 출력하지 않음.
talkabout vault list --vault-dir C:\private\talkabout-vault

# 명시적 profile 선택. 환경변수가 있어도 이 profile 실패 시 fallback하지 않는다.
talkabout check --config agent.json --vault-dir C:\private\talkabout-vault --vault-profile work

# 로컬 파일 삭제뿐: 서버 credential revoke는 별도 수행해야 한다.
talkabout vault delete --vault-dir C:\private\talkabout-vault --profile work
```

`--vault-profile`/`--vault-dir`는 함께 지정해야 하며 `--prompt-token`과 동시 사용할 수 없다.
check/join/run/connect/mcp가 동일한 credential 선택 규칙을 사용한다.
status/stop은 기존 로컬 journal 관리 명령이며 vault를 조회하지 않는다.
profile 저장은 기존 항목을 조용히 덮어쓰지 않는다. 교체는 명시적 삭제 후 다시 저장한다.

## 보호 계약

- Windows `CryptProtectData`/`CryptUnprotectData`, current-user scope, UI 금지. LOCAL_MACHINE scope 사용 금지.
- token과 정확한 서버 base URL·allowed origin binding을 암호화 envelope 안에 함께 저장한다.
- 다른 서버 설정으로 load하면 **credential 전송 전 거절**한다. URL path도 binding에 포함한다.
- 같은 URL에 다른 서버가 재설치되는 것은 구분하지 못한다. 별도 installation identity API는 없다.
- profile은 제한된 문자·길이만 허용하고 경로 탈출·symlink/reparse 경로·과대/손상 파일은 거절한다.
- 복호화 실패·다른 Windows 사용자·지원하지 않는 OS에서는 고정 오류 코드로 중단한다.
  평문 fallback·환경변수 fallback·자동 초기화/삭제는 없다.
- vault secret을 config/journal에 넣거나 환경변수로 export하지 않는다. 기존 runtime credential filtering을 유지한다.

## 보장하지 않는 것

DPAPI는 **저장 중 보호**다. 같은 사용자 권한의 악성 프로세스·관리자·로그인 세션 탈취·메모리 검사를 막는 경계가 아니다.
경로 검사는 동시 부모 디렉터리 교체를 handle 기반으로 봉쇄하는 경계가 아니다. private 디렉터리 전제가 필요하다.
UNC/cloud-sync 여부를 코드가 증명하지 않으므로 로컬 비동기화 위치 선택은 사용자 정책이다.
Python immutable 문자열의 완전한 메모리 소거를 보장하지 않는다. local delete는 물리적 삭제·백업 소거·원격 revoke를 뜻하지 않는다.
프로필 alias와 파일 존재는 비밀로 취급하지 않으므로 alias에 token·개인정보를 넣지 않는다.

## 검증

portable 보호기 주입 테스트와 Windows 실제 DPAPI roundtrip은 **합성 문자열만** 사용한다.
최종 vault+CLI 집중 회귀: **68 passed / 2 skipped**. 전체 회귀: **879 passed / 3 failed / 35 skipped**(기존 브라우저 3건).
실제 credential 저장·공급자 호출은 이 기능의 테스트에서 실행하지 않는다.
보안 경계의 상세 오류는 민감 경로나 OS 예외 원문 대신 고정 코드만 출력한다.
