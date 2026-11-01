# 자체 점검 — 2026-11-01

**범위:** 코드·문서·테스트·운영(배포 호스트 `mcp-box` 포함) 읽기 전용 감사 + 일부 인프로세스 검증.
**기준 커밋:** `ee5ac68`(main) · **배포:** `localhost/talkabout:38a8a23…` (healthy, `/ready` 200).
**표기:** `[확인]` = 코드/실행으로 직접 확인, `[추정]` = 브라우저·정책 등 미실행 영역.

이 문서는 제품 문서가 아니라 **세션 점검 기록**이다. 정본은 [`docs/STATUS.md`](../docs/STATUS.md).

## 요약

보안 경계(인증·인가·CSRF·원본 토큰 보호)와 컨테이너 하드닝은 성숙하다. 반면 **운영 감독 계층(재시작·백업·모니터링·업그레이드 도구)**, **배포 플랫폼(Linux) 검증**, **문서 SSOT 최신성**, **자격증명 응답 경합**, **1클릭 클립보드 잔존 위험**, **접근성**이 주요 공백이다.

| 순위 | 항목 | 등급 | 비용 | 근거 |
| --- | --- | --- | --- | --- |
| 1 | 로그인 스로틀 키 플러딩 우회 | 높음 | 작음 | `talkabout/auth.py:53-56` `[확인]` |
| 2 | 1클릭 토큰이 클립보드에 잔존(핸들러 미실행 시) | 중간 | 중간 | `app.mjs:786-793`, `connect-agent.ps1:118-134` `[확인]` |
| 3 | 자격증명 응답이 같은 방 갱신에서 무음 폐기 | 높음 | 작음 | `app.mjs:851-852`, `state.mjs:97` `[확인]` |
| 4 | 문서 SSOT가 실배포와 불일치 | 중간 | 작음 | `STATUS.md:3,13,18`, `README.md:26-28` `[확인]` |
| 5 | 인가 전 입력검증(방 존재 오라클) | 중간 | 작음 | `core.py:318-334` `[확인]` |
| 6 | Linux·이미지 빌드가 CI에 없음 | 높음 | 중간 | `.github/workflows/safe-regression.yml:66-90` `[확인]` |
| 7 | 재시작·백업·모니터링·업그레이드 도구 부재 | 높음 | 큼 | 컨테이너 `RestartPolicy=no`, 백업 최신 2026-10-28 `[확인]` |
| 8 | 접근성(대비·포커스·reduced-motion) | 중간 | 중간 | `style.css:66,78,484,547-551`, `app.mjs:365-493` `[확인/계산]` |

## 1. 보안

- **높음 없음.** 방·task·artifact·file 접근은 `authorize()` + owner-scoped `snapshot()`을 일관되게 통과하고, static allowlist는 exact match, 저장 키는 `[a-f0-9]{32}`, SQL은 전부 파라미터화되어 있다. 쿠키(HttpOnly·SameSite=strict·조건부 Secure), Origin+CSRF 이중 검사, 오류 코드만 반환, provider 자식 프로세스 env allowlist도 확인했다.
- **M-1 로그인 스로틀 우회 `[확인]`** — `failures`가 1024개에서 **살아있는 창을 포함해** FIFO evict 된다(`auth.py:53-54`). 서로 다른 username 1024회로 피해자 키를 밀어내면 5회/60초 제한이 초기화된다. 각 시도가 PBKDF2 600k회라 증폭 DoS이기도 하다. 인프로세스 재현: flood 전 6번째 429 → flood 후 401. **수정:** 만료 스윕만 evict, `(ip, workspace, username)` 복합 키, 성공 시에만 리셋, 지수 백오프. `tests/test_sessions.py:84-92`가 단일 키만 검사해 못 잡는다.
- **M-2 1클릭 토큰 클립보드 잔존 `[확인]`** — payload(1시간 room-scoped bearer 포함)가 시스템 클립보드에 들어가고, 핸들러 미설치·실행 실패 시 그대로 남는다. `clearInviteSecret()`은 화면 필드만 비운다. Windows 클립보드 기록·동기화·타 프로세스가 회수할 수 있다. **수정:** 클립보드 대신 loopback 1회용 콜백/임시 파일, 실패 시 즉시 revoke, TTL 단축, confirm 문구에 잔존 가능성 명시.
- **L-1 인가 전 입력검증 오라클 `[확인]`** — `core.mutation`이 `authorize()` 전에 `kind`를 검증해(`core.py:334`) 남의 방+`kind=bogus`만 422가 된다(없는 방 404, 정상 kind 404). 방 ID 128bit라 열거는 불가하나 유출 시 존재 확인 가능. **수정:** 인가 먼저, 또는 pre-auth 실패를 전부 404로.
- **L-2 GET `/session`이 CSRF 토큰 회전 `[확인]`** — `auth.py:72-76` + `api.py:211-213`. SameSite=strict 덕에 cross-site 트리거는 어렵지만, 다중 탭에서 한 탭의 GET이 다른 탭의 다음 mutation을 403으로 만들 수 있다. GET에서는 회전하지 말 것.
- **L-3 로그인 외 rate limit 부재 + 스냅샷 증폭** — 요청마다 워크스페이스 전체(최대 64방×2049 event)를 로드한다(`store.py:404-413`). room-scoped 토큰으로도 비용 증폭·동시 스트림 16 슬롯 고갈이 가능하다. `MODERATION_AND_SECURITY.md:38`의 "rate limit 코드 강제"는 실제로 로그인에만 해당.
- **L-4 소유자/자기 자신 membership 차단 `[확인]`** — `core.py:520-543`은 대상이 방 소유자인지 보지 않는다. `blocked` 또는 `manage_members` 회수 시 복구 불가(현재 human 1명 모델이라 self-DoS).
- **L-5 `credentials.room_id` 검증 부재**(`identity.py:221-223`), **L-6 만료 세션 미정리**(`store.py:291-297`), **L-7 `-ClipboardText` 진단 스위치가 토큰을 argv에 노출**(`connect-agent.ps1:58-60`) — 모두 저비용 수정 대상.
- **L-9 공급망** — helper가 `main` 브랜치 zip을 해시 없이 설치하고(`connect-agent.ps1:63,668`), helper 자신도 서버에서 받아 검증 없이 저장한다. `requirements.lock.txt`는 `--require-hashes`가 없다.
- **정보:** `%1`은 launcher가 사용하지 않아 링크 주소 주입 경로는 확인되지 않음(`Test-OriginValue`가 메타문자를 실제 거부) · handover의 human UUID가 전 멤버에 노출(`core.py:138`) · `runtime_check passed`는 자기신고(문서화됨) · `attachments.py`는 미배선 dead code · `Referrer-Policy` 부재.

## 2. 웹 UI·접근성

- **H1 자격증명 응답 무음 유실 `[확인]`** — `issueCredential`/`showCredentialMetadata`/`loadTaskDiagnostics`가 `view.epoch`로 펜싱하는데, `refresh()`가 매번 `openRoom(…,false,false)` → `view.select()`로 epoch를 올린다(`state.mjs:97`). 10초 폴링·SSE·수동 새로고침이 POST 도중 끼면 응답이 폐기되고 `finally`에서 토큰을 지워 **서버에만 1시간 자격증명이 남는다**. 첨부 조회는 같은 결함을 이미 `textRoomEpoch`로 고쳤다(`tests/test_text_files_ui.mjs:53-62`) — 같은 처방이 필요하다.
- **H2 클립보드 활성화 상실 `[추정]`** — `writeText`가 네트워크 await 뒤에 호출된다(`app.mjs:788`). Safari/Firefox는 제스처 task를 요구할 수 있고, 실패 시 토큰을 지운 뒤 수동 경로만 안내한다. 클립보드 쓰기를 먼저/같은 task에서 수행하거나 실패 시 비밀값 표시를 유지할 것.
- **M1/M2 접근성 `[계산/정적]`** — 라이트 테마 `--faint` 2.99–3.50:1, `--accent` 4.22:1, 기본 버튼 3.18:1(WCAG AA 미달). 폼 포커스는 `input:focus{outline:none}`이 `:focus-visible` 아웃라인을 덮어 링 2.10/1.38:1.
- **M3 reduced-motion 무시 `[확인]`** — CSS만 차단하고 `startStageAnimation` rAF 루프는 계속 돈다(`app.mjs:365-493`). `matchMedia`를 읽지 않는다.
- **M4–M6 라이브 리전·포커스** — 12개 이상 `role=status`가 폴링마다 재할당되고, 뷰 전환/다이얼로그 복귀 포커스 관리가 없으며, 10초마다 DOM 전체 교체로 방금 표시한 자격증명 상태·포커스가 사라진다.
- **M7 발급 실패 데드엔드** — `issueUncertain`은 성공 발급에서만 해제되어, 한 번 실패하면 주 버튼이 "다시 확인" 루프에 갇히고 복구는 접힌 고급 영역에만 있다. 1클릭도 영구히 숨는다.
- **우리 기능 특유:** “도우미를 실행했습니다”가 실제로는 스킴 이동 시도뿐(M8) · “초대하고 연결”이 메타데이터 조회 실패 시 **조용히 초대만** 수행(M9) · 기존 초대 재개는 런타임 기본값(codex)으로 자동 실행될 수 있음 · 클립보드 실패 시 수동 경로에 방금 발급한 토큰이 없음.
- **기타:** 실패 원인과 문구 불일치(network/500→"거절", 401→"기한 초과"), confirm 9곳/prompt 2곳, `finally{data.token=''}` TypeError가 원 오류를 덮음(`app.mjs:851`), `api` 타임아웃 부재로 폼 전체 비활성, `?connect=` 잔존으로 방 목록 복귀가 되돌려짐, 좁은 화면 task-meta nowrap·탭 28px 잘림·HUD/카메라 겹침, 라벨 누락(`connection`, `runtimeTest`, `stalled`), 로그아웃 잔존 상태.

## 3. 테스트·CI

- **Linux·이미지 빌드 미검증 `[확인]`** — 전체 스위트는 `windows-latest`(`safe-regression.yml:25`), ubuntu 잡은 `tests/test_posix_containment.py` 9건만(`:88`). 배포 대상은 Linux 전용인데 이미지 빌드 잡이 없다. 마지막 이미지 빌드 증거는 2026-10-13(`deploy/podman/VERIFICATION.md`). HEALTHCHECK 보존은 수동 `--format=docker` 의존.
- **`.containerignore` allowlist 불완전 `[확인]`** — `*.html|*.mjs|*.css`만 허용해 `connect-agent.ps1`·`favicon.svg`가 목록에 없다. 다만 **현재 buildah 빌드 이미지에는 두 파일이 포함됨을 직접 확인**했다(`podman run … ls web/`: 34,963 B helper, 684 B favicon). Docker 의미론 빌드로 바뀌면 helper 404로 1클릭이 죽으므로, 명시적 `!talkabout/web/connect-agent.ps1`·`!talkabout/web/favicon.svg` 추가와 **이미지 내 두 파일 존재 단정**이 필요하다.
- **`--maxfail=1`**(`tools/run_safe_regression.py:47`) — 첫 flake에서 전체가 멈춰 나머지 실패를 숨긴다. coverage 도구 없음.
- **EXCLUDED 5건**(브라우저·MCP·PG loopback readiness)은 어떤 잡에서도 실행되지 않는다. 유일한 실제 loopback HTTP/readiness E2E가 제외 목록에 있다.
- **PowerShell helper는 정적 텍스트 검사만**(`tests/test_helper_clipboard_contract.py`), 브라우저 E2E는 수동 1회뿐이고 테스트로 추가되지 않았다. `-DryRun` 실행 계약은 CI 가드(`ci_guard.py:9` allowed executables에 powershell 없음)로 실행 불가.
- **테스트 계약 갭:** `tests/test_replay_acl_projection.py:120-127` parametrize(`revoked|wrong_actor|room_scope`)와 본문(`read_scope`) 불일치로 scope 회수 케이스가 죽은 분기 · `test_sessions`가 스로틀 우회 미탐지 · `test_helper_clipboard_contract`가 `"%1"` 존재를 요구.
- **공급망:** PostgreSQL 테스트 바이너리를 체크섬 없이 다운로드(`safe-regression.yml:47`), pip 해시 미고정, `ci-constraints` 8개만 고정.
- **환경 flake:** process-boundary 테스트(`test_process_cleanup`, `git rev-parse`, stdin/EOF)가 이 PC에서 간헐 실패하며, 수정 전 HEAD에서도 동일하게 재현됨(코드 회귀 아님). 러너 문서에 트러블슈팅이 없다.

## 4. 운영·배포(호스트 확인)

- **재시작·감독 없음 `[확인]`** — 앱·DB 모두 `RestartPolicy=no`, systemd/quadlet 미등록. 호스트 재부팅 후 자동 복구 없음. `HealthCmd`는 정의되어도 소비자 없음.
- **백업 stale `[확인]`** — `~/backups/talkabout` 최신 dump가 2026-10-28이고, 그 뒤 데이터 변경·DB 재생성 이력이 있다. 스케줄·보존·암호화·WAL 없음.
- **이미지·컨테이너 누적 `[확인]`** — 이미지 440개 8.0 GB(92% reclaimable), 컨테이너 18개 중 2개만 active, 호스트 디스크 77%.
- **접근 경로** — 127.0.0.1:18081 publish 단독(80/443은 다른 프로젝트). TLS/Reverse proxy·모니터링·알림 없음. `allowed_hosts=['127.0.0.1','localhost']`라 hostname 프록시를 붙이려면 코드/설정 변경 필요.
- **로그** — 앱 `--log-driver k8s-file`, `Size=-1B`(무제한). 로테이션 없음.
- **업그레이드/롤백** — install.py 지문에 이미지가 포함되어 정식 업그레이드 경로가 없고, 실제 롤아웃은 수동 `podman run` 재생성(이번 세션). 절차 문서(README.md:97)는 실행 이력 없음. 이번 사고(inode 고갈 → `libpod/tmp` 삭제 → 런타임 추적 상실 → DB/앱 재생성)의 교훈이 런북에 반영되지 않았다.
- **설치 helper 버전 skew `[확인]`** — 설치된 `%LOCALAPPDATA%\TalkAbout\helper\connect-agent.ps1`은 설치 시점 스냅샷이고 자동 갱신이 없다. 서버 업그레이드 후 payload v가 올라가면 구 helper가 거부한다. launcher가 pinned origin에서 최신 helper를 확인·갱신하는 편이 안전하다.
- **문서 SSOT `[확인]`** — `docs/STATUS.md:3`(확인일 2026-10-25)·`:18/:168` "운영 배포 미완료", `README.md:26-28` "636/196 · 배포 안 됨"인데 실제로는 2회 배포·DB 재생성·데이터 보존 확인, 최신 측정 1176/242. `#53~#58`이 STATUS에 반영되지 않았다. `USER_FLOW.md:3` 배너는 "REVIEW BRANCH / NOT DEPLOYED 2026-10-16". 영문 문서에 1클릭/초대하고 연결 누락.

## 5. 바로 할 일(권고)

1. **로그인 스로틀 eviction 수정 + 우회 회귀 테스트**(`auth.py`, `test_sessions.py`).
2. **1클립 토큰 잔존 완화**: 핸들러 미실행 감지 시 revoke·비밀값 표시 유지, confirm/안내에 클립보드 잔존 고지, `-ClipboardText`는 `-DryRun`에서만 허용.
3. **자격증명 응답을 방 범위 epoch로 펜싱하고 무음 폐기 제거**(`app.mjs`), 첨부와 동일 패턴.
4. **STATUS/README/배너 최신화**(수치·배포 표·#53~#58), 운영 인스턴스 표(호스트·포트·이미지·롤백) 추가.
5. **CI에 이미지 빌드 + Linux smoke + 이미지 내 `connect-agent.ps1`/`favicon.svg` 존재 단정** 추가, `.containerignore` 명시 항목 보강, `--maxfail` 제거.
6. **운영 정책 결정**: 재시작(systemd/quadlet), 백업 스케줄·보존, 로그 로테이션, 이미지 GC, 알림. inode 사고 런북 반영.
7. 접근성(라이트 대비·포커스 링·reduced-motion), M7 데드엔드, M8 문구, M9 부분 실패 안내.

## 6. 검증하지 못한 것

- 실제 브라우저 정책(클립보드 활성화)·스크린리더·모바일 기기 렌더, 실제 공격 트래픽·부하.
- 이미지 재빌드/CI 잡 실행(GitHub Actions 이력 조회는 제외), pytest/node 전체 재실행(이 문서는 정적 감사 + 인프로세스 2건).
- PostgreSQL 통합·동시성, 실제 provider 호출.
