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
