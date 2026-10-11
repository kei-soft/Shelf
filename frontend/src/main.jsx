import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import cytoscape from 'cytoscape';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {BookOpen, Plus, ArrowUp, ArrowUpRight, FileText, Globe, Database, Network, MessageSquare, RefreshCw, Trash2, X, Check, PanelRightClose, Settings2, Play, Square} from 'lucide-react';
import './style.css';
import './scroll-layout.css';
import {api, post} from './api';
import Settings from './Settings';

const statuses = {draft:'분석 대기', queued:'분석 대기열', processing:'분석 중', cancelling:'중단하는 중', cancelled:'분석 중단됨', ready:'준비 완료', failed:'처리 실패'};
const RUNNING = ['queued','processing','cancelling'];
const SourceIcon = ({kind, ...props}) => kind === 'url' ? <Globe {...props}/> : kind === 'database' ? <Database {...props}/> : <FileText {...props}/>;

function Graph({edges, onEvidence, onExpand}) {
  const ref = useRef(null);
  useEffect(() => {
    const nodes = new Map();
    edges.forEach(e => { nodes.set(e.subject_id, e.subject); nodes.set(e.object_id, e.object); });
    const cy = cytoscape({container:ref.current,
      elements:[...Array.from(nodes, ([id,label]) => ({data:{id,label}})), ...edges.map(e => ({data:{id:e.id, source:e.subject_id, target:e.object_id, label:e.predicate, evidence:e}}))],
      style:[{selector:'node',style:{'background-color':'#497061', 'width':24,'height':24,label:'data(label)','font-size':11,'color':'#303d36','text-valign':'bottom','text-margin-y':9,'text-wrap':'wrap','text-max-width':90}},
      {selector:'edge',style:{'width':1.2,'line-color':'#bdc8bf','target-arrow-color':'#bdc8bf','target-arrow-shape':'triangle','curve-style':'bezier',label:'data(label)','font-size':8,'color':'#777','text-rotation':'autorotate','text-background-color':'#f7f8f4','text-background-opacity':1,'text-background-padding':2}}],
      layout:{name:'cose',animate:false,padding:35,nodeRepulsion:()=>10000}, minZoom:0.3, maxZoom:2});
    cy.on('tap','edge', e => onEvidence(e.target.data('evidence')));
    cy.on('tap','node', e => onExpand(e.target.id()));
    const observer = new ResizeObserver(() => {cy.resize(); cy.fit(undefined,35);});
    observer.observe(ref.current);
    return () => {observer.disconnect();cy.destroy();};
  }, [edges]);
  return <div className="graph" ref={ref}/>;
}

function App() {
  const [settingsOpen,setSettingsOpen] = useState(()=>new URLSearchParams(window.location.search).has('settings'));
  const [sources,setSources] = useState([]), [selected,setSelected] = useState([]);
  const [workerState,setWorkerState] = useState(null);
  const [health,setHealth] = useState(null), [threads,setThreads] = useState([]);
  const [thread,setThread] = useState(null), [messages,setMessages] = useState([]);
  const [question,setQuestion] = useState(''), [busy,setBusy] = useState(false);
  const [error,setError] = useState(''), [modal,setModal] = useState(false), [tab,setTab] = useState('file');
  const [url,setUrl] = useState(''), [uploading,setUploading] = useState(false);
  const [evidence,setEvidence] = useState([]), [edges,setEdges] = useState([]), [detail,setDetail] = useState(null);
  const [rightTab,setRightTab] = useState('sources');
  const conversationRef = useRef(null), file = useRef(null), followLatest = useRef(true);
  const refresh = async () => {
    try {setSources(await api('/sources')); setThreads(await api('/threads'));setWorkerState(await api('/worker'));} catch(e) {setError(e.message);}
  };
  useEffect(() => {
    refresh(); api('/health').then(setHealth).catch(e => setError(e.message));
    const timer = setInterval(refresh,5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const pane = conversationRef.current;
    if(pane && followLatest.current) pane.scrollTo({top:pane.scrollHeight,behavior:'instant'});
  }, [messages,busy]);
  const newChat = () => {followLatest.current=true;setError('');setThread(null);setMessages([]);setEvidence([]);setEdges([]);setDetail(null);};
  const toggle = id => {if(thread) newChat(); setSelected(old => old.includes(id) ? old.filter(x=>x!==id) : [...old,id]);};
  const loadGraph = async (items=evidence, entity_id=null) => {
    try {
      if(!selected.length){setEdges([]);return;}
      const docIds = items.filter(e=>e.kind!=='database').map(e=>e.id);
      const result = await api('/graph',post({source_ids:selected,evidence_ids:entity_id ? null : docIds,entity_id}));
      setEdges(old => entity_id ? [...new Map([...old,...result].map(e=>[e.id,e])).values()] : result);
    } catch(e) {setError(e.message);}
  };
  const showEvidence = item => {setDetail(item);setRightTab('sources');};
  const submit = async e => {
    e?.preventDefault();
    if(!question.trim() || busy) return;
    followLatest.current=true;setError('');setBusy(true);setMessages(old=>[...old,{role:'user',content:question}]);
    const currentQuestion=question;setQuestion('');
    try {
      const response = await fetch('/api/chat',post({question:currentQuestion,source_ids:selected,thread_id:thread}));
      if(!response.ok) {const r=await response.json();throw new Error(typeof r.detail==='string'?r.detail:'질문을 처리하지 못했습니다.');}
      const reader=response.body.getReader(), decoder=new TextDecoder();let buffer='';let received=false;
      while(true) {
        const {value,done}=await reader.read();
        if(done)break;
        buffer+=decoder.decode(value,{stream:true});
        let boundary;
        while((boundary=buffer.indexOf('\n\n'))>=0) {
          const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          const kind=frame.match(/^event: (.+)$/m)?.[1], raw=frame.match(/^data: (.+)$/m)?.[1];
          if(!raw)continue;const data=JSON.parse(raw);
          if(kind==='status')setThread(data.thread_id);
          if(kind==='error')throw new Error(data.message);
          if(kind==='answer') {received=true;setThread(data.thread_id);setMessages(old=>[...old,{role:'assistant',content:data.text,evidence:data.evidence}]);setEvidence(data.evidence);setDetail(null);await loadGraph(data.evidence);}
        }
      }
      if(!received)throw new Error('연결이 끊겼습니다. 대화 목록을 다시 열어 답변 저장 여부를 확인해주세요.');
      await refresh();
    } catch(e) {setError(e.message);setQuestion(currentQuestion);} finally {setBusy(false);}
  };
  const add = async () => {
    setUploading(true);setError('');
    try {
      if(tab==='file') {
        if(!file.current?.files[0])throw new Error('파일을 선택해주세요.');
        const data=new FormData();data.append('file',file.current.files[0]);
        await api('/sources/upload',{method:'POST',body:data});
      } else if(tab==='url') await api('/sources/url',post({url}));
      else await api('/sources/database',post({}));
      setModal(false);setUrl('');await refresh();
    } catch(e) {setError(e.message);} finally {setUploading(false);}
  };
  const openThread=async t=>{
    followLatest.current=true;
    try {setMessages(await api('/threads/'+t.id));setThread(t.id);setSelected(JSON.parse(t.scope));setEvidence([]);setEdges([]);setDetail(null);}catch(e){setError(e.message);}
  };
  const mutate=async (s,action)=>{
    if(action==='delete' && !window.confirm(`“${s.title}” 자료와 Neo4j 검색 인덱스를 모두 삭제할까요?`))return;
    setError('');
    try {
      if(action==='delete') await api('/sources/'+s.id,{method:'DELETE'});
      else await api('/sources/'+s.id+'/'+action,{method:'POST'});
      if(action==='delete'){setSelected(old=>old.filter(id=>id!==s.id));newChat();}
      await refresh();
    }catch(e){setError(e.message);}
  };
  return <div className="app">
    <aside className="sidebar">
      <a className="brand" href="/"><span className="brand-icon"><BookOpen size={22}/></span>Shelf<span className="beta">RAG</span></a>
      <button className="new-chat" onClick={newChat} disabled={busy}><Plus size={16}/>새 대화<span>↗</span></button>
      <div className="section-label">내 자료 <span>{sources.length}</span><button aria-label="자료 추가" onClick={()=>setModal(true)}><Plus size={16}/></button></div>
      <div className="source-list">{sources.some(s=>s.status==='queued')&&workerState&&!workerState.running&&<p className="worker-notice" role="status">{workerState.error||'자료 분석기를 시작하고 있습니다…'}</p>}{sources.length===0?<div className="empty-library"><BookOpen size={26}/><p>아직 비어 있는 서재</p><small>책, 웹페이지, DB를 추가해<br/>첫 대화를 시작하세요.</small></div>:sources.map(s=><div className={'source-item '+(selected.includes(s.id)?'selected':'')} key={s.id}>
        <button className="source-select" title={s.active_version?'대화에 사용할 자료 선택':s.status==='draft'?'분석 버튼(▶)을 눌러 분석을 시작하세요.':'자료 분석이 끝나면 선택할 수 있습니다.'} disabled={!s.active_version||busy} onClick={()=>toggle(s.id)}><span className={'checkbox '+(selected.includes(s.id)?'checked':'')}>{selected.includes(s.id)&&<Check size={10}/>}</span><SourceIcon kind={s.kind} size={16}/><span><strong title={s.title}>{s.title}</strong><small className={s.status==='failed'?'failed':''}>{s.status==='processing'?(s.progress||statuses[s.status]):statuses[s.status]}</small></span></button>
        {s.error&&<small className="source-error">{s.error}</small>}
        <div className="source-actions">{RUNNING.includes(s.status)
          ? <button title={s.status==='cancelling'?'중단하는 중…':'분석 중단'} disabled={busy||s.status==='cancelling'} onClick={()=>mutate(s,'cancel')}><Square size={12}/></button>
          : <button title={s.active_version?'다시 분석':'분석 시작'} disabled={busy} onClick={()=>mutate(s,'analyze')}>{s.active_version?<RefreshCw size={12}/>:<Play size={12}/>}</button>}<button title="자료 삭제" disabled={busy||['processing','cancelling'].includes(s.status)} onClick={()=>mutate(s,'delete')}><Trash2 size={12}/></button></div>
      </div>)}</div>
      <button className="add-source" onClick={()=>setModal(true)}><Plus size={15}/>자료 추가</button>
      <div className="section-label history-label">최근 대화</div><div className="history">{threads.map(t=><button disabled={busy} key={t.id} onClick={()=>openThread(t)} className={thread===t.id?'active':''}><MessageSquare size={14}/><span>{t.title}</span></button>)}</div>
      <button className="settings-launch" onClick={()=>setSettingsOpen(true)}><Settings2 size={16}/>연결 및 모델 설정</button><div className="sidebar-footer"><span className={'status-dot '+(health?.neo4j&&health?.model_configured?'connected':'')}/>{health?.neo4j&&health?.model_configured?'모델 설정 · Neo4j 연결됨':'연결 설정 필요'}<small>{workerState?.running?'자료 분석기 실행 중':'자료 분석기 확인 중'}</small><small>개인 워크스페이스</small></div>
    </aside>
    <main className="main">
      <header><div><span className="breadcrumb">워크스페이스</span><span className="slash">/</span><strong>자료와 대화</strong></div><span className="scope-pill"><span className="status-dot connected"/>{selected.length?`${selected.length}개 자료 선택`:'일반 대화'}</span></header>
      {error&&<div className="error" role="alert">{error}<button aria-label="알림 닫기" onClick={()=>setError('')}><X size={16}/></button></div>}
      <div className="conversation" ref={conversationRef} tabIndex={0} role="log" aria-label="대화 내용" onScroll={e=>{const p=e.currentTarget;followLatest.current=p.scrollHeight-p.scrollTop-p.clientHeight<80;}}>
        {!messages.length&&<div className="welcome"><span className="eyebrow">YOUR KNOWLEDGE, CONNECTED</span><h1>읽어 둔 지식이,<br/><em>대화가 되는 곳.</em></h1><p>책의 한 문장부터 데이터 속 숫자까지.<br/>내 자료를 연결하고, 근거 있는 답을 찾아보세요.</p><div className="suggestions">{['선택한 자료의 핵심 내용을 요약해줘','자료들 사이에 공통으로 등장하는 개념은?','이 자료에서 중요한 관계를 설명해줘'].map((text,i)=><button key={text} onClick={()=>setQuestion(text)}><span className="suggestion-number">0{i+1}</span><span>{text}</span><ArrowUpRight size={17}/></button>)}</div><div className="welcome-note"><BookOpen size={14}/>바로 대화하거나, 자료를 선택해 근거 있는 답을 찾아보세요</div></div>}
        {messages.map((m,i)=><article className={'message '+m.role} key={i}><div className="message-label">{m.role==='user'?'YOU':<><BookOpen size={15}/>SHELF</>}</div>{m.role==='user'
      ? <div className="message-content plain">{m.content}</div>
      : <div className="message-content markdown"><Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown></div>}{m.evidence?.length>0&&<div className="citation-chips">{m.evidence.map(c=><button key={c.id} onClick={()=>{setEvidence(m.evidence);showEvidence(c);loadGraph(m.evidence);}}><span>{c.number}</span>{c.title}</button>)}</div>}</article>)}
        {busy&&<div className="thinking"><span/>{selected.length?'자료를 읽고 근거를 확인하고 있어요…':'답변을 작성하고 있어요…'}</div>}
      </div>
      <div className="composer-wrap"><form className="composer" onSubmit={submit}><textarea aria-label="자료에 질문하기" placeholder={selected.length?'선택한 자료에 질문해보세요…':'무엇이든 질문해보세요…'} value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}} rows={2}/><div className="composer-bottom"><span><BookOpen size={14}/>{selected.length?`${selected.length}개 자료에서 답변`:'일반 대화 · 자료 미선택'}</span><button aria-label="질문 보내기" disabled={busy||!question.trim()}><ArrowUp size={19}/></button></div></form><p className="composer-note">답변의 출처를 확인하세요. 관계도는 자료에서 추출한 미검토 관계를 표시합니다.</p></div>
    </main>
    <aside className="evidence-panel"><div className="panel-heading"><span>대화의 근거</span><PanelRightClose size={16}/></div><div className="tabs"><button className={rightTab==='sources'?'active':''} onClick={()=>setRightTab('sources')}><FileText size={14}/>출처 <span>{evidence.length}</span></button><button className={rightTab==='graph'?'active':''} onClick={()=>setRightTab('graph')}><Network size={14}/>관계도</button></div>
      {rightTab==='graph'?<><div className="graph-help">노드로 연결을 펼치고, 선으로 근거를 확인하세요.</div>{edges.length?<Graph edges={edges} onEvidence={showEvidence} onExpand={id=>loadGraph(evidence,id)}/>:<div className="panel-empty"><Network size={32}/><h3>연결을 발견해보세요</h3><p>답변에 사용된 개체와 관계가<br/>이곳에 표시됩니다.</p></div>}<div className="graph-legend"><span/>원문에서 추출된 관계 · 미검토</div></>:<div className="evidence-scroll">{detail?<div className="evidence-detail"><button className="back" onClick={()=>setDetail(null)}>← 출처 목록</button><span className="eyebrow">SOURCE PASSAGE</span><h3>{detail.title}</h3><small>{detail.location}</small><p>{detail.text}</p>{detail.source_id&&detail.kind!=='url'&&detail.kind!=='database_schema'&&<a href={'/api/sources/'+detail.source_id+'/original'} target="_blank" rel="noreferrer">원본 다운로드 <ArrowUpRight size={14}/></a>}{detail.location?.startsWith('https://')&&<a href={detail.location} target="_blank" rel="noreferrer">웹 원문 열기 <ArrowUpRight size={14}/></a>}</div>:evidence.length?evidence.map(c=><button className="evidence-card" key={c.id} onClick={()=>showEvidence(c)}><span className="citation-number">{c.number}</span><h3>{c.title}</h3><small>{c.location}</small><p>{c.text.slice(0,180)}…</p><span className="read-more">근거 읽기 <ArrowUpRight size={13}/></span></button>):<div className="panel-empty"><FileText size={30}/><h3>답변에는 이유가 있습니다</h3><p>대화를 시작하면 답변의 근거가 된<br/>페이지와 문단을 모아 보여드려요.</p><div className="sample-lines"><i/><i/><i/></div></div>}</div>}
      <div className="panel-footer"><span className="status-dot connected"/>SOURCE-GROUNDED ANSWERS</div>
    </aside>
    {settingsOpen&&<Settings onClose={()=>setSettingsOpen(false)} onSaved={()=>{refresh();api('/health').then(setHealth).catch(()=>{});}}/>}
    {modal&&<div className="modal-overlay" onClick={()=>!uploading&&setModal(false)}><section role="dialog" aria-modal="true" aria-labelledby="add-title" className="modal" onClick={e=>e.stopPropagation()}><button className="close-modal" aria-label="닫기" disabled={uploading} onClick={()=>setModal(false)}><X size={20}/></button><span className="eyebrow">BUILD YOUR LIBRARY</span><h2 id="add-title">서재에 자료 추가</h2><p>등록한 뒤 목록의 분석 버튼(▶)을 눌러 분석을 시작합니다.</p><div className="modal-tabs">{[['file','책 / 파일',FileText],['url','웹사이트',Globe],['database','PostgreSQL',Database]].map(([id,label,Icon])=><button key={id} className={tab===id?'active':''} onClick={()=>setTab(id)}><Icon size={16}/>{label}</button>)}</div>{tab==='file'?<label className="file-drop"><BookOpen size={30}/><strong>책이나 문서를 선택하세요</strong><small>PDF · EPUB · TXT · MD · SQL / 최대 30MB</small><input ref={file} type="file" accept=".pdf,.epub,.txt,.md,.sql"/></label>:tab==='url'?<label className="url-label">웹페이지 주소<input type="url" placeholder="https://example.com/article" value={url} onChange={e=>setUrl(e.target.value)}/><small>입력한 페이지의 본문을 수집합니다.</small></label>:<div className="database-note"><Database size={28}/><h3>PostgreSQL 연결</h3><p>서버에 설정한 DB의 허용 테이블 구조를 등록합니다. 최신 데이터는 승인된 읽기 전용 조회로 확인합니다.</p></div>}{error&&<p role="alert" className="failed">{error}</p>}<button className="primary" disabled={uploading} onClick={add}>{uploading?'등록 중…':'자료 등록'}<Plus size={16}/></button></section></div>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
