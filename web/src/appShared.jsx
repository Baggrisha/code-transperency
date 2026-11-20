import React, {useState} from 'react';

export const types = {contract:'Контракт', specification:'Спецификация', delivery:'Накладная', acceptance:'Документ о приёмке', amendment:'Дополнительное соглашение'};
export const reviewNames = {needs_review:'Требует рассмотрения', confirmed:'Расхождение подтверждено', resolved:'Устранено', dismissed:'Сигнал отклонён'};
export const tabs = [['overview','Обзор'], ['documents','Документы'], ['recognition','Распознавание'], ['matching','Сопоставление'], ['results','Результаты'], ['actions','История действий']];
export const enc = encodeURIComponent;
export const number = value => value === null || value === undefined ? '—' : String(value);
export const showValue = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const documentName = record => record.document.filename || record.original_name || record.id;
export const documentTypeLabel = type => types[type] || (type === 'other' ? 'Другое' : type);
export function dateLabel(value, withTime = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString('ru-RU', {timeZone:'Europe/Moscow'}) + (withTime ? ', ' + date.toLocaleTimeString('ru-RU', {timeZone:'Europe/Moscow', hour:'2-digit', minute:'2-digit'}) : '');
}
export function latestReviews(run) { return run?.latest_reviews || Object.fromEntries((run?.reviews || []).map(review => [review.signal_id, review])); }
export function runCounts(run) {
  const signals = run?.result?.signals || [], reviews = latestReviews(run);
  const reviewed = signals.filter(signal => reviews[signal.id] && reviews[signal.id].status !== 'needs_review').length;
  return {signals:signals.length, reviewed, pending:signals.length - reviewed};
}
export function contractNumber(dossier) {
  if (dossier?.contract_number) return dossier.contract_number;
  const numbers = [...new Set((dossier?.documents || []).filter(record => record.document.type === 'contract' && (record.document.status || 'active') === 'active').map(record => record.document.number?.trim() || record.document.contract_id?.trim()).filter(Boolean))];
  return numbers.length === 1 ? numbers[0] : '';
}
export function statusInfo(item, run) {
  const status = run?.result?.status || item?.last_run_status, stale = run?.stale ?? item?.last_run_stale;
  const counts = run ? runCounts(run) : {signals:item?.signal_count || 0, pending:item?.pending_signal_count || 0};
  if (stale) return {key:'stale', text:'Обновить сверку', tone:'warning'};
  if (!status) return {key:'draft', text:'Черновик', tone:'neutral'};
  if (counts.pending) return {key:'needs_review', text:'Требует рассмотрения', tone:'warning'};
  if (status === 'incomplete') return {key:'incomplete', text:'Нужны уточнения', tone:'warning'};
  if (counts.signals) return {key:'reviewed', text:'Рассмотрено', tone:'ok'};
  if (status === 'ok') return {key:'ok', text:'Расхождений нет', tone:'ok'};
  return {key:'needs_review', text:'Требует рассмотрения', tone:'warning'};
}
function isDemo(dossier) { return Boolean(dossier?.is_demo || /синтетическ|демо|^T\d{2}\s*·/i.test((dossier?.title || '') + ' ' + (dossier?.description || ''))); }
export function caseTitle(dossier) {
  const title = dossier?.title || '', suffix = ' [синтетические данные]';
  return isDemo(dossier) && title.endsWith(suffix) ? title.slice(0,-suffix.length) : title;
}
export function caseDescription(dossier) {
  const description = dossier?.description || '';
  return isDemo(dossier) && description === 'Учебный кейс T02. Синтетические данные.' ? 'Сопоставление документов по поставке уличных скамеек.' : description;
}
export function Icon({name, className = ''}) { return <span className={`identity-icon identity-icon--${name} ${className}`} aria-hidden="true"/>; }
export function SearchIcon() { return <svg className="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>; }
export function Heading({icon, children}) { return <h2 className="section-heading"><Icon name={icon} className="section-icon"/><span>{children}</span></h2>; }
export function Header({eyebrow, title, subtitle, action}) { return <header className="page-header"><div className="header-copy"><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p className="subtitle">{subtitle}</p></div>{action && <div className="header-action">{action}</div>}</header>; }
export function Stat({label, value}) { return <div className="stat"><span>{label}</span><b>{value}</b></div>; }
export function StaleNotice({run}) { return run?.stale ? <div className="notice" role="status"><b>Документы изменены после этой проверки.</b> Выполните сверку ещё раз. Ниже показан сохранённый результат.</div> : null; }

// Each control owns its pending state; failed operations remain available for retry.
export function Action({app, action, children, disabled, ...props}) {
  const [pending, setPending] = useState(false);
  return <button type="button" {...props} disabled={disabled || pending} onClick={async event => {
    if (pending) return;
    setPending(true);
    try { await action(event); } catch (error) { app.toast(error.message, true); } finally { setPending(false); }
  }}>{children}</button>;
}
export function AsyncForm({app, onSave, children, ...props}) {
  const [pending, setPending] = useState(false);
  return <form {...props} aria-busy={pending} onSubmit={async event => {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    setPending(true);
    try { await onSave(form); } catch (error) { app.toast(error.message, true); } finally { setPending(false); }
  }}>{typeof children === 'function' ? children(pending) : children}</form>;
}
