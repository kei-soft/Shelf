import React, {useEffect, useRef, useState} from 'react';
import {X, Check, RefreshCw, Server, KeyRound, Database, Cpu} from 'lucide-react';
import {api, post} from './api';

export default function Settings({onClose, onSaved}) {
  const [form,setForm] = useState(null), [pending,setPending] = useState('');
  const [results,setResults] = useState({}), [notice,setNotice] = useState(''), [error,setError] = useState('');
  const [models,setModels] = useState([]);
  const dialog=useRef(null);
  useEffect(()=>{api('/settings').then(setForm).catch(e=>setError(e.message));},[]);
  useEffect(()=>{
    const previous=document.activeElement; dialog.current?.focus();
    return ()=>previous?.focus();
  },[]);
  const change=(name,value)=>{setForm(old=>({...old,[name]:value}));setResults({});setNotice('');};
  const payload=()=>Object.fromEntries(Object.entries(form).filter(([k])=>!k.endsWith('_SET')));
  const check=async target=>{
    setPending(target);setError('');
    try {
      const result=await api('/settings/check',post({target,settings:payload()}));
      setResults(old=>({...old,[target]:result}));
      if(result.models)setModels(result.models);
    }catch(e){setError(e.message);}finally{setPending('');}
  };
  const save=async e=>{
    e.preventDefault();setPending('save');setError('');setNotice('');
    try {
      const result=await api('/settings',{...post(payload()),method:'PUT'});
      setForm(result.settings);setResults({});
      setNotice(result.requeued?`저장했습니다. 자료 ${result.requeued}개를 새 검색 설정으로 다시 분석합니다.`:'저장했습니다. 다음 질문과 자료 분석부터 적용됩니다.');
      onSaved();
    }catch(e){setError(e.message);}finally{setPending('');}
  };
  const text=(name,label,type='text',hint='')=><label className="setting-field">{label}<input type={type} value={form[name]??''} autoComplete={type==='password'?'new-password':'off'} onChange={e=>change(name,e.target.value)} placeholder={form[name+'_SET']?'저장되어 있음 · 비워두면 유지':hint}/></label>;
  const button=(target,label)=><div className="check-row"><button type="button" className="check-connection" disabled={!!pending} onClick={()=>check(target)}>{pending===target?<RefreshCw className="spin" size={13}/>:<Check size={13}/>} {pending===target?'확인 중…':label}</button>{results[target]&&<span role="status" className={results[target].ok?'check-ok':'failed'}>{results[target].message}</span>}</div>;
  const keyboard=e=>{
    if(e.key==='Escape'&&!pending)onClose();
    if(e.key==='Tab'){
      const focusable=Array.from(dialog.current.querySelectorAll('button:not(:disabled),input,select,a[href]'));
      const first=focusable[0],last=focusable.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
  };
  return <div className="modal-overlay"><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1} ref={dialog} onKeyDown={keyboard}>
    <button type="button" className="close-modal" aria-label="설정 닫기" disabled={!!pending} onClick={onClose}><X size={20}/></button>
    <span className="eyebrow">WORKSPACE SETTINGS</span><h2 id="settings-title">연결 및 모델 설정</h2><p>내 컴퓨터의 모델이나 API를 연결하세요. 비밀키는 서버에만 저장됩니다.</p>
    {error&&<p className="failed" role="alert">{error}</p>}
    {!form?<p>설정을 불러오는 중…</p>:<form onSubmit={save}>
      <fieldset disabled={!!pending}>
        <section className="settings-section"><h3><Cpu size={17}/>채팅 모델</h3><div className="settings-grid">
          <label className="setting-field">제공자<select value={form.LLM_PROVIDER} onChange={e=>{change('LLM_PROVIDER',e.target.value);change('CHAT_MODEL',e.target.value==='ollama'?'qwen3:8b':'gpt-4.1-mini');}}><option value="openai">OpenAI API</option><option value="ollama">Ollama</option></select></label>
          <label className="setting-field">모델 이름<input list="installed-models" value={form.CHAT_MODEL} onChange={e=>change('CHAT_MODEL',e.target.value)} required/></label>
        </div><p className="settings-hint">Deep Agents에는 도구 호출을 지원하는 모델이 필요합니다. 모델 확인은 짧은 테스트 요청을 실행합니다.</p>{button('model','채팅 모델 확인')}</section>
        <section className="settings-section"><h3><Server size={17}/>Ollama 서버</h3>{text('OLLAMA_BASE_URL','서버 주소','url','http://127.0.0.1:11434')}
          {button('ollama','연결 및 설치 모델 확인')}<datalist id="installed-models">{models.map(m=><option key={m} value={m}/>)}</datalist>
          <label className="setting-field context-field">대화 문맥 길이<input type="number" min="4096" max="262144" value={form.OLLAMA_CONTEXT_LENGTH} onChange={e=>change('OLLAMA_CONTEXT_LENGTH',Number(e.target.value))}/></label>
          <p className="settings-hint">Ollama를 먼저 실행하고 모델을 설치해주세요. 문맥 길이가 클수록 메모리를 더 사용합니다. cloud 모델이나 외부 서버 주소를 선택하면 자료가 해당 서버로 전송됩니다.</p>
        </section>
        <section className="settings-section"><h3><KeyRound size={17}/>OpenAI</h3>{text('OPENAI_API_KEY','API 키','password','sk-…')}<p className="settings-hint">채팅과 임베딩을 모두 Ollama로 선택하면 API 키가 필요 없습니다.</p></section>
        <section className="settings-section"><h3><Cpu size={17}/>자료 검색 · 임베딩</h3><div className="settings-grid">
          <label className="setting-field">제공자<select value={form.EMBEDDING_PROVIDER} onChange={e=>{change('EMBEDDING_PROVIDER',e.target.value);change('EMBEDDING_MODEL',e.target.value==='ollama'?'bge-m3':'text-embedding-3-small');change('EMBEDDING_DIMENSIONS',e.target.value==='ollama'?1024:1536);}}><option value="openai">OpenAI API</option><option value="ollama">Ollama</option></select></label>
          <label className="setting-field">임베딩 모델<input list="installed-models" value={form.EMBEDDING_MODEL} onChange={e=>change('EMBEDDING_MODEL',e.target.value)} required/></label>
          <label className="setting-field">벡터 차원<input type="number" min="1" max="4096" value={form.EMBEDDING_DIMENSIONS} onChange={e=>change('EMBEDDING_DIMENSIONS',Number(e.target.value))}/></label>
        </div>{button('embedding','임베딩 및 차원 확인')}{results.embedding?.dimensions&&results.embedding.dimensions!==Number(form.EMBEDDING_DIMENSIONS)&&<button type="button" className="apply-dimensions" onClick={()=>change('EMBEDDING_DIMENSIONS',results.embedding.dimensions)}>{results.embedding.dimensions}차원 적용</button>}
          <p className="settings-hint">검색 모델·차원·Ollama 주소를 바꾸고 저장하면 자료를 다시 분석합니다. 원본은 유지되며 완료 전에는 해당 자료의 채팅이 잠시 제한됩니다.</p>
        </section>
        <section className="settings-section"><h3><Database size={17}/>Neo4j</h3>{text('NEO4J_URI','접속 주소','text','bolt://127.0.0.1:7687')}<div className="settings-grid">{text('NEO4J_USER','사용자')}{text('NEO4J_PASSWORD','접속 비밀번호','password')}{text('NEO4J_DATABASE','데이터베이스 이름')}</div>{button('neo4j','Neo4j 연결 확인')}
          <p className="settings-hint">서버에 설정된 비밀번호를 입력합니다. 여기서 저장해도 Neo4j 서버 자체의 비밀번호가 변경되지는 않습니다. 다른 서버/DB로 바꾸면 자료를 다시 분석합니다.</p>
          <details className="docker-help"><summary>Docker는 언제 필요한가요?</summary><p>이 프로젝트에서 Docker는 Neo4j 서버를 간편하게 실행하는 선택 사항입니다. Neo4j Desktop이나 외부 서버를 쓰면 필요 없습니다. Python 앱, Ollama, 기존 PostgreSQL 연결에는 Docker가 필요하지 않습니다.</p><p>Docker를 사용할 경우 최초 서버 비밀번호는 .env의 NEO4J_PASSWORD로 정하고 docker compose up -d로 실행한 뒤, 같은 비밀번호를 위에 입력하세요. 기존 데이터가 있는 서버는 원래 비밀번호를 사용합니다.</p></details>
        </section>
        <section className="settings-section"><h3><Database size={17}/>PostgreSQL · 선택 사항</h3>{text('SOURCE_DATABASE_URL','읽기 전용 접속 URL','password','postgresql+psycopg://user:password@localhost:5432/db')}{button('postgresql','PostgreSQL 연결 확인')}<p className="settings-hint">허용할 테이블과 조회 SQL은 database.json에서 관리합니다.</p></section>
      </fieldset>
      <p className="settings-hint">비밀키는 로컬 data/app.sqlite에 평문으로 저장되며 브라우저에는 다시 전송하지 않습니다. 개인 컴퓨터에서 사용해주세요.</p>
      {notice&&<p className="save-notice" role="status"><Check size={15}/>{notice}</p>}
      {/* The modal scrolls, so an error at the top is off-screen from the button that caused it. */}
      {error&&<p className="failed" role="alert">{error}</p>}
      <button type="submit" className="primary" disabled={!!pending}>{pending==='save'?'저장 중…':'설정 저장'}<Check size={16}/></button>
    </form>}
  </section></div>;
}
