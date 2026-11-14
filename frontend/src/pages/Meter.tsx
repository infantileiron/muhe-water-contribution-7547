import {useEffect,useMemo,useState} from 'react';
import {PageHeading} from '../components/common/PageHeading';
import {Notice} from '../components/common/Notice';
import {Empty} from '../components/common/Empty';
import {createBatch,setDetail as fetchSetDetail} from '../api/meter';
import {useAsync} from '../hooks/useAsync';
import {number,percent,signed,date} from '../utils/format';
import type {MeterSetDetail} from '../types/domain';
import type {Workspace} from '../stores/workspace';

interface LocatedError{code:string;message:string;zone?:string;period?:string;ref?:string}

const EXAMPLE_READINGS=JSON.stringify([
 {zone:'研发东区',read_at:'2026-08-01T00:00:00Z',cumulative:1200},
 {zone:'研发东区',read_at:'2026-09-01T00:00:00Z',cumulative:1290},
 {zone:'研发东区',read_at:'2026-10-01T00:00:00Z',cumulative:1404},
],null,1);
const EXAMPLE_DAYS=JSON.stringify([
 {zone:'研发东区',period:'2026-08',scale:2500},
 {zone:'研发东区',period:'2026-09',scale:2480},
],null,1);

export function Meter({data,canWrite,refresh,go}:{data:Workspace;canWrite:boolean;refresh:()=>Promise<void>;go:(p:string)=>void}){
 const [name,setName]=useState('');
 const [key,setKey]=useState('');
 const [source,setSource]=useState('meter_readings.json');
 const [readingsText,setReadingsText]=useState('');
 const [daysText,setDaysText]=useState('');
 const [selectedSet,setSelectedSet]=useState('');
 const [detail,setDetail]=useState<MeterSetDetail|null>(null);
 const [located,setLocated]=useState<LocatedError[]>([]);
 const task=useAsync();

 const sets=useMemo(()=>data.meterSets,[data]);

 async function openDetail(id:string){
  setSelectedSet(id);
  try{setDetail(await fetchSetDetail(id));setLocated([])}catch(e){setDetail(null)}
 }

 async function submit(e:React.FormEvent){
  e.preventDefault();
  setLocated([]);
  await task.execute(async()=>{
   let readings:unknown,days:unknown;
   try{readings=JSON.parse(readingsText)}catch{throw new Error('抄表读数不是合法 JSON')}
   try{days=JSON.parse(daysText)}catch{throw new Error('月度人日不是合法 JSON')}
   try{
    const res=await createBatch({idempotency_key:key,name,source,readings,person_days:days});
    setName('');setKey('');setReadingsText('');setDaysText('');
    await refresh();
    await openDetail(res.observation_set.id);
   }catch(err){
    const e=err as {detail?:{message?:string;errors?:LocatedError[]}}&Error;
    if(e?.detail?.errors){setLocated(e.detail.errors);throw new Error(e.detail.message||'抄表批次整批拒绝')}
    throw err;
   }
  });
 }

 const activeId=selectedSet&&sets.some(s=>s.id===selectedSet)?selectedSet:sets[0]?.id||'';
 const shown=detail&&detail.id===activeId?detail:null;

 useEffect(()=>{
  if(activeId&&detail?.id!==activeId)openDetail(activeId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[activeId]);

 return <>
  <PageHeading label="07 / METER BATCHES" title="累计抄表，不按月硬减。">按批次导入分区抄表时刻、累计量和月度人日。<br/>相邻读数间水量按实际时长分摊到月，再接入双方法分解。</PageHeading>

  {canWrite&&<section className="paper">
   <h2>导入抄表批次</h2>
   <p className="caption">整批校验：同刻冲突、读数倒退、月界缺包围读数、人日与分摊月份不一致都会被定位并整批拒绝，不落任何部分数据。同批并发/重试凭幂等键只生成一份观测集；补点请用新幂等键另建新集。</p>
   <form className="grid-form" onSubmit={submit}>
    <label>批次名称<input required minLength={2} value={name} onChange={e=>setName(e.target.value)} placeholder="例：2026Q3 分区抄表"/></label>
    <label>幂等键（同批重试保持一致）<input required minLength={8} value={key} onChange={e=>setKey(e.target.value)} placeholder="例：meter-2026q3-001"/></label>
    <label className="wide">来源文件名<input value={source} onChange={e=>setSource(e.target.value)}/></label>
    <label className="wide">抄表读数 JSON（zone / read_at 含时区 / cumulative 累计量）
     <textarea rows={5} required value={readingsText} onChange={e=>setReadingsText(e.target.value)} placeholder={EXAMPLE_READINGS}/>
    </label>
    <label className="wide">月度人日 JSON（zone / period YYYY-MM / scale 有效作业人日）
     <textarea rows={4} required value={daysText} onChange={e=>setDaysText(e.target.value)} placeholder={EXAMPLE_DAYS}/>
    </label>
    {task.error&&<Notice error>{task.error}</Notice>}
    {located.length>0&&<div className="validation-box" role="alert">
     <b>整批拒绝 · 定位到 {located.length} 处问题：</b>
     <ul>{located.map((er,i)=><li key={i}><code>{er.code}</code>
      {er.zone&&<span>分区「{er.zone}」</span>}{er.period&&<span>{er.period}</span>}{er.ref&&<span>{er.ref}</span>}
      <em>{er.message}</em></li>)}</ul>
    </div>}
    <button disabled={task.busy}>校验并生成分摊观测集</button>
   </form>
  </section>}

  <section className="paper">
   <div className="section-heading">
    <h2>抄表观测集（不可变版本）</h2>
    <div className="filters"><select aria-label="选择抄表观测集" value={activeId} onChange={e=>openDetail(e.target.value)}>
     {sets.map(s=><option key={s.id} value={s.id}>{s.batch_name||s.id} · v{s.version}</option>)}
    </select></div>
   </div>
   {!sets.length?<Empty>还没有抄表批次。导入含包围读数的累计抄表，系统会按时长分摊成月度观测。</Empty>:
    shown?<SetTrace detail={shown} go={go}/>:<p className="caption">选择观测集查看来源区间、分摊明细与对账。</p>}
  </section>
 </>;
}

function SetTrace({detail,go}:{detail:MeterSetDetail;go:(p:string)=>void}){
 const rec=detail.reconciliation;
 const closed=Math.abs(rec.residual)<=rec.tolerance;
 return <>
  <div className="data-caption">
   <b>{detail.batch.name}</b>
   <span>{detail.source||detail.batch.source}</span>
   <span>{detail.zone_count} 分区 × {detail.month_count} 月 = {detail.monthly.length} 条月度观测</span>
   <span>v{detail.version}</span>
  </div>

  <section className="recon-grid">
   <div><span>累计读数差合计</span><strong>{number(rec.meter_total,3)}</strong><small>m³</small></div>
   <div><span>月度分摊合计</span><strong>{number(rec.monthly_total,3)}</strong><small>m³</small></div>
   <div className={closed?'ok':'bad'}><span>对账残差</span><strong>{signed(rec.residual)}</strong><small>{closed?'闭合':'超出容差 '+rec.tolerance}</small></div>
  </section>

  <h3>月度总量对账</h3>
  <div className="table-scroll"><table>
   <thead><tr><th>月份</th><th>分区数</th><th>分摊用水合计 / m³</th><th>人日合计</th></tr></thead>
   <tbody>{detail.period_reconciliation.map(p=><tr key={p.period}>
    <td>{p.period}</td><td>{p.zones}</td><td>{number(p.water,3)}</td><td>{number(p.scale,0)}</td>
   </tr>)}</tbody>
  </table></div>

  <h3>来源区间 → 月度分摊明细</h3>
  <div className="table-scroll"><table>
   <thead><tr><th>分区</th><th>区间（相邻读数）</th><th>累计差 Δ / m³</th><th>分摊月份</th><th>时长占比</th><th>分摊水量 / m³</th><th>当月人日</th></tr></thead>
   <tbody>{detail.segments.flatMap(seg=>seg.allocations.map((a,i)=><tr key={seg.zone+seg.reading_from.read_at+a.period}>
    {i===0&&<td rowSpan={seg.allocations.length}>{seg.zone}</td>}
    {i===0&&<td rowSpan={seg.allocations.length} className="interval-cell">{shortTime(seg.reading_from.read_at)}<br/>→ {shortTime(seg.reading_to.read_at)}<br/><small>Δ {number(seg.delta,2)}</small></td>}
    {i===0&&<td rowSpan={seg.allocations.length}>{number(seg.delta,3)}</td>}
    <td>{a.period}</td>
    <td>{percent(a.time_share)}</td>
    <td>{number(a.water,3)}</td>
    <td>{a.scale==null?'—':number(a.scale,0)}</td>
   </tr>))}</tbody>
  </table></div>

  <h3>逐区分摊对账</h3>
  <div className="table-scroll"><table>
   <thead><tr><th>分区</th><th>累计读数差 / m³</th><th>月度合计 / m³</th><th>残差 / m³</th></tr></thead>
   <tbody>{rec.by_zone.map(z=><tr key={z.zone}>
    <td>{z.zone}</td><td>{number(z.meter_total,3)}</td><td>{number(z.monthly_total,3)}</td>
    <td className={Math.abs(z.residual)<=rec.tolerance?'ok':'bad'}>{signed(z.residual)}</td>
   </tr>)}</tbody>
  </table></div>

  <div className="trace-footer">
   <small>观测校验码 {detail.checksum.slice(0,24)}… · 生成于 {date(detail.created_at)}</small>
   <button className="secondary" onClick={()=>go('comparisons')}>用此观测集建立两期对照　→</button>
  </div>
 </>;
}

function shortTime(iso:string){return iso.replace('T',' ').replace('Z',' Z').slice(0,18)}
