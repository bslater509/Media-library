import{e as l}from"./utils-CXyMYw6b.js";const w=/\.zip$/i;function $({title:v="Choose a folder",subtitle:f="",confirmLabel:g="Use this folder",rowIcon:y="",onSelect:m=null}={}){let e=null,o=null,a=null,i=null,x=null,r="",d=!0,c=!1;function u(){o&&(o.textContent=r||"/",o.title=r||"/",i.disabled=d,i.className=d?"px-2 py-1 rounded-lg bg-slate-900 text-slate-600 border border-slate-800 text-[11px] font-semibold shrink-0 cursor-not-allowed":"px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold shrink-0 transition",x.disabled=c||!r)}function k(t){const p=(t||[]).filter(s=>s&&!(s.is_zip||w.test(s.path||"")));if(!p.length){a.innerHTML='<div class="px-2 py-6 text-center text-xs text-slate-500">No subfolders here — use this one, or go up.</div>';return}a.innerHTML=p.map(s=>`
      <button type="button" data-picker-dir="${l(s.path)}" class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 hover:bg-slate-800 text-left transition min-w-0">
        ${y}
        <span class="truncate flex-1">${l(s.name||s.path)}</span>
        <span class="text-slate-500 shrink-0">›</span>
      </button>`).join(""),a.querySelectorAll("[data-picker-dir]").forEach(s=>{s.addEventListener("click",()=>b(s.dataset.pickerDir))})}async function b(t){c=!0,u(),a.innerHTML='<div class="px-2 py-6 text-center text-xs text-slate-500">Loading…</div>';try{const s=await(await fetch(`/api/backup/folders?path=${encodeURIComponent(t||"")}`)).json();r=s.current_path||t||"",d=!!s.is_root,c=!1,u(),k(s.subfolders)}catch{c=!1,u(),a.innerHTML='<div class="px-2 py-6 text-center text-xs text-amber-300">Could not read the folder list.</div>'}}function n(){e&&(e.classList.add("hidden"),e.classList.remove("flex"),document.removeEventListener("keydown",h))}function h(t){t.key==="Escape"&&e&&!e.classList.contains("hidden")&&(t.stopPropagation(),n())}function L(){e||(e=document.createElement("div"),e.className="ml-sheet fixed inset-0 bg-slate-950/85 backdrop-blur-sm z-[96] hidden items-center justify-center p-3 sm:p-6",e.innerHTML=`
      <div class="ml-sheet-card bg-slate-900 border border-slate-700/80 rounded-2xl w-full max-w-lg max-h-[88vh] flex flex-col shadow-2xl overflow-hidden">
        <div class="ml-sheet-grabber" data-sheet-grabber></div>
        <div class="px-4 py-3 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between gap-3 shrink-0">
          <div class="min-w-0">
            <h3 class="text-xs sm:text-sm font-bold text-white truncate">${l(v)}</h3>
            ${f?`<p class="text-[10px] text-slate-400 font-mono truncate">${l(f)}</p>`:""}
          </div>
          <button type="button" data-picker-close class="w-8 h-8 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white flex items-center justify-center text-sm transition shrink-0" title="Close">✕</button>
        </div>
        <div class="px-3 pt-3 flex items-center gap-1.5 shrink-0">
          <button type="button" data-picker-up class="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold shrink-0 transition">⬆ Up</button>
          <span data-picker-path class="flex-1 truncate text-[11px] font-mono text-slate-300 bg-slate-950 px-2 py-1 rounded-lg border border-slate-800">/</span>
        </div>
        <div data-picker-list class="flex-1 min-h-0 overflow-y-auto px-3 py-3 flex flex-col gap-1"></div>
        <div class="px-4 py-3 bg-slate-950/80 border-t border-slate-800 flex items-center justify-end gap-2 shrink-0" style="padding-bottom: max(0.75rem, var(--sab, env(safe-area-inset-bottom, 0px)));">
          <button type="button" data-picker-cancel class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold transition">Cancel</button>
          <button type="button" data-picker-confirm class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold transition disabled:opacity-40 disabled:pointer-events-none">${l(g)}</button>
        </div>
      </div>`,document.body.appendChild(e),o=e.querySelector("[data-picker-path]"),a=e.querySelector("[data-picker-list]"),i=e.querySelector("[data-picker-up]"),x=e.querySelector("[data-picker-confirm]"),e.querySelector("[data-picker-close]").addEventListener("click",n),e.querySelector("[data-picker-cancel]").addEventListener("click",n),e.addEventListener("click",t=>{t.target===e&&n()}),i.addEventListener("click",()=>{d||!r||b(r.substring(0,r.lastIndexOf("/"))||"")}),x.addEventListener("click",()=>{const t=r;n(),t&&m&&m(t)}))}return{open(t=""){L(),e.classList.remove("hidden"),e.classList.add("flex"),document.addEventListener("keydown",h),b(t||"")},close:n}}export{$ as c};
