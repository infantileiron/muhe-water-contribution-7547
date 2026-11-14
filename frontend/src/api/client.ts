export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
 const token=localStorage.getItem('mix-token');
 const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...options.headers}});
 if(!response.ok){let detail:unknown='请求失败';try{detail=(await response.json()).detail}catch{};
  const message=typeof detail==='string'?detail:JSON.stringify(detail);
  const error=new Error(message) as Error&{detail?:unknown};
  if(typeof detail==='object'&&detail!==null)error.detail=detail;
  throw error}
 return response.json();
}
export const post=<T>(path:string,data:unknown)=>request<T>(path,{method:'POST',body:JSON.stringify(data)});
export async function downloadReport(id:string){const r=await fetch('/api/reports/'+id+'/csv',{headers:{Authorization:'Bearer '+localStorage.getItem('mix-token')}});if(!r.ok)throw new Error('下载失败');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='muhe-water.csv';a.click();URL.revokeObjectURL(url)}
