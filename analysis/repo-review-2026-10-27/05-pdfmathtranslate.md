# 05. PDFMathTranslate 정적 소스 심층 분석

## 1. 판정과 검토 범위

**판정: TalkAbout W1의 필수 의존성으로 도입하지 않는다. 문서 기능이 실제로 필요해질 때에만 별도의 선택적·격리된 document-analysis job과 private-room artifact 경계를 설계한다. AGPL 코어를 TalkAbout 서버에 직접 import하거나 소스를 복사하는 방식은 권고하지 않는다.**

- 분석 기준일: 2026-10-27. 대상: `PDFMathTranslate/PDFMathTranslate`, 고정 커밋 `3f2ab95ae9d74d0ed9a5ff018fe71280223906a8`. 로컬 HEAD 텍스트가 이 값을 가리키는 것을 읽어 확인했다. 작업 트리 전체의 해시 동일성이나 upstream 진위까지 검증한 것은 아니다.
- 방법: 로컬 소스·문서·테스트를 읽는 **정적 분석만** 수행. 애플리케이션/테스트/셸 명령 실행, 설치, 모델 다운로드, 네트워크 요청은 하지 않았다. 아래 GitHub 링크도 방문하지 않고 고정 소스 위치를 인용하기 위해 구성했다. 저장소 변경·커밋 없음. 생성 산출물은 이 보고서 하나다.
- TalkAbout 전제는 의뢰자가 제공한 현재 경계다: 크기가 제한된 inert UTF-8 `.txt`/`.md`만 허용, binary upload 없음, private rooms, 실제 coding+analysis W1. 이 보고서가 TalkAbout 구현 자체를 별도 감사한 것은 아니다. PDF 기능은 이 경계를 넓히는 **신규 제품·보안 프로젝트**이지 기존 텍스트 첨부의 자연스러운 확장이 아니다.
- README는 2.0이 [PDFMathTranslate-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next)로 이동했다고 명시한다. 이 링크는 후속 검토 대상의 안내일 뿐, next 저장소의 코드·품질·라이선스를 이 보고서가 검증하지 않았다. 이 고정 저장소의 precise 어댑터까지만 분석한다. [README:65–80](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/README.md#L65-L80)

## 2. 제품 적합성: 무엇을 가져올 가치가 있는가

이 프로젝트의 핵심은 과학 PDF의 **기존 화면 배치와 수식을 최대한 남긴 번역 PDF 생성**이다. 범용 문서 이해 엔진, 정답 보증 OCR, 코드 실행 도구, private-room 협업 플랫폼이 아니다. README의 “수식·차트·목차·주석 보존”은 제품 설명이며 모든 입력에서 성립하는 검증 결과로 해석하면 안 된다. [README:51–57](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/README.md#L51-L57)

TalkAbout에서 참고할 만한 것은 **원본과 변환 결과의 분리, 문서→페이지→문단 작업 계층, 교체 가능한 provider, 비용이 큰 작업의 비동기화**라는 설계 개념이다. W1의 실제 코딩·분석 수행과 검증 가능한 결과/근거 기록을 먼저 완성하고, PDF 번역/문서 분석은 별도 opt-in capability로 미루는 편이 맞다. 이 프로젝트를 넣는 것만으로 coding agent의 실행 격리나 분석 신뢰성이 해결되지는 않는다.

## 3. 레이아웃 보존의 구체적인 파이프라인

### 3.1 일반 legacy 경로

1. **입력과 작업본 분리**: `translate_stream`은 PDF bytes를 PyMuPDF `Document`로 열고, 일반 모드에서는 원본을 다시 저장해 별도 작업본을 만든다. 출력 언어 폰트를 준비한 뒤 작업본에 폰트를 삽입하고 xref 리소스를 보완한다. 외부 폰트가 없으면 BabelDOC asset 함수로 위임한다. [high_level:410–470](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L410-L470), [high_level:673–696](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L673-L696)
2. **선택적 OCR 전처리**: 텍스트가 없고 이미지가 있는 선택 페이지만 대상으로 300dpi 렌더링과 PyMuPDF OCR PDF 생성을 한다. 이미 텍스트가 있는 부분 스캔은 건너뛴다. 따라서 이미지+텍스트 혼합 페이지 전체의 OCR 복원이라고 부를 수 없다. [high_level:315–370](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L315-L370)
3. **페이지 이미지 → layout mask**: pdfminer로 페이지를 순회하면서 PyMuPDF 픽스맵을 ONNX layout model에 넣는다. `abandon`, `figure`, `table`, `isolate_formula`, `formula_caption` 영역은 mask 0의 보존 영역으로 덮고, 나머지 검출 영역에 문단 식별값을 붙인다. 즉 “표를 의미 구조로 재작성”하는 것이 아니라 **대상 영역과 보존 영역을 구분**하는 접근이다. [high_level:153–225](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L153-L225)
4. **글자·선·좌표 기반 문단 및 수식 분리**: converter는 mask 값, 글꼴 이름 정규식, 유니코드 수학 기호/그리스 문자, 작은 위첨자 크기, 수직 글자를 조합해 수식을 판정한다. 수식 glyph·선·상대 위치는 별도 stack에 남기고 번역할 텍스트에 `{vN}` placeholder를 넣는다. 수식 판정은 휴리스틱이므로 이탤릭 일반문, 코드, 특수 폰트, 읽기 순서가 복잡한 다단 문서에서 오분류할 수 있다. [converter:197–281](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L197-L281)
5. **문단 번역**: 순수 공백/수식 문단은 통과시키고 텍스트 문단을 provider로 보낸다. 일반 경로는 페이지 처리 중 thread pool로 문단을 번역한다. [converter:350–381](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L350-L381)
6. **원래 문단 좌표로 재조판**: 번역 텍스트에 CJK/Latin spacing을 적용하고 원래 x/y·문단 폭·글자 크기를 사용한다. placeholder를 만나면 저장한 수식 glyph와 선을 되살린다. 존재하지 않는 수식 번호는 skip하며, 우측 경계에서 줄을 바꾸고 원래 높이에 맞추려고 line-height를 낮춘다. 이는 손실 없는 round-trip이나 충돌 없는 자동 조판의 보장이 아니다. placeholder 누락/추가, 번역문 길이 팽창, 글꼴 glyph 부재가 시각/내용 손실로 이어질 수 있다. [converter:411–488](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L411-L488), [converter:534–552](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L534-L552)
7. **PDF content stream 패치와 결과 조립**: 새 페이지 xref/content stream을 만들고 interpreter가 얻은 patch를 작업본에 적용한다. 원본 뒤에 번역본을 삽입한 다음 페이지를 번갈아 이동해 dual을 만든다. 결과는 번역본 mono와 원문/번역 교차 페이지 dual의 두 bytes다. 선택 페이지가 있더라도 문서 전체를 열고 조립한다. [high_level:226–231](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L226-L231), [high_level:473–504](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L473-L504)

### 3.2 ultrafast와 precise는 같은 보장 수준이 아니다

- **ultrafast**: `pdf-inspector` native text 위치를 추출해 heuristic layout mask를 만들며 ONNX·OCR·폰트 subset을 생략한다. 문서 전역 paragraph future를 제출하고 완료 후 재파싱/조판한다. 페이지 간 동시 번역이 가능하지만 스캔 페이지에는 번역할 native text가 없어 skip한다. “모델 없는 layout 경로”이지 “네트워크 없는 번역”은 아니다. 폰트 준비와 번역 provider 호출은 남는다. [high_level:133–175](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L133-L175), [high_level:240–258](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L240-L258), [high_level:394–428](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L394-L428)
- **precise**: next 코어를 별도 venv/subprocess로 실행하는 어댑터다. `ensure_venv()`는 필요하면 venv 생성 및 `pip install -e`를 실행한다. 격리된 Python 의존성은 OS 보안 sandbox가 아니며, parent 환경을 복사한다. W1 서버 요청 중 이 자동 provisioning 경로를 호출해서는 안 된다. [precise:67–120](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/kernel/precise.py#L67-L120)

## 4. 작업 단위, 취소, 재시도, 캐시, provider 계약

| 항목 | 고정 소스에서 확인한 동작 | TalkAbout 적용 판단 |
|---|---|---|
| 외부 job 단위 | Flask/Celery는 문서 전체 bytes+args를 한 task로 enqueue, mono/dual bytes를 결과로 반환한다. | 대형 PDF를 broker/result backend에 그대로 싣기보다 artifact ref만 queue에 전달한다. |
| 내부 작업 단위 | 일반 경로는 페이지 loop + 페이지 안 문단 thread pool, ultrafast는 문서 전역 문단 future. | 파일 job 아래 page/region progress를 표현하되 재시작 가능한 checkpoint는 별도 설계한다. 현재 소스에서 durable page checkpoint는 확인하지 못했다. |
| progress | 페이지 처리 전에 callback을 호출하고 ultrafast 번역 단계에서는 total을 future 수로 reset한다. | 단일 퍼센트로 완료를 과장하지 말고 `stage`, `completed`, `total`, 단위를 명시한다. |
| 취소 | 페이지 경계 및 ultrafast future 완료 사이에서 event 검사. GUI는 event를 set. Flask DELETE는 `revoke(terminate=True)`. | in-flight HTTP·문단 retry가 즉시 중단된다고 가정 금지. 협력 취소 + deadline + 프로세스 강제 종료 및 잔여물 정리가 필요하다. |
| retry | converter 문단 worker의 `@retry(wait=wait_fixed(1))`에는 stop 조건이 없다. OpenAI는 RateLimitError에 최대 100회 exponential retry. | 중첩 시 무제한 재시도 가능성. 인증 오류/잘못된 입력도 외부 retry에 재진입할 수 있으므로 오류 분류·총 시도/시간/비용 예산·jitter·circuit breaker를 새로 둔다. |
| provider | `BaseTranslator.translate()` → cache → `do_translate()`. service 문자열은 `provider:model`로 나누고 하드코딩 클래스 목록에서 선택한다. | 참고할 수 있는 인터페이스 개념이나 tenant secret isolation, 데이터 전송 동의, capability, 비용/사용량, timeout 계약을 담는 범용 agent provider 계약은 아니다. |

근거: [backend:39–96](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/backend.py#L39-L96), [high_level:153–160](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L153-L160), [high_level:247–255](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L247-L255), [gui:204–218](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/gui.py#L204-L218), [converter:162–173](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L162-L173), [converter:354–381](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter.py#L354-L381), [translator:454–469](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L454-L469).

**캐시의 실제 범위와 private-room 문제**

- SQLite unique key는 `(translate_engine, translate_engine_params, original_text)`다. params는 재귀적으로 정렬한 JSON이다. 기본 params는 언어·모델이며 OpenAI는 temperature·prompt 등을 추가한다. **room/user/tenant/document hash는 기본 키에 없다.** 같은 worker 계정의 private rooms가 캐시와 원문/번역 평문을 공유할 수 있으므로 그대로 재사용하면 안 된다. [cache:12–28](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/cache.py#L12-L28), [cache:31–79](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/cache.py#L31-L79), [translator:46–62](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L46-L62)
- 기본 DB는 사용자 홈 아래 cache이며 import 말미에 `init_db()`를 호출한다. WAL/busy timeout은 있지만 TTL·room 삭제 연동·암호화·용량 제한은 이 구현에서 확인되지 않는다. [cache:93–107](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/cache.py#L93-L107), [cache:141](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/cache.py#L141)
- `ignore_cache`는 일반 translator에서 **읽기만 우회하고 쓰기는 한다**. `no_cache`만 읽기/쓰기 모두 막고, ultrafast+ignore_cache 경로에서 이를 설정한다. privacy UI에 “캐시 끄기”라고 붙이면 의미를 정확히 맞춰야 한다. OpenAI custom base URL은 cache params에 포함되지 않아 동일 모델명·prompt로 다른 endpoint를 쓰는 경우 캐시 재사용 의미도 점검해야 한다. [translator:91–105](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L91-L105), [high_level:123–125](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L123-L125), [translator:438–448](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L438-L448)
- provider `envs`는 ConfigManager에 다시 저장한다. config는 홈 디렉터리 JSON에 쓰므로 API key와 custom endpoint가 공유/영속화될 수 있다. worker에 private-room secret을 무차별 환경 상속하지 말고 job 단위로 최소 secret만 주입해야 한다. [translator:64–81](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L64-L81), [config:28–60](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/config.py#L28-L60), [config:138–152](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/config.py#L138-L152)
- precise의 sync/async signature에는 cancellation event가 있지만 두 실행 본문에서 event를 검사하지 않는다. sync는 stderr loop와 `wait()`, async는 `communicate()`를 기다린다. 어댑터 계약이 있다는 이유만으로 취소 동작이 보장되지는 않는다. [precise:122–180](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/kernel/precise.py#L122-L180), [precise:198–243](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/kernel/precise.py#L198-L243)

## 5. 결과물과 provenance: PDF 두 개만으로는 부족하다

기본 파일 API는 입력 basename에 `-mono.pdf`, `-dual.pdf`를 붙여 지정 output 경로에 직접 쓴다. 동일 basename의 충돌/overwrite, 중간 실패 후 부분 파일, 경로 접근 범위는 호출자가 통제해야 한다. `TranslateResult`도 PDF path/bytes와 time_cost만 담고 input hash, commit/model/prompt digest, page-source mapping, provider request ID, 동의 기록은 없다. [high_level:653–668](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L653-L668), [protocol:11–43](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/kernel/protocol.py#L11-L43)

TalkAbout에 필요한 독립적인 artifact manifest는 다음과 같다.

- `jobId`, `roomId`, requester, 권한 snapshot/재검증 정책, 부모 입력 artifact ID와 SHA-256, 입력 크기/MIME/page count.
- engine repository+commit/image digest, mode, dependency SBOM digest, layout/OCR/font asset digest, provider/model ID, prompt/config digest, 외부 전송 동의와 허용 endpoint.
- 페이지/영역 인덱스·원문 위치, OCR 여부·언어, 추출/번역 구분, 생략·overflow·placeholder mismatch 경고. 번역문을 원문 증거로 둔갑시키지 않는다.
- 결과 mono/dual/추출텍스트의 각 hash·크기·MIME·생성 상태, 시도 번호, 단계별 시간, 사용량/비용(관측 가능 범위), 실패/취소/만료 기록, retention/삭제 시각.
- 비밀키·원문 전문은 일반 로그/manifest에 넣지 않는다. 원문·추출·번역·미리보기를 각각 room ACL 대상 artifact로 관리하고 모든 다운로드에서 현재 권한을 재검사한다.

PDF/A 옵션은 metadata와 OutputIntent를 추가하는 구현이다. 이를 정식 PDF/A conformance 검증 또는 active-content 제거 증명으로 취급하면 안 된다. [high_level:507–553](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L507-L553)

## 6. 적대적 PDF 및 원격 콘텐츠 위험

다음은 **정적으로 관찰한 공격면과 설계 결손**이다. 실제 exploit 성공, 특정 CVE 존재/부재, 배포 환경의 취약 여부를 실증한 것은 아니다.

| 위험 | 근거와 영향 | 도입 전 최소 통제 |
|---|---|---|
| CPU/메모리/디스크 고갈 | 문서 전체 bytes를 읽고 원본/작업본/dual을 메모리에서 다루며 페이지 크기 배열과 OCR 300dpi 이미지를 만든다. 적은 압축 bytes라도 거대한 이미지/페이지/객체가 될 수 있다. [high_level:178–196](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L178-L196), [high_level:346–349](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L346-L349) | bytes/page count뿐 아니라 페이지 pixel·object·해제 후 메모리·wall time·output bytes 제한. parser까지 포함한 OS 격리와 kill budget. |
| SSRF·무제한 다운로드 | CLI URL 입력은 redirect 허용 `requests.get`으로 timeout/size cap 없이 `r.content` 저장. GUI는 streaming size limit/10초 timeout이 있으나 별도 코드에 사설망/DNS/redirect 목적지 검증이 없다. [high_level:596–614](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L596-L614), [gui:172–201](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/gui.py#L172-L201) | W1에서는 remote URL fetch 비활성. 향후 필요해도 전용 fetcher, IP/DNS·redirect 매 hop 차단, allowlist, egress deny by default. |
| 모델·OCR·폰트 공급망/암묵적 네트워크 | ONNX 경로·폰트를 BabelDOC asset API로 얻음. OCR data 다운로드는 고정 버전 URL이지만 `known_hash=None`. [doclayout:127–130](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/doclayout.py#L127-L130), [high_level:296–306](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L296-L306) | 빌드/승인 단계에만 확보, 해시·출처·라이선스 고정, runtime download 금지, 모델/폰트 read-only mount. 위임된 asset 코드의 실제 다운로드 동작/검증은 이번 범위 밖. |
| 공개 API를 private-room API로 오인 | Flask route 내 인증·room ownership·입력 제한 없이 task ID로 상태/삭제/결과 접근. 요청 form도 print. [backend:61–96](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/backend.py#L61-L96) | 이 backend를 외부에 그대로 노출 금지. TalkAbout gateway에서 room ACL/quotas/schema/CSRF 정책 적용, job ID의 난수성은 권한 검사가 아님. |
| 원문 외부 전송·prompt injection | 기본 prompt는 원문을 user content 안에 넣는다. PDF 속 지시문이 번역 품질/placeholder를 교란할 수 있다. 이 경로 자체의 tool 실행은 확인되지 않았지만, 결과를 W1 coding agent 지시로 승격하면 별도 권한 침범 경로가 생긴다. [translator:115–152](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/translator.py#L115-L152) | 원문/번역 모두 untrusted data로 표시, 분석 컨텍스트에서 instruction과 분리, tool 권한 부여 금지, 외부 provider 전송 전 opt-in·기밀성 정책. |
| 출력 PDF의 능동 콘텐츠 | 원본을 복사/패치해 결과를 조립하며 위 경로에 JavaScript/action/attachment/link를 전면 제거하는 sanitizer는 보이지 않는다. 이것이 모든 항목이 반드시 보존된다는 뜻도 아니다. | 출력도 불신. 별도 정제/검사, 격리된 preview origin, attachment download, 브라우저 inline 노출 최소화. PDF/A label로 안전을 주장하지 않는다. |
| 불필요한 변환기 확대 | `.doc`/`.docx`를 LibreOffice subprocess로 변환하는 경로도 있다. timeout 120초는 있으나 독립 OS sandbox는 아니다. [converter_docx:11–40](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/converter_docx.py#L11-L40) | 초기 문서 capability는 승인된 PDF 유형만, office 변환 capability는 별도 검토. 서버 파일 경로를 사용자 args로 넘기지 않는다. |

임시파일 관리도 독립 worker scratch에 한정해야 한다. `translate()`는 입력이 system temp 하위라고 판단하면 읽은 후 삭제하므로, 일반 서버가 소유한 공유 temp 경로를 그대로 넘기지 않는 편이 안전하다. [high_level:642–651](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pdf2zh/high_level.py#L642-L651)

## 7. 라이선스: 실제 AGPL과 의존성은 별도 검토

**확인된 사실**: 프로젝트 메타데이터의 license는 `AGPL-3.0`이며 실제 LICENSE 전문은 GNU Affero General Public License v3다. MIT/Apache라는 전제로 코드를 복사해서는 안 된다. 단순 배지 추정이 아니다. [pyproject:1–8](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pyproject.toml#L1-L8), [LICENSE:1–12](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/LICENSE#L1-L12)

- §13은 프로그램을 수정한 버전과 네트워크 상호작용하는 사용자에게 Corresponding Source를 받을 기회를 제공하는 문구를 포함한다. 배포 시 object code/Corresponding Source 관련 조건도 별도로 있다. private room 또는 상업 서비스라는 이유만으로 자동 면제/자동 금지라고 결론내릴 수 없다. [LICENSE:235–237](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/LICENSE#L235-L237), [LICENSE:540–559](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/LICENSE#L540-L559)
- **코드 없는 개념 참고**: 비동기 문서 job, 교체 가능한 provider, 원본/결과 artifact 분리 같은 일반 개념을 독자 구현하는 것과, 이 소스의 표현·함수·코드를 복사/수정/배포하는 것은 구별해야 한다. 설계 개념을 참고했다는 사실만으로 AGPL 통합 여부를 단정하지 않는다.
- **복사·수정·통합·배포**: 코어 import, 코드 복제, 수정 fork, container 배포, 네트워크 서비스 연동은 실제 구성·수정 여부·전달물·결합 형태를 제시하고 법률 자문을 받아야 한다. subprocess/별도 container/API 분리는 좋은 운영 경계지만 **라이선스 의무를 자동 소거하는 면책선이 아니다**. next 저장소와 BabelDOC도 각각 검토해야 한다.
- 의존성 선언은 requests/googletrans/PyMuPDF/pdfminer-six/pikepdf/ONNX/ONNX Runtime/OpenCV/fontTools/BabelDOC/Gradio 및 여러 provider SDK를 포함한다. PyMuPDF는 `<1.25.3`, pdfminer-six는 `==20250416`, BabelDOC은 `>=0.1.22,<0.3.0`, Gradio는 `>=6.16.0,<7` 등 혼합 pin/range다. 최상위 선언만으로 실제 설치 버전·전체 전이 의존성·라이선스 compatibility·취약점 상태를 인증할 수 없다. [pyproject:13–40](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pyproject.toml#L13-L40)
- backend/ocr/ultrafast/precise 등 extras로 의존성이 더 늘어난다. PyMuPDF처럼 별도의 copyleft/상업 라이선스 검토가 필요한 것으로 알려진 구성요소는 실제 선택 버전의 배포본 LICENSE로 확인해야 한다. 이 checkout만으로 각 dependency와 model/font/OCR data의 최종 권리조건을 확정하지 않았다. optional Argos 모델과 LibreOffice까지 포함한 SBOM·NOTICE·asset 출처 원장이 도입 gate다. [pyproject:42–64](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/pyproject.toml#L42-L64)

이 절은 기술적 위험 선별이며 법률 의견·준법 인증이 아니다.

## 8. 정적 테스트 검토와 향후 수용 gate

**아래는 테스트 실행 결과가 아니다.** 테스트 소스를 읽어 무엇을 주장하는지 대조한 결과다. 신규 테스트 파일도 작성하지 않았다.

| 정적 대조 항목 | 소스가 주장/검사하는 범위 | 남는 공백 |
|---|---|---|
| 모델 없는 ultrafast | rotation 0/90/180/270, cropbox·2단 텍스트·스캔 페이지를 만든 뒤 모델/렌더/OCR/subset 호출 시 실패하도록 mock. mono 3페이지/dual 6페이지, 텍스트·회전·이미지 보존을 assert. [test_ultrafast:38–141](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_ultrafast.py#L38-L141) | 폰트와 translator를 mock하므로 실제 원격 전송/글꼴 다운로드/번역 품질 검증이 아니다. hostile PDF·메모리 budget도 증명하지 않는다. |
| 캐시 기능 | get/set/overwrite, engine·params 구분, JSON 정렬 일관성을 검사. [test_cache:13–119](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_cache.py#L13-L119) | room isolation/TTL/삭제권한 검증 없음. thread safety 테스트는 DB locked 이유로 주석 처리됨. [test_cache:158–160](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_cache.py#L158-L160) |
| no_cache | cache get/set을 실패 mock으로 바꿔 read/write 우회를 검사. [test_ultrafast:144–155](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_ultrafast.py#L144-L155) | 일반 ignore_cache UI 의미와 tenant deletion은 별도 검증 필요. |
| converter | 초기화·문자 폭·기초 layout 호출, 일부 complex formula 객체 테스트. `receive_layout` 자체를 mock한 테스트도 존재. [test_converter:43–96](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_converter.py#L43-L96) | 실문서의 수식 대응 수/위치, 번역 팽창 후 겹침, 표/주석 보존을 전부 검증하지 않는다. |
| import 경계 | package import와 version CLI가 heavy pipeline을 먼저 로드하지 않는지 검사. [test_cli:19–36](https://github.com/PDFMathTranslate/PDFMathTranslate/blob/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8/test/test_cli.py#L19-L36) | 실제 translation import 시 DB/설정/asset side effects까지 없다는 뜻은 아니다. |

향후 구현 승인을 위한 **제안 테스트**(이번에는 미실행):

1. **현행 W1 불변성**: PDF/가짜 확장자/invalid UTF-8/NUL/과대 `.txt`·`.md`가 기존 제한에서 거부되는지, Markdown/문서 지시문이 tool instruction으로 승격되지 않는지 검증한다.
2. **private-room IDOR**: 다른 room 사용자의 job 조회·취소·다운로드·preview·캐시 접근을 모두 거부. 권한 회수·room 삭제 후 signed URL 및 artifact 접근도 차단한다.
3. **작업 예산/취소**: 영구 401/429/timeout, 멈춘 provider, 무한 retry 후보, 대형 페이지, 과도 객체 PDF에 대해 정해진 시간/메모리/비용 내 중단. pending future·child process·scratch·부분 결과까지 정리한다.
4. **네트워크 금지**: offline worker에서 URL 입력, loopback/사설망/metadata IP, DNS rebinding/redirect, runtime model/font/OCR 다운로드를 모두 실패시킨다. 승인 provider만 broker를 통해 egress한다.
5. **시각/의미 품질**: 수식·표·회전·cropbox·다단·주석·부분 스캔·악성 placeholder·한글 팽창 corpus에서 원본/결과 렌더 diff와 텍스트/placeholder 대응을 함께 평가한다. 픽셀 diff만으로 의미 정확성을 판정하지 않는다.
6. **provenance/재시도 멱등성**: 입력·출력 hash, engine/config/asset digest, attempt, 경고·부분 실패가 manifest에 남고 동일 job retry가 결과를 조용히 overwrite하지 않는지 확인한다.
7. **라이선스/공급망**: 승인된 고정 dependency와 asset으로 빌드한 SBOM·NOTICE·소스 제공 절차·법률 검토 기록 없이는 배포하지 않는다.

## 9. 권고 아키텍처와 도입 순서

```text
현행 TalkAbout W1: bounded inert UTF-8 text/Markdown → private-room coding+analysis

향후 별도 opt-in 문서 capability:
room-authenticated gateway → quarantine artifact → queued DocumentJob(ref only)
  → disposable document worker (read-only image/assets, scratch quota, no general egress)
  → provider broker (approved endpoint/secret/consent/budget only)
  → validate/sanitize → room-scoped artifacts + provenance manifest
  → text evidence/안전 preview (untrusted) → 사용자가 승인한 analysis context
```

- **지금**: PDF 업로드를 열지 않고 문서 분석 요구와 성공 기준을 검증한다. W1 coding/analysis job의 실제 실행·취소·실패·근거 UI를 우선한다.
- **설계 참고만 채택**: 단계형 progress, provider adapter, 원본/파생 artifact, 고정 provenance schema를 독립 구현한다. PDFMathTranslate 코드를 서버 내부로 가져올 필요는 없다.
- **조건부 실험**: 별도 이미지/배포 단위의 문서 worker를 feature flag 뒤에 두고 non-root·프로세스/파일시스템/네트워크 sandbox·quota·deadline을 적용한다. precise의 venv는 이 보안 격리를 대신하지 못한다. provider/secret도 웹 서버의 전체 환경을 상속하지 않는다.
- **제품 gate**: 바이너리 수신 정책·보관 기간·외부 전송 동의·room ACL·artifact 삭제·예산·실패 복구·시각 품질·라이선스 검토가 통과한 뒤에만 제한된 파일/페이지/사용자로 pilot한다.

**최종 결론:** PDFMathTranslate는 레이아웃 보존 번역 파이프라인을 배우기에는 유용하지만, 현재 TalkAbout의 inert text upload 정책 및 owner-opt-in W1 경계에 직접 끼워 넣을 서버 라이브러리로 적합하다고 판단할 근거는 부족하다. 선택적 격리 document-analysis job/artifact 아키텍처를 우선 검토하되, 별도 프로세스라는 이유로 보안 또는 AGPL 문제가 해결됐다고 주장하지 않는다.
