# Handy (cjpais/handy) 성능 정밀 분석 — R3

> 분석 대상: `.analysis-repos/handy/Handy-main/` (main, 2026-11-02 zip 스냅샷, v0.10.0, MIT).
> **방법: 순수 정적 분석.** 이 환경에 cargo/rustc/bun이 없어 빌드·실행하지 않았다. 아래 "추정"은 모두
> 코드에서 읽은 상수(샘플레이트·프레임 크기·파일 크기·스레드 구조)에서 계산한 값이며, 실측이 아니다.
> 저장소가 이미 남기는 계측 로그(§3)는 인용하되, 그 값을 내가 얻었다고 쓰지 않는다.
> 파일 경로는 저장소 루트 기준 상대 경로다. 두 번째 언급부터는 짧은 이름(`actions.rs:697`,
> `transcription.rs:1024`)으로 쓰며, 이는 각각 `src-tauri/src/...`·`src/...` 아래로 해석한다.
> 라인 번호는 2026-11-02 스냅샷 기준.

---

## §0 요약 — 핫패스 표

체감 지연 5종(키 누름→녹음 시작 / 말 끝→텍스트 / 라이브 프리뷰 / 첫 실행·모델 전환 / 상주 자원)에
직결되는 지점만 올렸다. "효과 추정"은 근거(상수·개수·바이트)를 함께 적었다.

| # | 지점 | file:line | 원인 | 체감 영향 | 효과 추정(근거) |
| --- | --- | --- | --- | --- | --- |
| H1 | 라이브 텍스트 IPC 무제한 재전송 | `src-tauri/src/managers/transcription.rs:1410-1416` | revision마다 **committed 전체 문자열 + tentative**를 이벤트로 emit. 스로틀·병합 없음 | 라이브 프리뷰 렌더 비용, 장문에서 IPC 바이트 폭증 | 프레임당 최대 31.25회(=16 kHz/512) × (누적 텍스트 길이) → 10분 낭독 시 누적 수 MB(추정, §1.4 산정) |
| H2 | 오버레이가 이벤트마다 전체 리렌더 + 강제 레이아웃 | `src/overlay/RecordingOverlay.tsx:169-171`, `:232-238` | `setStreamText`→전체 트리 재렌더, `useLayoutEffect`에서 `scrollHeight` 읽기(리플로우) | 오버레이 프레임 드랍, 입력 지연 | H1과 동일 빈도. 리플로우 1회당 강제 레이아웃 1회(추정) |
| H3 | 시작 시 웹뷰 2개 생성 + 1.31 MB i18n 즉시 로드 | `src-tauri/src/lib.rs:396-397`, `src-tauri/src/i18n/index.ts:13-16` | overlay 창을 `overlay_style: none`이어도 부팅 시 생성. 26개 로케일 JSON 1,343,007 B를 `import.meta.glob(eager)`로 전량 로드(i18n 모듈을 두 엔트리 모두 import) | 첫 창 표시 시간, 상주 메모리 | 파싱 대상 1.31 MB × 2 엔트리. 텍스트 파싱 비용은 수십 ms 규모(추정). 상주는 WebView2 프로세스 1개 추가 |
| H4 | 전사가 tokio 워커를 점유 | `src-tauri/src/actions.rs:697`, `:705`, `:747` | `async_runtime::spawn` 안에서 `stop_recording()`·`transcribe_recording()`(수 초 블로킹)을 직접 호출. `spawn_blocking` 미사용 | 동시 다운로드·기기 프리워밍·플러그인 async 작업 지연 | 디코드 1회 = 오디오 30 s일 때 CPU 2.1 s / GPU 1.2 s(카탈로그 RTF 기준, §1.7). 4코어면 런타임 워커 25% 점유 |
| H5 | 라이브 결과 실패 시 **전체 재디코드** | `src-tauri/src/managers/transcription/live.rs:232-236` → `transcription.rs:1005` | 라이브 스트림이 빈 텍스트/실패면 `decode_batch(audio)`로 처음부터 다시 디코드 | 말 끝→텍스트가 2배 | 동일 오디오 2회 디코드 → 위 H4의 2배(추정) |
| H6 | 녹음 세션마다 `get_settings()` 전체 재구성 | `src-tauri/src/shortcut/handler.rs:35`, `settings.rs:1121-1167` | 매 단축키 이벤트·시작·정지·유휴 루프에서 store 값 deep clone → `serde_json::from_value`(필드 67개) → 마이그레이션 → 기본 바인딩 병합 | 키 누름 지연(수십~수백 µs, 추정), 코드 전반의 호출 지점 141곳 | 정적 근거: `AppSettings` 필드 67개 + HashMap/Vec 포함, `get_settings(` 호출 141곳. 값 자체는 µs급이라 **1차 병목은 아님**(과대평가 금지) |
| H7 | WAV 저장용 샘플 전체 복사 | `src-tauri/src/actions.rs:737` | `samples.clone()` 후 `spawn_blocking`으로 WAV 기록 | 정지 순간 메모리 스파이크 | 5분 낭독 = 4.8 M샘플 × 4 B = 19.2 MB 복사(추정). 스왑 압박 시 수 ms~수십 ms |
| H8 | VAD 프레임 단위 채널 3홉 + 매 프레임 할당 | `src-tauri/src/managers/transcription.rs:182-189`, `src-tauri/src/audio_toolkit/audio/sink.rs:166-169`, `src-tauri/src/audio_toolkit/audio/visualizer.rs:86-155` | 512샘플(32 ms)마다 `frame.to_vec()` + mpsc + FFT 버킷 Vec 할당 | CPU 상주(녹음 중) | 31.25 회/s × (Vec 2 KB + FFT 512 pt + Vec<f32> 16) — 1회당 수십 µs(추정) |
| H9 | 프런트 코드 스플리팅 없음 | `vite.config.ts:18-26`, `src/App.tsx:28`, `src/components/Sidebar.tsx:8-14` | 모든 설정 화면·온보딩·`react-markdown`·`react-select`가 정적 import. `React.lazy` 0건 | 첫 렌더까지 JS 파싱 | 번들 크기 미측정(§5). 로케일 1.31 MB가 여기 더해짐 |
| H10 | 기본 모델 731 MB, 유휴 5분에 언로드 | 카탈로그 `parakeet-unified-en-0.6b` Q8_0 731.4 MB; `src-tauri/src/settings.rs:150-160`(`Min5`), `src-tauri/src/managers/transcription.rs:304-373` | 5분 무사용 → 워커 프로세스 종료. 다음 발화에 재로드 | 첫 발화·재개 지연, 디스크 I/O | 로드 시간은 미실측. 저장소 주석은 "웜 로드 ~0.2 s(Apple Silicon)·~1.5 s(Windows CPU), 콜드 40 s+"(`src/overlay/RecordingOverlay.tsx:35-38`) |

**즉시 착수 3건**: H1+H2(프리뷰 emit 병합·스로틀), H4(`spawn_blocking`), H3(i18n 지연 로드 + 오버레이 지연 생성).

---

## §1 핫패스 추적

### 1.1 실행 지형 (코드로 확인한 구조)

Handy는 **3계층**이다: Tauri 메인 프로세스 / 별도 웹뷰 2개(설정 창 + 오버레이) / **transcribe.cpp 워커 프로세스**.

| 구성 | 역할 | 근거 |
| --- | --- | --- |
| 메인 스레드 | Tauri 이벤트 루프, 창·트레이 | `lib.rs:1135-1164` (`on_window_event`, `invoke_handler`) |
| tokio 런타임 | 다운로드 스트림·명령·업데이트 | `lib.rs:944-945` 플러그인 초기화, `commands/*` |
| 코디네이터 스레드 (1개) | 단축키 입력 상태기계, `start/stop` 실행 | `transcription_coordinator.rs:577-600` (`thread::spawn` + `mpsc`), `:721-741` |
| 오디오 전달 스레드 | `CaptureSink::process_chunk` — VAD·레벨 인라인 | `audio_toolkit/audio/sink.rs:81-83` 주석, `:172-203` |
| 스트림 워커 스레드 | `StreamCmd` 큐 소비 → 엔진 피드 | `transcription.rs:806-830`, `:885-906` |
| 엔진 소유 스레드 | 워커 프로세스와 요청/응답 직렬화 | `engine_supervisor/supervisor.rs:1-15` |
| **transcribe.cpp 워커 프로세스** | 모델 상주·실제 추론 | `supervisor.rs:35-47`, `lib.rs:1192-1195` |
| 유휴 감시 스레드 | 10 s 주기로 모델 언로드 판단 | `transcription.rs:304-373` |
| 웹뷰 스레드 ×2 | 설정 창 / `recording_overlay` | `lib.rs:999-1018`, `lib.rs:396-397` |

즉 **모델은 메인 프로세스에 없다.** 앱 메모리(모델 제외)와 모델 메모리가 분리돼 있고,
앱 종료가 모델 로드를 기다리지 않는 이유이기도 하다(`lib.rs:1192-1195`).

### 1.2 경로 A — 키 누름 → 녹음 시작 (사용자 체감 1)

```
OS 훅 → handle_shortcut_event(app, ...)                       shortcut/handler.rs:29
        └ get_settings(app)   ← store deep clone + serde 파싱  handler.rs:35 / settings.rs:1121
      → coordinator.send_input(...)  (mpsc, non-blocking)      transcription_coordinator.rs:646-700
[코디네이터 스레드]
      → run_effect → start() → action.start()                  transcription_coordinator.rs:745-758
      → TranscribeAction::start                                actions.rs:478
          ├ initiate_model_load(true)   ← **비블로킹** (thread::spawn)  transcription.rs:696-728
          ├ thread::spawn(preload_vad)  ← Silero 1.2 MB F32     actions.rs:490-494
          ├ get_settings(app) ×2        (again)                 actions.rs:500, :521
          ├ set_tray_state(Recording)                           actions.rs:516
          ├ tm.start_stream() (라이브 모델일 때)                actions.rs:549-551
          ├ show_recording_overlay()    ← 웹뷰 IPC              actions.rs:557-562
          └ rm.try_start_recording(binding, vad_policy)         actions.rs:576
                ├ state 락 획득(이후 마이크 open까지 보유)      managers/audio.rs:1042
                ├ start_microphone_stream()                     audio.rs:800
                │    ├ get_settings → 기기 해석 → VAD 프레임    audio.rs:861-870
                │    └ recorder.open(frames_per_chunk=512)      audio.rs:875-903
                ├ lend_vad()                                    audio.rs:1066
                └ recorder.start(sink) → RecordingReadiness      audio.rs:1078-1092
[별도 스레드] readiness.wait() → 첫 청크 도착 → recording-ready emit + 시작음 + mute
                                                              actions.rs:585-624
```

코드가 이미 남기는 계측 지점(그대로 인용):

| 구간 | 로그 | file:line |
| --- | --- | --- |
| 마이크 open 3분해 | `"mic stream breakdown: device_resolve={:?} vad_ensure={:?} open={:?}"` | `managers/audio.rs:897-902` |
| 시작 전 단계 4분해 | `"start-path pre-recording steps: model_kickoff={:?} tray={:?} settings+stream_plan={:?} overlay={:?}"` | `actions.rs:566-573` |
| 전체 시작 | `"TranscribeAction::start completed in {:?}"` | `actions.rs:649-652` |
| 스트림 초기화 | `"Microphone stream initialized in {:?}"` | `audio.rs:914-917` |
| 첫 샘플 도착 | `"Microphone is receiving samples; recording is ready"` | `actions.rs:612` |

**구조적 관찰 3개**

1. `get_settings`가 같은 발화에서 최소 2회(핸들러 1 + 액션 1~2) 호출된다. 비용은 µs급이지만,
   구조적으로 "설정 = 매 이벤트마다 전체 재파싱"이 코드 전반(141곳)에 퍼져 있어
   캐시 도입을 어렵게 만든다 → H6.
2. `try_start_recording`은 `self.state` 뮤텍스를 **1042행에서 잡고 1104행까지** 보유한다.
   그 안에서 `start_microphone_stream()`(기기 열거 + `cpal` 스트림 open)까지 수행한다.
   기기 해석·open이 수십 ms면 그 시간 동안 `state` 락이 잡혀 있고, `stop`/`update_vad_backend`
   같은 경로가 대기한다 → §2-6.
3. 마이크 스트림을 **키 누름 시점에 연다**(on-demand 기본값, `settings.rs:588-591` `always_on_microphone=false`).
   그래서 "키 누름→녹음 시작"에 기기 open 비용이 그대로 들어간다. always-on이면 이 비용이 0에 가까워지지만
   상시 마이크 점유라는 다른 비용이 있다(트레이드오프는 제품 결정).

### 1.3 경로 B — 말 끝(키 릴리스) → 텍스트 표시 (사용자 체감 2)

```
stop()  actions.rs:655
 ├ invalidate_recording_readiness / unregister_cancel_shortcut        :658-662
 ├ get_settings(overlay_style)                                        :677
 ├ set_tray_state(Transcribing) + 오버레이 전환                        :672-685
 ├ remove_mute + stop 사운드(동기 호출)                                :688-691
 └ async_runtime::spawn(async {                                       :697
      rm.stop_recording(binding, gen)   ← sink.finish()로 samples 이동   :705 / audio.rs:1244-1300
      if samples.is_empty() { ... } else {
        spawn_blocking(save_wav(samples.clone()))                     :737-740
        tm.transcribe_recording(samples)   ← **여기서 대기(블로킹)**    :747
           ├ finalize_live(n)  → 스트림 워커에 Finalize 큐 + reply 대기  transcription.rs:917-934
           └ complete(n, audio, live)
                └ live == None 이면 decode_batch(audio) = 전체 재디코드  :1005
        wav_handle.await + verify_wav_file                            :750-771
        process_transcription_output(...) → paste 스레드              :799-940
   })
```

| 구간 | 특성 | 근거 |
| --- | --- | --- |
| 스트림 잔여 오디오 | **큐 백로그를 먼저 소진**한 뒤 finalize | `supervisor.rs:513-528`(무제한 큐) + `:1069-1076`(pending_ms) |
| 라이브 실패 시 | 전체 재디코드(=2배) | `live.rs:224-262`, `transcription.rs:1003-1006` |
| 장문(>40 s) | 30 s 목표·40 s 상한으로 절단, **순차** 디코드 | `audio_toolkit/chunking.rs:23-30`, `transcription/chunked.rs:144-177` |
| 후처리 | LLM 왕복이 텍스트 표시 앞에 붙음(옵션) | `actions.rs:194-473`, `:791-799` |
| 붙여넣기 | 별도 스레드(수면 포함) | `actions.rs:862-867`, `paste_tx/mod.rs:51-63` |

이미 있는 계측: `"Recording stopped and samples retrieved in {:?}, sample count: {}"`(`actions.rs:709-713`),
`"Transcription completed in {:.2}s ... ({:.2}x real-time)"`(`transcription.rs:1024-1027`),
`"Live preview finalized in ..."`(`transcription.rs:1496-1510`).

**핵심 관찰**

- **백로그 = 지연.** 피드는 `StreamHandle::feed`에서 무제한 큐에 쌓이고(`supervisor.rs:526-528`),
  엔진 소유 스레드는 프레임마다 **동기 왕복**(`supervisor.rs:954-994` → `protocol.rs:186-193`)을 한다.
  CPU에서 실시간률(RTF)이 1 미만인 모델(예: `whisper-large-v3` Q8_0 CPU RTF 0.97, §1.7)을 고르면
  낭독 중 백로그가 단조 증가하고, 릴리스 시 그 백로그를 다 소화해야 텍스트가 나온다.
  이때 화면에는 라이브 프리뷰가 계속 갱신되므로 사용자는 "느리다"보다 "말이 안 끝난다"로 느낀다.
- **2배 디코드 경로가 실제로 존재.** 라이브 스트림이 빈 텍스트를 내면 배치로 재시도한다(`live.rs:232-236`).
  스트림을 지원하지 않는 모델은 애초에 배치 1회지만, 스트리밍 모델에서 스트림이 실패하면 2회다.
- **장문 꼬리.** 라이브 청킹은 컷 확정에 5 s 룩어헤드를 요구하므로(`chunking.rs:27-30`),
  정지 시점에 최대 40 s(`MAX_CHUNK_SAMPLES`)가 미디코드로 남는다 → 그만큼의 디코드가 릴리스 후 지연.

### 1.4 경로 C — 오디오 콜백 → IPC (라이브 프리뷰)

프레임 크기는 **VAD 구현이 결정**한다: Silero = 512샘플 = **32 ms**(`vad/mod.rs:66-70` 테스트가 512를 고정),
earshot = 256샘플 = 16 ms(`vad/mod.rs:71-75`). 기본 백엔드는 Silero(`settings.rs:1065`, `:1591`) → **31.25 프레임/s**.

```
capture 스레드(resolver/delivery, 실시간 오디오 스레드 아님)
 └ CaptureSink::process_chunk                            audio/sink.rs:173
     ├ visualizer.feed(real) → 매 프레임 FFT(512) + Vec 할당   visualizer.rs:86-155
     ├ samples.extend_from_slice(frame)   ← 녹음 전체를 Vec<f32>로 누적   sink.rs:166-169
     └ on_audio(frame) → stream_router.feed(frame)        managers/audio.rs:536
          ├ relaxed atomic 확인 후 Mutex → tx.send(frame.to_vec())   transcription.rs:182-189
          → [스트림 워커 스레드] rx.recv → StreamHandle::feed(pcm)    transcription.rs:885-895
          → [엔진 소유 스레드] call_work(Feed) = write+flush+read(동기 왕복)  supervisor.rs:970
             └ Response::Fed { update, text } → on_progress(StreamProgress)  supervisor.rs:971-983
                  └ preview_script.convert(...) → emit_stream_text(...)     live.rs:173-184
                       └ StreamTextEvent{ committed: String, tentative: String } emit  transcription.rs:1410-1416
```

**프레임당 비용/바이트 산정(추정)**

| 항목 | 계산 | 값 |
| --- | --- | --- |
| 프레임 | 16 000 / 512 | 31.25 회/s |
| 할당 | `frame.to_vec()` 2 KB + `buckets` Vec + 엔진 프레임 Vec(헤더+PCM) | ≥3회/프레임 → ~94회/s |
| 프로세스 IPC | 프레임 2 048 B + JSON 헤더(`encode_message`) + `flush` | 31.25 × (~2.1 KB) ≈ 66 KB/s (양방향이면 ×2) |
| 채널 홉 | audio→router→stream worker→engine owner = 3홉 | 지연은 µs급, 처리량은 1코어의 일부 |
| 텍스트 emit | **revision이 바뀔 때마다 committed 전체 길이** | 장문에서 O(n²) 누적 |

누적 바이트 추정: 낭독 T초 동안 텍스트가 L자까지 선형 증가하고 초당 k회 emit되면
총 전송량 ≈ L·k·T/2. T=600 s, L=5 000자, k=2(보수적) → 약 3×10⁶ 바이트(=3 MB)에 달한다.
프레임 수(18 750) 대비 이벤트 수는 적지만 **이벤트 1건의 페이로드가 단조 증가**하는 것이 문제다.
같은 파일의 마이크 레벨 경로는 이미 30 FPS 스로틀 + `emit_to` 단일 창으로 최적화돼 있다
(`overlay.rs:817-844`) — 즉 **패턴은 저장소 안에 이미 있고, 텍스트 경로에만 적용되지 않았다.**

### 1.5 경로 D — 시작 시간 / 모델 로드·전환

`setup` 순서(코드 그대로):

| 순서 | 동작 | 근거 |
| --- | --- | --- |
| 1 | 플러그인 10여 개 init, `manage(cli_args)`, `NoticeStore` | `lib.rs:930-945` |
| 2 | **메인 웹뷰 생성**(`visible(false)`) | `lib.rs:999-1018` |
| 3 | Windows: WebView2 accelerator 끄기(COM cast) | `lib.rs:1038-1055` |
| 4 | `get_settings`, 테마 적용, 로그 레벨 | `lib.rs:1057-1079` |
| 5 | `TranscriptionCoordinator::new` | `lib.rs:1081` |
| 6 | `initialize_core_logic`: ModelManager → TranscriptionManager → AudioRecordingManager → HistoryManager(동기 SQLite 마이그레이션) → 트레이 → **오버레이 웹뷰 생성** | `lib.rs:195-397` |
| 7 | `secure_input::init`, 오버레이 캐시 | `lib.rs:1088-1096` |
| 8 | **디바이스 프리워밍 스레드**(GPU/Metal 셰이더 캐시 회피) | `lib.rs:1107-1112` |
| 9 | `show_main_window()` | `lib.rs:1129-1131` |

- `ModelManager::new`는 카탈로그 76종을 레지스트리에 시딩하고(`model.rs:1124-1134`),
  모든 엔트리에 대해 디스크 존재/부분 파일 크기를 stat한다(`model.rs:1314-1372`).
  커스텀 모델은 GGUF 헤더 64 KiB 프로브까지 한다(`model.rs:1630-1634`).
- 카탈로그 JSON 190 242 B는 `include_str!` + `Lazy` 파싱(`catalog/mod.rs:307-308`)이며
  시딩 시점에 강제 평가된다.
- **오버레이는 설정과 무관하게 부팅 시 생성**된다(`lib.rs:396-397`). `overlay_style: none`이어도
  웹뷰 프로세스가 하나 더 뜨고, 1.31 MB 로케일 JSON을 다시 파싱한다.
- 모델 로드 자체는 워커 프로세스 spawn + `Load` 요청이며 타임아웃이 `HELLO 60 s / LOAD 180 s`로
  매우 관대하다(`supervisor.rs:38-40`). 발화 시작 시에는 `initiate_model_load`가 **비블로킹**이라
  녹음은 즉시 시작되고, 전사만 로드 완료를 기다린다(`actions.rs:488`, `transcription.rs:696-728`,
  `transcription.rs:914-916` "waiting behind a model load is a queue position, not a failure").
- 유휴 언로드 기본값 `Min5`(`settings.rs:150-160`), 감시 주기 10 s(`transcription.rs:312`).
  5분 침묵 후 다음 발화는 **콜드 로드**를 문다. 사용자 체감으로는 "가끔 첫 발화만 느리다"로 나타난다.

### 1.6 프런트엔드

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| 엔트리 | `main` + `overlay` 2개, `manualChunks` 없음 | `vite.config.ts:16-26` |
| 코드 스플리팅 | 없음 — 설정 6개 화면, 온보딩 전 화면, `react-markdown`, `react-select` 정적 import | `src/App.tsx:16-38`, `src/components/Sidebar.tsx:6-14` |
| i18n | 26개 로케일 **eager** 인라인 (1 343 007 B) | `src/i18n/index.ts:12-25` |
| 부팅 부수효과 | React 렌더 **전** 스토어 3개 initialize(invoke 3건) | `src/main.tsx:26-31` |
| 오버레이 상태 갱신 | `mic-level` 30/s + `stream-text`(최대 31/s) + 1 s 타이머 → 각각 별도 `setState` | `RecordingOverlay.tsx:157-167`, `:169-171`, `:226` |
| 강제 리플로우 | 텍스트 갱신마다 `scrollHeight` 읽기 | `RecordingOverlay.tsx:232-238` |
| 히스토리 | 무한 스크롤 페이지네이션(가상화는 아님) | `HistorySettings.tsx:182-206` |
| 모델 표 | 76+ 행, 키 입력마다 전 행 필터(가상화 없음) | `ModelTable.tsx:29-53` |
| 좋은 사례 | 다운로드 진행률 100 ms 스로틀 + 1 s 틱 | `managers/model/download.rs:30-33` |

### 1.7 리소스 — 모델·메모리·가속

카탈로그(`src-tauri/src/catalog/catalog.json`)는 **모델별 실측 RTF(real-time factor)** 를 담고 있다
(`results.<id>.performance.accelerated|cpu`, 기준 머신 `ryzen-4750u`, backend `vulkan|cpu`,
`benchmarks.speed`). 기본 양자화는 전 모델 Q8_0(`benchmarks.quant`). 정적 추출 결과:

| 모델 | 크기(Q8_0) | RTF(GPU) | RTF(CPU) | 30 s 발화 디코드(CPU) | 스트리밍 |
| --- | --- | --- | --- | --- | --- |
| parakeet-tdt_ctc-110m | 135 MB | 76.5 | 50.0 | 0.60 s | ✗ |
| canary-180m-flash | 218 MB | 33.1 | 21.7 | 1.38 s | ✗ |
| parakeet-tdt-0.6b-v2 | 730 MB | 27.4 | 14.3 | 2.10 s | ✗ |
| **parakeet-unified-en-0.6b** | **731 MB** | **25.3** | **14.0** | **2.15 s** | ✓ |
| whisper-small | 270 MB | 11.7 | 5.9 | 5.12 s | ✗ |
| whisper-large-v3-turbo | 886 MB | 3.1 | 1.4 | 20.98 s | ✗ |
| whisper-large-v3 | 1 669 MB | 2.0 | **0.97** | **30.93 s** | ✗ |
| Voxtral-Mini-4B-Realtime | 4 732 MB | 0.76 | 0.59 | 51.28 s | ✓ |

(디코드 시간은 `30 s ÷ RTF` 단순 환산 — **추정**. 카탈로그 원값은 RTF뿐이다.)

메모리: 워커 프로세스는 모델 가중치(위 크기) 이상을 상주시킨다. 정확한 RSS는 미실측(§5).
앱 쪽 상주는 ① 메인 웹뷰 ② 오버레이 웹뷰 ③ 트레이 ④ VAD(Silero F32 1.2 MB)이며
②는 설정과 무관하게 항상 존재한다(H3).

---

## §2 개선안 (효과순)

정렬 기준: 사용자 체감 지연/자원에 대한 기대효과 ÷ 비용(위험 포함).

| 순위 | 개선 | 태그 | 비용 | 1차 지표 |
| --- | --- | --- | --- | --- |
| 1 | 라이브 텍스트 emit 병합·스로틀(변경분만) | [즉시] | S | IPC 바이트/s, 프리뷰 프레임 시간 |
| 2 | 전사를 `spawn_blocking`으로 이동 | [즉시] | S | tokio 워커 점유율, 동시 다운로드 지연 |
| 3 | i18n 로케일 지연 로드 + 오버레이 지연 생성 | [즉시] | S~M | 첫 창 표시 p50, 상주 프로세스 수 |
| 4 | 라이브 실패 시 재디코드 범위 축소 | [단기] | M | 말 끝→텍스트 p95 |
| 5 | 설정 스냅샷 캐시(Arc + 무효화) | [단기] | M | 키 누름→녹음 시작 p50 |
| 6 | `try_start_recording`의 state 락 범위 축소 | [단기] | S | 동시 start/stop 지연 |
| 7 | WAV 저장 시 샘플 복사 제거 | [단기] | S | 정지 시 RSS 스파이크 |
| 8 | 설정 화면 `React.lazy` + 모델 표 가상화 | [단기] | M | 첫 렌더 TTI |
| 9 | 스트림 피드 파이프라인(백로그 상한 + 우선순위) | [장기] | L | 백로그 ms, 지연 p95 |
| 10 | 워커 프로세스 워밍/모델 상주 정책 조정 | [장기] | L | 첫 발화 p50 |

### 2-1 [즉시] 라이브 텍스트 emit 병합·스로틀 — S

**before** (`transcription.rs:1410-1416`, `live.rs:173-184`)

```rust
fn emit_stream_text(app_handle: &AppHandle, committed: &str, tentative: &str) {
    let _ = StreamTextEvent { committed: committed.to_string(), tentative: tentative.to_string() }
        .emit(app_handle);   // revision마다 전체 문자열, 스로틀 없음
}
```

**after** — 마이크 레벨과 같은 패턴(`overlay.rs:821-833`)을 텍스트에도 적용. 마지막 emit 이후
33 ms 미만이면 최신 값만 보관하고, 타이머(16~33 ms)로 1건만 내보낸다.

```rust
// 최신 값은 AtomicU64 타임스탬프 + Mutex<Option<(String,String)>>
struct TextEmitter { last: AtomicU64, pending: Mutex<Option<(String, String)>> }
impl TextEmitter {
    fn offer(&self, c: &str, t: &str) {
        *self.pending.lock().unwrap() = Some((c.to_owned(), t.to_owned()));
        let now = now_ms();
        if now.saturating_sub(self.last.load(Relaxed)) < EMIT_THROTTLE_MS_TEXT { return; }
        self.last.store(now, Relaxed);
        if let Some((c, t)) = self.pending.lock().unwrap().take() { emit_stream_text(&c, &t); }
    }
}
```

효과(추정): 이벤트 수 상한 30/s → 30/s로 같아 보이지만, **변경 빈도가 프레임 빈도(31/s)와
무관해지고** 동일 프레임 다중 revision이 병합된다. 장문에서 누적 바이트가 줄고,
프런트는 렌더·리플로우를 최대 30/s로 예측 가능하게 만든다. 위험: 낮은 스로틀에서 타이핑이
"뚝뚝" 끊겨 보일 수 있음 → 16 ms(60 FPS)도 후보. `RecordingOverlay.tsx`의 `useLayoutEffect`
리플로우도 같은 주기로 묶인다.

### 2-2 [즉시] 전사 경로를 `spawn_blocking`으로 — S

**before** (`actions.rs:697-771`)

```rust
tauri::async_runtime::spawn(async move {
    ...
    if let Some(samples) = rm.stop_recording(&binding_id, cancel_generation) { ... }
    let transcription_result = tm.transcribe_recording(samples);   // 수 초 블로킹
    let wav_saved = match wav_handle.await { ... };
```

**after** — 블로킹 부분만 블로킹 풀로:

```rust
tauri::async_runtime::spawn(async move {
    let Some((samples, input_silent)) = tauri::async_runtime::spawn_blocking({
        let rm = rm.clone(); let b = binding_id.clone();
        move || rm.stop_recording(&b, cancel_generation).map(|s| (s, rm.last_input_silent()))
    }).await? else { ... };
    ...
    let transcription_result =
        tauri::async_runtime::spawn_blocking(move || tm2.transcribe_recording(samples)).await?;
```

같은 문제가 `shortcut/mod.rs:95-99`(설정 읽기 + OS 단축키 등록을 async로 spawn)에도 있다.
효과(추정): 4코어에서 런타임 워커 1개(=25%)가 최대 수 초 점유되던 것이 사라져
동시 다운로드 스트림·기기 프리워밍·업데이트 확인이 지연되지 않는다. 위험: 낮음(순수 이동).

### 2-3 [즉시] i18n 지연 로드 + 오버레이 지연 생성 — S~M

**before** (`src/i18n/index.ts:12-16`)

```ts
const localeModules = import.meta.glob<...>("./locales/*/translation.json", { eager: true });
```

26개 로케일 1 343 007 B가 양쪽 엔트리에 인라인되고, i18next가 초기화 시 전부 resource로 만든다.

**after** — 언어 하나만 정적 import(영어) + 나머지는 `import.meta.glob`(eager 제거)로 두고
`syncLanguageFromSettings()`에서 `await import(...)` 1건만 로드. 온디맨드 실행은 설정 언어가
OS와 같을 때가 대부분이므로 초기 네트워크/디스크 비용 없이 1회 파싱.

```ts
import en from "./locales/en/translation.json";
const lazyLocales = import.meta.glob<{ default: Record<string, unknown> }>("./locales/*/translation.json");
// i18n.init({ resources: { en: { translation: en } }, partialBundledLanguages: true })
```

같은 절에서: `create_recording_overlay`(`lib.rs:396-397`)를 **첫 녹음 시점(또는 overlay_style ≠ None일 때만)**
생성하도록 옮긴다. `overlay.rs:808-819`가 이미 `OVERLAY_ENABLED` 캐시로 "숨은 오버레이에 이벤트를
보내지 않는" 최적화를 하고 있으므로, 창 자체를 안 만드는 것이 그 최적화의 자연스러운 완성이다.

효과(추정): 부팅 시 파싱 대상 1.31 MB → 42.5 KB(en), 웹뷰 프로세스 1개 감소. 위험:
오버레이 첫 표시가 수십 ms 늦어질 수 있음 → 첫 녹음 시작 시 생성 + "arming" 상태가 이미 존재하므로 흡수 가능.

### 2-4 [단기] 라이브 실패 시 재디코드 범위 축소 — M

`live.rs:232-236`은 빈 스트림 결과를 배치 전체 재디코드로 넘긴다. 개선 2안:

- (a) 스트림이 **부분 committed 텍스트**를 남겼으면 그 접미 구간(마지막 커밋 이후 오디오)만 배치 디코드.
- (b) 스트림이 실패한 세션은 `stream_failed` 플래그를 세션에 남겨, 다음 발화부터 배치 경로로 바로 간다
  (같은 모델·같은 기기에서 반복 실패하는 경우 2배 비용을 매번 물지 않게).

효과(추정): 스트림 실패 세션에서 말 끝→텍스트가 약 절반. 위험: (a)는 커밋 경계 정합성 검증 필요.

### 2-5 [단기] 설정 스냅샷 캐시 — M

**before** (`settings.rs:1121-1167`): 매 호출 store 값 deep clone → `from_value` → 마이그레이션 →
기본 바인딩 병합.

**after**: `OnceLock<RwLock<Arc<AppSettings>>>`에 스냅샷을 두고 `write_settings`/`store` 변경 시에만
무효화. 호출부 141곳은 시그니처 그대로 두고 내부만 바꾼다(`get_settings` → `Arc` 반환은
전파 범위가 크므로, 우선 `get_settings_cached()`를 추가해 핫패스(핸들러·start·stop)만 교체).

효과(추정): 키 누름 경로에서 수십~수백 µs 제거(값 자체가 µs급이므로 **1차 병목은 아니다**).
진짜 이득은 부수효과 제거: `get_settings`가 읽기 도중 `store.set`을 호출하는 경로
(`settings.rs:1151-1153`, 마이그레이션·기본값 병합)가 핫패스에서 사라진다. 위험: 캐시 무효화 누락 시
설정 변경이 반영되지 않는 버그 — 테스트로 "변경 즉시 반영" 고정 필요.

### 2-6 [단기] `try_start_recording` 락 범위 축소 — S

`managers/audio.rs:1042`에서 `state` 가드를 잡고 `start_microphone_stream()`(기기 열거·open)까지
수행한다(`:1059`). 기기 open 동안 `state` 락이 잡혀 있으면 `stop`, `update_vad_backend`,
`close_microphone` 경로가 대기한다.

**after**: 상태 전이를 명시적 2단계로 — `Idle → Opening`(락 잡고 표시 후 즉시 해제) → 실제 open →
`Recording`. `set_state`가 이미 단일 쓰기 지점(`audio.rs:1026-1035`)이므로 변형 추가가 쉽다.
효과(추정): 동시 조작(빠른 더블 탭, 취소) 시 대기 제거. 위험: 상태기계 확장이므로 회귀 테스트 필요.

### 2-7 [단기] WAV 저장 시 샘플 복사 제거 — S

`actions.rs:737`의 `samples.clone()`은 녹음 전체(5분=19.2 MB, 추정)를 정지 순간 복사한다.
`samples`를 `Arc<[f32]>`로 감싸 WAV 기록과 디코드가 공유하거나, WAV 기록을 디코드 **후**로 미루고
소유권을 이동한다. 단, 지금은 "저장과 전사를 병행"하는 설계 의도가 있으므로(`actions.rs:732-747` 주석)
`Arc` 공유가 정답. 효과: 정지 순간 RSS 스파이크 제거. 위험: 낮음.

### 2-8 [단기] 설정 화면 지연 로드 + 모델 표 가상화 — M

`App.tsx:28`·`Sidebar.tsx:8-14`의 정적 import를 `React.lazy`로 바꾸고, `ModelsSettings`·
`HistorySettings`·`PostProcessingSettings`만 분리. 모델 표는 76+ 행 × 복잡한 행 컴포넌트이므로
키 입력 필터마다 전 행 재렌더된다(`ModelTable.tsx:29-53`) → `useDeferredValue` + 행 `memo` 또는
고정 높이 가상화. 효과: 첫 렌더 JS 파싱·평가 감소, 설정 창 입력 응답성. 위험: 낮음(온보딩 전환 애니메이션 주의).

### 2-9 [장기] 스트림 피드 파이프라인 — L

지금은 프레임마다 동기 왕복(`supervisor.rs:954-994`)이고 큐에 상한이 없다. 개선:
① 피드 큐에 상한(예: 2 s 분량) + 초과 시 오래된 프레임 드롭 정책 명시,
② 배치 피드(2~4프레임 묶음, `Feed` 요청 1회)로 왕복 수 감소,
③ `pending_ms` 기반 백로그 지표를 UI/로그로 노출해 "느린 기기"를 사용자가 진단 가능하게.
효과: 왕복 수 1/2~1/4, 백로그 상한으로 꼬리 지연 예측 가능. 위험: 스트림 상태기계·취소 경로 회귀 위험 큼.

### 2-10 [장기] 워커 워밍·모델 상주 정책 — L

기본 5분 유휴 언로드(`settings.rs:150-160`)는 메모리와 첫 발화 지연의 트레이드오프다.
개선: ① 최근 사용 모델 1개를 "웜"으로 유지하는 옵션(메모리 한도 기반),
② 소형 모델(≤300 MB: `parakeet-tdt_ctc-110m` 135 MB, `canary-180m-flash` 218 MB)은 상시 상주 후보로 분류,
③ 언로드/로드를 로그 p50으로 노출. 효과: 첫 발화 지연 체감 개선. 위험: 상주 메모리 증가.

### [제외] 검토했으나 상위에서 뺀 것

- **`get_settings` 단독 최적화**: 값이 µs급(추정)이라 지연 기여가 작다. 2-5에서 부수효과 제거 목적으로만 다룬다.
- **`stream_router.feed`의 `frame.to_vec()`**: 프레임 1개 2 KB, 31/s — 총량 66 KB/s. 2-9에서 묶어 다룬다.
- **VAD 전 구간 재스코어(청킹)**: Silero 1회 통과 비용은 디코드 대비 작다(추정). 장문에서만 유의미.
- **`emit_levels` 30 FPS**: 이미 스로틀 + 단일 창 최적화 완료(`overlay.rs:808-844`). 잘못된 지적이 된다.

---

## §3 측정 방법 — 무엇으로 증명할지

이 저장소는 **이미 계측 지점을 남겨 두었다.** 새 벤치를 만들기 전에 이것부터 수집해야 한다.

| 지표 | 이미 있는 로그(file:line) | 수집 명령/도구 | 통계 |
| --- | --- | --- | --- |
| 키 누름→녹음 시작 | `start-path pre-recording steps`(`src-tauri/src/actions.rs:566-573`), `TranscribeAction::start completed`(같은 파일 `:649-652`), `mic stream breakdown`(`src-tauri/src/managers/audio.rs:897-902`) | `RUST_LOG=handy=debug` 실행 후 `%APPDATA%\handy\logs\*.log` grep | p50/p95 |
| 첫 샘플 도착까지 | `Recording request accepted in …`(`src-tauri/src/actions.rs:579-582`), `Microphone is receiving samples`(같은 파일 `:612`) | 위와 동일(두 줄 타임스탬프 차) | p50/p95 |
| 라이브 프리뷰 처리율·emit 빈도 | `Live preview perf: … {feed_count} frames fed, {emit_count} updates emitted` (`src-tauri/src/managers/transcription.rs:1476-1489`, 5 s 주기 같은 파일 `:45`) | 동일 | emit/feed 비율, RTF |
| 말 끝→텍스트 | `Recording stopped and samples retrieved in`(`src-tauri/src/actions.rs:709-713`), `Transcription completed in …`(`src-tauri/src/actions.rs:784-788`, `src-tauri/src/managers/transcription.rs:1024-1027`), `Live preview finalized in`(`src-tauri/src/managers/transcription.rs:1496-1510`) | 동일 | p50/p95, 실패 세션 비율(=2배 디코드 발생률) |
| 모델 로드/언로드 | `Loaded transcribe-cpp model …`(`src-tauri/src/managers/transcription.rs:644-658`), `Model unloaded … took {}ms`(같은 파일 `:357-360`), `Model idle for … unloading`(같은 파일 `:349-353`) | 동일 | 콜드/웜 로드 시간 |
| 시작 시간 | 없음(앱 내) | **외부**: `hyperfine --warmup 1 "Handy.exe --start-hidden"`(창은 안 띄움) / GUI 첫 프레임은 PresentMon 또는 WebView2 DevTools Performance | p50/p95 |
| IPC 바이트/초 | 없음 | ① 이벤트에 바이트 카운터 추가(`StreamTextEvent` 직렬화 길이 누적, debug 전용) ② DevTools Performance → Event/Task/Parse/Style/Layout | 합계, 최대 |
| GPU/백로그 | `pending_ms`(`src-tauri/src/engine_supervisor/supervisor.rs:1071-1076`) | ① `--debug` 로그에 pending_ms 주기 출력 ② `xperf`/ETW(Windows), `Instruments`(macOS) | max backlog(ms) |
| 상주 메모리 | 없음 | Windows: `Get-Process handy` 후 `WorkingSet` 열 확인; macOS: `footprint` | RSS, 프로세스 수 |
| 번들 크기 | 없음(마이크 없음) | `bun run build` 후 `dist/assets/*.js` 크기 (이 환경엔 bun 없음 → 미확인) | raw/gzip |

**제안 하네스(저비용, 저장소 스타일 유지)**

1. `tests/`에 이미 단위 테스트 다수(`managers/audio.rs:1404-1410` 등). 여기에 **합성 오디오 결정성 테스트**를 추가:
   `Chunker`에 30 s 사인파 + 무음 3 s를 넣어 컷 개수·경계를 고정(회귀 방지).
2. `--debug`에서만 동작하는 `handy --bench-dictation <wav>`: 모델 로드 → 배치 디코드 → `(audio_secs, compute_secs, RTF)`
   를 stdout JSON으로. 카탈로그의 벤치 머신 값(`catalog.json benchmarks.speed`)과 같은 축이라 비교 가능.
3. 스트림 경로는 `emit_count / feed_count`와 이벤트 평균 바이트를 `Live preview finalized` 줄에 추가
   (이미 `feed_count`·`emit_count`가 있으므로 필드 2개만 추가).
4. 프런트는 Playwright가 이미 의존성에 있다(`package.json`, `playwright.config.ts`).
   `page.evaluate`로 `performance.getEntriesByType('longtask')` + 합성 `stream-text` 이벤트를 쏘아
   프레임 시간을 측정하면, H1/H2 개선 전후를 자동 비교할 수 있다.

---

## §4 TalkAbout 적용안 (Python/FastAPI/PostgreSQL)

전제: TalkAbout는 오디오가 없다. Handy에서 가져올 것은 **음성 파이프라인이 아니라 "핫패스에 전체 상태를
다시 만드는 패턴"과 "파생 지표로 지연을 예산화하는 방식"** 이다. 아래 수치는 이 환경에서
`.tmp-perf/bench_domain.py`·`bench_web.mjs`(미커밋 로컬 스크립트)를 실제로 돌려 얻은 값이다.

| Handy 병목 | TalkAbout의 같은 패턴(현재 코드) | 조치 |
| --- | --- | --- |
| H1 전체 텍스트 재전송 | SSE/롱폴 이벤트는 증분이지만, `public_room()`이 매 응답마다 `copy.deepcopy`로 방 전체를 재구성(`core.py:75-138`) | 프로젝션 캐시 or 1회 계산 후 재사용 |
| H6 매 이벤트 전체 재파싱 | **`PostgresStore.snapshot()`이 방마다 이벤트 전체(≤2049행)를 다시 읽고 deepcopy + `room_bounds` json.dumps**(`store.py:356-362`, `:404-413`) | 이벤트 페이징: 커서 이후만 조회 |
| H4 async 런타임 점유 | 좋은 사례: 모든 블로킹 코어 호출이 `run_in_threadpool`(`api.py:103`, `:131`, `:206` 등) | **Handy에 역제안할 패턴** |
| H5 2배 디코드 | `core.events()`가 폴 루프 1회에 `store.snapshot()`을 **2회** 호출(`core.py:695`, `:736`) | 루프 밖 1회 + 변경 감지 시에만 재확인 |
| H9 eager 로드 | 웹은 단일 모듈 즉시 로드(app.mjs 34 144 B + state.mjs 29 223 B gzip = 63 367 B) — 이미 작음. 다만 `state.mjs` 91 KB(원본)를 부팅 시 전량 평가 | 라벨 분리(언어별) 또는 지연 |
| H10 유휴 폴링(모델 언로드 감시) | 스케줄러 `0.5 s` 고정 루프(`api.py:30-33`) + `_flush`가 **무조건** `UPDATE`+`NOTIFY`(`store.py:364-373`) | dirty일 때만 flush/NOTIFY |

### 4-1 (최우선) `snapshot()`의 방 전체 이벤트 재적재 → 커서 페이징

`store.py:355-362`는 `_room()`에서 방마다 `SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049`를
실행한다(주석 `:359`가 "P0 shortcut"임을 스스로 인정). `core.events()`는 폴 1회에 이 스냅샷을 **2회**
호출하고(`core.py:695`, `:736`), 이벤트가 없으면 `wait(min(0.25, …))`로 잠깐 자고 다시 돈다(`:760`) →
**접속 클라이언트 1개당 최대 8회/초** 전체 방 재적재.

실측 근거(로컬 `bench_domain.py`, MemoryStore·DB 왕복 제외): 메시지 199개 방에서
`core.events()` 1회 **5.63 ms**, `room_snapshot()` **6.20 ms**, `room_bounds()` **1.43 ms**(room JSON 371 692 B).
Postgres 경로는 여기에 쿼리 2회와 2049행 역직렬화가 더 붙는다 → 폴 1회가 수 ms~수십 ms.

조치:
1. `events(room_id, after_seq, limit)` 쿼리를 추가하고 `_room()`은 최근 이벤트만(또는 0개) 적재.
2. `core.events()`의 스냅샷 2회 호출을 1회로 줄이고 `cursor_scope_changed` 검증은 `last_seq` 1값 비교로 대체.
3. 대기 루프는 `LISTEN/NOTIFY`(`store.py:443-449`)가 이미 있으므로 0.25 s 폴링을 제거하고 **알림 구동**으로 전환.
   Handy의 `emit_levels` 스로틀(`overlay.rs:821-833`)과 같은 "이벤트 폭을 상한으로 묶는" 사고방식.
4. 목표 예산을 수치로 박는다: Handy 카탈로그의 `methodology.freeWaitSeconds: 3`(30 s 발화에 3 s까지는
   "공짜 대기") 같은 방식으로, TalkAbout는 **"폴 1회 p95 ≤ 5 ms, 알림 후 첫 응답 p95 ≤ 50 ms"** 를
   `tools/`에 상시 측정으로 넣는다.

### 4-2 `_flush` 무조건 write + NOTIFY → dirty 게이트

`store.py:364-373`은 변경이 없어도 `UPDATE rooms SET payload=…`와 `NOTIFY talkabout_changed`를 실행한다.
스케줄러가 `0.5 s`마다 모든 active 방에 대해 `tick_all()`을 돌리므로(`api.py:30-33`),
**초당 2회 × 방 수** 만큼 write + NOTIFY가 발생하고, 그 NOTIFY가 모든 롱폴 리스너를 깨워 4-1의 재적재를 유발한다.
Handy의 `maybe_log`/`emit_levels`처럼 "변경이 없으면 아무것도 하지 않는다"를 명시:

```python
if schedule(room, principals):          # 반환값 = 변경 여부 (현재 domain.schedule은 None 반환 → 플래그 반환으로 변경 필요)
    self._flush(conn, room, row["last_event_seq"])   # room_bounds/UPDATE/NOTIFY는 여기서만
```

또한 `tick_all()`이 방마다 `self.connect()`로 **새 연결을 여는** 구조(`store.py:432-441`)는
방 4개 상한(`core.py`/`store.py:399`)에서는 견디지만, 연결 풀 1개로 묶는 편이 낫다.

### 4-3 (역방향 기여) TalkAbout의 threadpool 패턴을 Handy에 제안

TalkAbout는 모든 동기 코어 호출을 `run_in_threadpool`로 넘긴다(`api.py:103`, `:124-143`, `:148-213`).
Handy는 전사(수 초 블로킹)를 `async_runtime::spawn` 안에서 직접 호출한다(`actions.rs:697-771`).
§2-2 개선안의 근거가 TalkAbout 코드에 이미 있는 셈이다 — "블로킹 작업은 블로킹 풀로" 를
양쪽 보고서에 동일 문장으로 넣을 수 있다.

### 4-4 측정 하네스 이관

`.tmp-perf/bench_domain.py`는 이미 `core.rooms/read/room_snapshot/events`, `room_bounds`, `prompt_text`,
이벤트 1건 평균 바이트(실측 223.4 B)를 잰다. 여기에 **`bench_events_poll`**(폴 1회 비용 + 스냅샷 호출 횟수 카운터)을
추가하면 4-1/4-2 개선 전후를 같은 스크립트로 비교할 수 있다. `bench_web.mjs`는 이 환경의 Node 24에서
`vm.SourceTextModule`이 없어 모듈 파싱 측정만 실패했다(자산 크기 측정은 정상: app.mjs 34 144 B gz,
state.mjs 29 223 B gz) — 파싱 측정은 `--experimental-vm-modules` 플래그로 고칠 수 있다.

---

## §5 미확인 / 한계 (정직하게)

| 항목 | 상태 |
| --- | --- |
| 실측 수치 전부 | **미실측.** 이 환경에 cargo/rustc/bun이 없어 빌드·실행 불가. §1·§2의 ms 값은 상수 기반 계산이며 "추정"으로 표기했다. |
| 앱 시작 시간(첫 프레임까지) | 미확인. 코드 순서(§1.5)는 확인했으나 각 단계 소요 시간은 모른다. |
| 번들 크기 | 미확인. `vite build` 미실행. i18n 1 343 007 B는 **원본 JSON 바이트 합**이며 번들 내 증가분이 아니다. |
| `app.store(path)` / `store.get()`의 I/O 성격 | 미확인. tauri-plugin-store 내부 구현이 이 저장소에 없다. 메모리 캐시인지 디스크 재읽기인지에 따라 H6의 비용이 수십 배 달라진다. |
| `handy-recorder`(cpal) 내부 | 미확인. 크레이트 외부 의존(`Cargo.toml` `handy-recorder = "0.1"`)이라 콜백 스레드 구조·리샘플러 비용은 코드로 확인할 수 없다. sink 계약(프레임 크기·delivery 스레드)만 확인했다. |
| `transcribe-cpp` 내부 추론 비용 | 미확인. 카탈로그의 RTF는 저장소가 기록한 **측정값**이며 내가 재현하지 않았다. |
| 워커 프로세스 RSS | 미확인. 모델 파일 크기는 하한일 뿐이다. |
| 2배 디코드 발생률 | 미확인. 경로가 존재함(`live.rs:232-236`)은 확인했으나 실제 빈도를 모른다. 로그의 `Live transcription produced no text; retrying in batch`(`live.rs:235`) 빈도로 측정해야 한다. |
| 스트림 `text` 이벤트 실제 빈도 | 미확인. `revision` 변경 시에만 emit되며(`worker.rs:203-206`), revision이 얼마나 자주 오르는지는 모델·백엔드 의존이다. 상한(31.25/s)만 계산했다. |
| macOS/Windows별 차이 | 일부만 분기 확인(Windows WebView2 accelerator, macOS activation policy). 성능 수치는 플랫폼별로 다르며 여기서는 플랫폼 중립 경로만 다뤘다. |
| 부재 주장의 범위 | "가상화 없음", "코드 스플리팅 없음", "텍스트 emit 스로틀 없음"은 `grep`으로 찾지 못한 것에 한정한다(`React.lazy`·`manualChunks`·`Suspense`·텍스트 emit 타이머 미발견). |

### 이 저장소가 이미 잘하고 있는 것 (개선 제안에서 제외)

- 마이크 레벨 emit: 30 FPS 스로틀 + `emit_to` 단일 창 + `OVERLAY_ENABLED` 원자 캐시(`overlay.rs:790-844`).
- 다운로드 진행률: 100 ms 스로틀 + 1 s 틱(`model/download.rs:30-33`).
- 설정 캐시가 필요한 자리(오디오 콜백)는 이미 원자 캐시로 우회(`overlay.rs:790-794` 주석).
- 모델 로드를 녹음 시작과 분리(비블로킹 kickoff, `transcription.rs:696-728`).
- 워커 프로세스 격리로 GPU 크래시·행 격리(`supervisor.rs:1-15`, 타임아웃 `:38-47`).
- 처분 지점마다 `Instant` 계측을 남겨 둔 것(§3 표) — 이 보고서의 측정 계획 대부분이 "이미 있는 로그를 모으는 것"으로 끝난다.
