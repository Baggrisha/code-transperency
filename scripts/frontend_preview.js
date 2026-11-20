/* Injected only by export_frontend.py. Never loaded by the server application.
 * This adapter demonstrates navigation and review decisions on synthetic data.
 * It does not extract files, run comparisons, or create a fabricated PDF.
 */
(() => {
  const seed = structuredClone(globalThis.KONTROL_PREVIEW_SEED);
  const cases = new Map([[seed.dossier.id, seed.dossier]]);
  const runs = new Map([[seed.run.id, seed.run]]);
  const events = new Map([[seed.dossier.id, seed.events]]);
  let sequence = 0;
  const now = () => new Date().toISOString();
  const copy = value => structuredClone(value);
  const failure = (message,status = 422) => Object.assign(new Error(message),{status});
  const event = (id,action,payload) => {
    const entries = events.get(id) || [];
    entries.push({id:++sequence,action,payload,created_at:now()});
    events.set(id,entries);
  };
  const summary = dossier => {
    const run = runs.get(dossier.runs[0]?.id), latest = run?.latest_reviews || {};
    const signalCount = run?.result.signals.length || 0;
    const reviewed = (run?.result.signals || []).filter(signal => latest[signal.id] && latest[signal.id].status !== 'needs_review').length;
    return {id:dossier.id,title:dossier.title,description:dossier.description,contract_number:dossier.contract_number,
      document_count:dossier.documents.length,signal_count:signalCount,reviewed_signal_count:reviewed,
      pending_signal_count:signalCount-reviewed,last_run_id:run?.id,last_run_status:run?.result.status,
      last_run_stale:run?.stale || false,created_at:dossier.created_at,updated_at:dossier.updated_at,
      is_demo:true,revision:dossier.revision,metadata_revision:dossier.metadata_revision};
  };
  globalThis.KONTROL_PREVIEW = {
    async api(path, options = {}) {
      const url = new URL(path,'http://preview.local'), method = options.method || 'GET';
      const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);
      if (path === '/api/demo') return [{id:'T02',title:'Поставка уличных скамеек'}];
      if (path === '/api/cases' && method === 'GET') return [...cases.values()].map(summary);
      if (path === '/api/cases' && method === 'POST') {
        const payload = JSON.parse(options.body), id = 'preview-case-' + (++sequence);
        const dossier = {id,...payload,created_at:now(),updated_at:now(),revision:0,metadata_revision:0,documents:[],runs:[],is_demo:true};
        cases.set(id,dossier); event(id,'case_created',{title:dossier.title}); return copy(dossier);
      }
      if (parts[1] === 'demo' && method === 'POST') {
        return {case:copy(cases.get(seed.dossier.id)),options:copy(seed.run.request)};
      }
      if (parts[1] === 'cases') {
        const dossier = cases.get(parts[2]);
        if (!dossier) throw failure('Проверка не найдена',404);
        if (parts.length === 3 && method === 'GET') return copy(dossier);
        if (parts.length === 3 && method === 'PUT') {
          const payload = JSON.parse(options.body);
          if (payload.expected_metadata_revision !== dossier.metadata_revision) throw failure('Карточка изменена. Откройте её заново.',409);
          for (const key of ['title','description','contract_number']) dossier[key] = payload[key].trim();
          dossier.metadata_revision++; dossier.updated_at = now();
          event(dossier.id,'case_updated',{title:dossier.title}); return copy(dossier);
        }
        if (parts[3] === 'events') return copy(events.get(dossier.id) || []);
        if (parts[3] === 'runs' && method === 'POST') throw failure('Автономный макет показывает готовый синтетический результат. Для новой сверки запустите проект с сервером.');
        if (parts[3] === 'documents' && method === 'POST') throw failure('Загрузка и распознавание файлов доступны в версии с сервером. В макете файл не обработан.');
        const record = dossier.documents.find(item => item.id === parts[4]);
        if (!record) throw failure('Документ не найден',404);
        if (parts[5] === 'file') throw failure('В синтетическом макете оригинального файла нет.',404);
        if (parts[5] === 'history') return copy(record.history || []);
        if (method === 'PUT') {
          const payload = JSON.parse(options.body);
          if (payload.expected_revision !== record.revision) throw failure('Версия документа изменилась.',409);
          record.document = payload.document; record.revision++; dossier.revision++;
          record.history ||= [];
          record.history.push({revision:record.revision,reviewer:payload.reviewer,comment:payload.comment,created_at:now()});
          dossier.updated_at = now();
          for (const run of runs.values()) if (run.case_id === dossier.id) run.stale = true;
          event(dossier.id,'document_reviewed',{document_id:record.id,reviewer:payload.reviewer,comment:payload.comment});
          return copy(record);
        }
      }
      if (parts[1] === 'runs' && parts.length === 2) {
        const items = [...runs.values()].map(run => ({id:run.id,case_id:run.case_id,case_title:cases.get(run.case_id).title,created_at:run.created_at,status:run.result.status,signal_count:run.result.signals.length,stale:run.stale}));
        const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 25);
        return {items:items.slice(offset,offset+limit),total:items.length,limit,offset};
      }
      if (parts[1] === 'runs') {
        const run = runs.get(parts[2]);
        if (!run) throw failure('Сохранённый результат не найден',404);
        if (parts.length === 3) return copy(run);
        if (parts[3] === 'signals' && method === 'POST') {
          const payload = JSON.parse(options.body);
          const review = {signal_id:parts[4],...payload,created_at:now()};
          run.reviews.push(review); run.latest_reviews[parts[4]] = review;
          event(run.case_id,'signal_reviewed',review);
          return copy(review);
        }
        if (parts[3] === 'export') {
          if (url.searchParams.get('format') === 'json') return new Blob([JSON.stringify(run,null,2)],{type:'application/json'});
          throw failure('В автономном макете доступен экспорт JSON. PDF, HTML и CSV формируются сервером.');
        }
      }
      throw failure('Это действие доступно в версии с сервером.');
    },
  };
})();
