import{e as r}from"./utils-CXyMYw6b.js";const y={downloading:"bg-emerald-900/60 text-emerald-300",queued:"bg-slate-800 text-slate-300",completed:"bg-emerald-900/60 text-emerald-400",failed:"bg-red-900/50 text-red-400",cancelled:"bg-slate-800 text-slate-500"};function q(e){if(!e)return 0;const t=Array.isArray(e.active)?e.active.length:0,s=Array.isArray(e.queued)?e.queued.length:0;return t+s}function A(e){const t=e&&e.active||[],s=e&&e.queued||[];return t.length>1?`DL ${t.length} active`:t.length===1?`DL ${Math.round(t[0].percent||0)}%`:s.length?`${s.length} queued`:"Idle"}function $(e){if(!e)return"";const t=e.current_title||String(e.url||"").replace(/^https?:\/\//,"")||e.id||"";return String(t)}function w(e,{retryable:t=!1}={}){if(!e)return"";const s=e.status==="downloading",d=e.status==="queued",c=e.status==="completed",o=e.status==="failed",u=e.status==="cancelled",i=s||d,f=t&&(o||u),p=$(e),a=Math.round(e.percent||0),l=e.downloader?String(e.downloader):"",x=e.playlist_index&&e.playlist_count?`${e.playlist_index}/${e.playlist_count}`:"",h=a>0?a+"%":e.playlist_index?e.playlist_index+" done":"…",n=[];s?(x?n.push(x):e.playlist_index&&n.push(`${e.playlist_index} done`),e.speed&&n.push(e.speed),e.eta&&n.push(`ETA ${e.eta}`),l&&n.push(l)):c&&e.imported_count?(n.push(`${e.imported_count} file${e.imported_count>1?"s":""}`),l&&n.push(l)):o&&e.error?(n.push(String(e.error).slice(0,80)),l&&n.push(l)):u&&l&&n.push(l);const g=y[e.status]||"bg-slate-800 text-slate-400",v=i?`<button data-urldl-cancel="${r(e.id)}" class="text-[10px] px-2 py-0.5 bg-red-900/30 text-red-400 hover:bg-red-900/50 hover:text-red-300 rounded border border-red-900/50 transition shrink-0" title="Cancel">✕</button>`:"",b=f?`<button data-urldl-retry="${r(e.id)}" class="text-[10px] px-2 py-0.5 bg-emerald-900/30 text-emerald-300 hover:bg-emerald-900/50 hover:text-emerald-200 rounded border border-emerald-800/50 transition shrink-0" title="Retry download">↻</button>`:"";return`
        <div class="bg-slate-900 rounded-lg border border-slate-800/80 p-2 flex flex-col gap-1.5">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-[9px] uppercase px-1.5 py-0.5 rounded ${g} font-mono shrink-0">${r(e.status)}</span>
            <span class="truncate text-[11px] text-slate-300 flex-1 min-w-0" title="${r(p)}">${r(p)}</span>
            ${b}
            ${v}
          </div>
          ${s||d?`
          <div class="flex items-center gap-2">
            <div class="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden">
              <div class="h-full bg-emerald-500 transition-all duration-300${s&&a<=0?" animate-pulse opacity-60":""}" style="width:${s?a>0?a:100:0}%"></div>
            </div>
            <span class="text-[10px] text-emerald-400 font-mono shrink-0">${s?h:"queued"}</span>
          </div>`:""}
          ${n.length?`<div class="text-[10px] text-slate-500 font-mono truncate">${r(n.join(" · "))}</div>`:""}
        </div>`}function D(e,{historyLimit:t=20,retryable:s=!1}={}){const d=e&&e.active||[],c=e&&e.queued||[],o=e&&e.history||[],u=t>0?o.slice(-t):o;return[...d,...c,...u.slice().reverse()].map(i=>w(i,{retryable:s})).join("")}export{q as a,w as b,D as c,A as u};
