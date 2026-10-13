export const labels = {
  ko:{prototype:'비공개 text-only prototype',login:'관리자 로그인',workspace:'Workspace',username:'사용자 이름',password:'비밀번호',refresh:'새로고침',logout:'로그아웃',topic:'주제',create:'방 만들기',rooms:'방 목록',closeView:'보기 닫기',participants:'참여자',pause:'일시정지',resume:'재개',closeRoom:'방 종료',discussion:'대화',kind:'메시지 종류',question:'질문',advice:'조언',text:'내용',send:'보내기',artifacts:'텍스트 산출물',tasks:'에이전트 작업 상태',confirm:'이 관리 명령을 승인하시겠습니까?',rejected:'요청이 거절됐습니다. 권한 또는 최신 상태를 확인하세요.',expired:'세션이 만료됐습니다. 다시 로그인하세요.',download:'텍스트 다운로드'},
  en:{prototype:'Private text-only prototype',login:'Administrator login',workspace:'Workspace',username:'Username',password:'Password',refresh:'Refresh',logout:'Log out',topic:'Topic',create:'Create room',rooms:'Rooms',closeView:'Close view',participants:'Participants',pause:'Pause',resume:'Resume',closeRoom:'Close room',discussion:'Discussion',kind:'Message kind',question:'Question',advice:'Advice',text:'Content',send:'Send',artifacts:'Text artifacts',tasks:'Agent task status',confirm:'Approve this administrative control?',rejected:'Request rejected. Check permissions or current state.',expired:'Session expired. Log in again.',download:'Download text'}
};

export class ViewState {
  constructor(){this.epoch=0;this.selected=null;this.room=null;this.language='ko';this.busy=new Map();this.keys=new Map();}
  select(room){this.epoch++;this.selected=room;this.room=null;return {epoch:this.epoch,room};}
  close(){return this.select(null);}
  accept(ticket,data){if(ticket.epoch!==this.epoch||ticket.room!==this.selected)return false;this.room=data;return true;}
  start(kind,payload){if(this.busy.has(kind))return null;const signature=JSON.stringify([kind,this.selected,payload]);if(!this.keys.has(signature))this.keys.set(signature,crypto.randomUUID());const ticket={kind,signature,key:this.keys.get(signature),epoch:this.epoch,nonce:crypto.randomUUID()};this.busy.set(kind,ticket);return ticket;}
  finish(ticket,success){if(this.busy.get(ticket.kind)?.nonce===ticket.nonce)this.busy.delete(ticket.kind);if(success)this.keys.delete(ticket.signature);return ticket.epoch===this.epoch;}
  languageTo(language){if(!labels[language])throw new Error('invalid language');this.language=language;}
}

Object.assign(labels.ko,{adopt:'이 버전 채택',adopted:'채택됨',interventions:'관리자 개입 이력',confirmAdopt:'이 텍스트 버전을 채택하시겠습니까?',streamEnded:'실시간 갱신 시간이 끝났습니다. 새로고침으로 다시 연결할 수 있습니다.'});
Object.assign(labels.en,{adopt:'Adopt this version',adopted:'Adopted',interventions:'Administrator interventions',confirmAdopt:'Adopt this text version?',streamEnded:'Live update budget ended. Refresh to connect again.'});

Object.assign(labels.ko,{handovers:'소유자 인계 요청',acceptHandover:'관리자 인계 수락',confirmHandover:'전체 방 이력을 읽고 관리하는 membership을 수락하시겠습니까?',mode:'모드',bounded_rounds:'구조화 토론',free_discussion:'자유 토론',co_creation:'공동 창작',unsupportedUi:'GUI는 방 생성·관찰·인계·조언·pause/resume/close·text 채택을 지원합니다. agent 발언 요청/worker 실행, 고급 설정, 업로드·공개는 이 화면에서 지원하지 않습니다.'});
Object.assign(labels.en,{handovers:'Owner handover offers',acceptHandover:'Accept administrator handover',confirmHandover:'Accept membership that reads the full room history and administers this room?',mode:'Mode',bounded_rounds:'Structured discussion',free_discussion:'Free discussion',co_creation:'Co-creation',unsupportedUi:'GUI supports create/read/handover/advice, pause/resume/close and text adoption. Agent turn requests/worker execution, advanced settings, uploads and publishing are not supported in this screen.'});
