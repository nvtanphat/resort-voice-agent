import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../index.css';

type Language = 'vi' | 'en' | 'zh' | 'ko';
type Status = 'pending_staff' | 'approved' | 'in_progress' | 'paused' | 'rejected' | 'completed';
type Action = 'approve' | 'reject' | 'start' | 'pause' | 'resume' | 'complete';

type StaffRequest = {
  id: string;
  kind: string;
  language: Language;
  details: string;
  status: Status;
  created_at: number;
  updated_at: number;
  staff_note?: string;
  verified_by?: string;
  department_id?: string;
  priority?: number;
  overdue?: number;
  ack_overdue?: number;
  assignee?: string;
  eta_minutes?: number | null;
  guest_verification_state?: string;
  external_dispatch_state?: 'accepted' | 'queued' | 'pending_sync' | 'failed' | 'not_configured' | 'not_requested';
  external_reference?: string;
  guest_change_state?: string;
  [key: string]: unknown;
};

type Summary = {
  pending_count: number;
  approved_count: number;
  emergency_count: number;
  oldest_pending_at?: number | null;
};

type Emergency = {id: string; status: 'open' | 'acknowledged' | 'resolved'; language: Language; details?: string; created_at: number; [key: string]: unknown};

const copy: Record<Language, Record<string, string>> = {
  vi: {title: 'Bàn điều phối nhân viên', subtitle: 'HITL · Hàng đợi dịch vụ · Audit', token: 'Staff bearer token', signIn: 'Đăng nhập', signOut: 'Đăng xuất', queue: 'Hàng đợi', emergencies: 'Khẩn cấp', metrics: 'KPI', refresh: 'Làm mới', pending: 'Chờ duyệt', active: 'Đang xử lý', emergency: 'Khẩn cấp', all: 'Tất cả', details: 'Chi tiết yêu cầu', note: 'Ghi chú bắt buộc', assignee: 'Người phụ trách', approve: 'Duyệt', reject: 'Từ chối', start: 'Nhận việc', pause: 'Tạm dừng', resume: 'Tiếp tục', complete: 'Hoàn tất', verify: 'Tôi đã kiểm tra độc lập', audit: 'Lịch sử audit', noItems: 'Không có yêu cầu phù hợp.', invalid: 'Token không hợp lệ hoặc không được cấp quyền.', actionError: 'Thao tác chưa được ghi nhận; hãy kiểm tra lại trạng thái.', synthetic: 'Dữ liệu mô phỏng', load: 'Đang tải…', signInHint: 'Token chỉ được giữ trong phiên trình duyệt này.'},
  en: {title: 'Staff operations desk', subtitle: 'HITL · Service queue · Audit', token: 'Staff bearer token', signIn: 'Sign in', signOut: 'Sign out', queue: 'Queue', emergencies: 'Emergencies', metrics: 'KPI', refresh: 'Refresh', pending: 'Pending review', active: 'In progress', emergency: 'Emergency', all: 'All', details: 'Request details', note: 'Required review note', assignee: 'Assignee', approve: 'Approve', reject: 'Reject', start: 'Take work', pause: 'Pause', resume: 'Resume', complete: 'Complete', verify: 'I independently verified the details', audit: 'Audit history', noItems: 'No matching requests.', invalid: 'Invalid or unauthorized token.', actionError: 'Action was not recorded; re-check the current state.', synthetic: 'Synthetic data', load: 'Loading…', signInHint: 'The token stays only in this browser session.'},
  zh: {title: '员工运营台', subtitle: '人工审核 · 服务队列 · 审计', token: '员工令牌', signIn: '登录', signOut: '退出', queue: '队列', emergencies: '紧急事件', metrics: '指标', refresh: '刷新', pending: '待审核', active: '处理中', emergency: '紧急', all: '全部', details: '请求详情', note: '审核备注', assignee: '负责人', approve: '批准', reject: '拒绝', start: '开始处理', pause: '暂停', resume: '继续', complete: '完成', verify: '我已独立核实详情', audit: '审计记录', noItems: '没有匹配请求。', invalid: '令牌无效或无权限。', actionError: '操作未记录，请重新检查状态。', synthetic: '模拟数据', load: '加载中…', signInHint: '令牌只保留在本浏览器会话中。'},
  ko: {title: '직원 운영 데스크', subtitle: '사람 검토 · 서비스 대기열 · 감사', token: '직원 bearer 토큰', signIn: '로그인', signOut: '로그아웃', queue: '대기열', emergencies: '긴급', metrics: '지표', refresh: '새로고침', pending: '검토 대기', active: '처리 중', emergency: '긴급', all: '전체', details: '요청 상세', note: '필수 검토 메모', assignee: '담당자', approve: '승인', reject: '거절', start: '작업 시작', pause: '일시 중지', resume: '재개', complete: '완료', verify: '세부 내용을 독립적으로 확인했습니다', audit: '감사 기록', noItems: '일치하는 요청이 없습니다.', invalid: '토큰이 유효하지 않거나 권한이 없습니다.', actionError: '작업이 기록되지 않았습니다. 상태를 다시 확인하세요.', synthetic: '시뮬레이션 데이터', load: '로드 중…', signInHint: '토큰은 이 브라우저 세션에만 보관됩니다.'},
};

function text(lang: Language, key: string): string { return copy[lang][key] || copy.en[key] || key; }
function idempotencyKey(): string { return crypto.randomUUID().replace(/-/g, ''); }
function dateTime(value: unknown, lang: Language): string {
  return typeof value === 'number' ? new Date(value * 1000).toLocaleString(lang === 'vi' ? 'vi-VN' : lang === 'zh' ? 'zh-CN' : lang === 'ko' ? 'ko-KR' : 'en-GB') : '—';
}

async function apiCall<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  headers.set('Accept', 'application/json');
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {...init, headers});
  if (!response.ok) throw new Error(`${response.status}:${await response.text()}`);
  return response.status === 204 ? undefined as T : await response.json() as T;
}

function StaffApp(): React.ReactElement {
  const [lang, setLang] = useState<Language>('vi');
  const [token, setToken] = useState('');
  const [draftToken, setDraftToken] = useState('');
  const [requests, setRequests] = useState<StaffRequest[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [emergencies, setEmergencies] = useState<Emergency[]>([]);
  const [selected, setSelected] = useState<StaffRequest | null>(null);
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);
  const [filter, setFilter] = useState<'all' | Status>('pending_staff');
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState('');
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'queue' | 'emergencies' | 'metrics'>('queue');

  const tr = (key: string) => text(lang, key);
  const statusLabel = (status: Status) => ({pending_staff: tr('pending'), approved: tr('approve'), in_progress: tr('active'), paused: tr('pause'), rejected: tr('reject'), completed: tr('complete')}[status]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true); setError('');
    try {
      const status = filter === 'all' ? '' : `&status=${encodeURIComponent(filter)}`;
      const [page, nextSummary, nextEmergencies] = await Promise.all([
        apiCall<{items: StaffRequest[]; next_cursor: string | null}>(token, `/staff/requests/page?limit=100${status}`),
        apiCall<Summary>(token, '/staff/queue/summary'),
        apiCall<Emergency[]>(token, '/staff/emergencies?limit=50'),
      ]);
      setRequests(page.items); setSummary(nextSummary); setEmergencies(nextEmergencies);
      if (selected) {
        const fresh = page.items.find(item => item.id === selected.id);
        if (fresh) setSelected(fresh);
      }
    } catch { setError(tr('invalid')); }
    finally { setLoading(false); }
  }, [filter, selected, token]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (!token) return undefined; const timer = window.setInterval(() => void load(), 8000); return () => window.clearInterval(timer); }, [load, token]);
  useEffect(() => {
    if (!token || !selected) { setAudit([]); return; }
    void apiCall<Record<string, unknown>[]>(token, `/staff/requests/${selected.id}/audit?limit=100`).then(setAudit).catch(() => setAudit([]));
  }, [selected, token]);

  const doTransition = async (action: Action) => {
    if (!selected || busy) return;
    if ((action === 'approve' || action === 'reject') && note.trim().length < 8) { setError(tr('note')); return; }
    if (action === 'approve' && !verified) { setError(tr('verify')); return; }
    setBusy(true); setError('');
    try {
      await apiCall(token, `/staff/requests/${selected.id}/transition`, {method: 'POST', headers: {'Idempotency-Key': idempotencyKey()}, body: JSON.stringify({action, verified, note: note.trim(), assignee: assignee.trim()})});
      setNote(''); setVerified(false); setAssignee(''); await load();
    } catch { setError(tr('actionError')); }
    finally { setBusy(false); }
  };

  const transitionEmergency = async (item: Emergency, action: 'acknowledge' | 'resolve') => {
    try { await apiCall(token, `/staff/emergencies/${item.id}/transition`, {method: 'POST', body: JSON.stringify({action, note: note.trim()})}); await load(); }
    catch { setError(tr('actionError')); }
  };

  const filteredRequests = useMemo(() => requests, [requests]);
  if (!token) return <main className="min-h-screen bg-[#f3efe6] p-6 text-[#1c2430] sm:p-12"><section className="mx-auto mt-16 max-w-md rounded-3xl bg-white p-8 shadow-xl"><p className="text-xs font-bold uppercase tracking-[.2em] text-[#8c6d3e]">Concierge Kiosk · Staff</p><h1 className="mt-3 text-3xl font-semibold">{tr('title')}</h1><p className="mt-2 text-sm text-gray-500">{tr('signInHint')}</p><label className="mt-8 block text-sm font-semibold">{tr('token')}<input value={draftToken} onChange={event => setDraftToken(event.target.value)} type="password" autoComplete="off" className="mt-2 w-full rounded-xl border border-[#d8d0c2] p-3 outline-none focus:border-[#8c6d3e]" /></label>{error && <p className="mt-3 text-sm text-red-700">{error}</p>}<button onClick={() => {setError(''); setToken(draftToken.trim());}} disabled={!draftToken.trim()} className="mt-6 w-full rounded-xl bg-[#142742] px-4 py-3 font-semibold text-white disabled:opacity-50">{tr('signIn')}</button><div className="mt-5 flex justify-end gap-2 text-xs"><span>VI</span>{(['en', 'zh', 'ko'] as Language[]).map(code => <button key={code} onClick={() => setLang(code)} className="text-[#8c6d3e]">{code.toUpperCase()}</button>)}</div></section></main>;

  return <main className="min-h-screen bg-[#f3efe6] text-[#1c2430]"><header className="border-b border-[#e8e2d5] bg-[#142742] px-5 py-4 text-white sm:px-8"><div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[.2em] text-[#d8c29d]">Concierge Kiosk</p><h1 className="text-xl font-semibold sm:text-2xl">{tr('title')}</h1><p className="text-xs text-blue-100">{tr('subtitle')}</p></div><div className="flex items-center gap-3"><select value={lang} onChange={event => setLang(event.target.value as Language)} className="rounded-lg bg-white/10 px-2 py-2 text-sm"><option value="vi">VI</option><option value="en">EN</option><option value="zh">中文</option><option value="ko">한국어</option></select><button onClick={() => {setToken(''); setDraftToken('');}} className="rounded-lg border border-white/30 px-3 py-2 text-sm">{tr('signOut')}</button></div></div></header><div className="mx-auto max-w-[1500px] p-4 sm:p-8"><div className="grid gap-3 sm:grid-cols-3"><Stat label={tr('pending')} value={summary?.pending_count ?? '—'} tone="amber" /><Stat label={tr('active')} value={summary?.approved_count ?? '—'} tone="blue" /><Stat label={tr('emergency')} value={summary?.emergency_count ?? '—'} tone="red" /></div><nav className="mt-6 flex flex-wrap items-center gap-2"><NavButton active={tab === 'queue'} onClick={() => setTab('queue')}>{tr('queue')}</NavButton><NavButton active={tab === 'emergencies'} onClick={() => setTab('emergencies')}>{tr('emergencies')} {summary?.emergency_count ? `(${summary.emergency_count})` : ''}</NavButton><NavButton active={tab === 'metrics'} onClick={() => setTab('metrics')}>{tr('metrics')}</NavButton><button onClick={() => void load()} className="ml-auto rounded-xl border border-[#d8d0c2] bg-white px-4 py-2 text-sm font-semibold">{loading ? tr('load') : tr('refresh')}</button></nav>{error && <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}{tab === 'queue' && <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,440px)]"><section className="rounded-2xl border border-[#e8e2d5] bg-white p-4 shadow-sm"><div className="mb-4 flex flex-wrap gap-2">{(['pending_staff', 'approved', 'in_progress', 'paused', 'rejected', 'completed', 'all'] as const).map(value => <button key={value} onClick={() => setFilter(value)} className={`rounded-full px-3 py-1.5 text-xs font-semibold ${filter === value ? 'bg-[#142742] text-white' : 'bg-[#f3efe6] text-gray-600'}`}>{value === 'all' ? tr('all') : statusLabel(value)}</button>)}</div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b text-xs uppercase tracking-wide text-gray-500"><tr><th className="px-2 py-3">ID</th><th className="px-2 py-3">{tr('details')}</th><th className="px-2 py-3">Status</th><th className="px-2 py-3">SLA</th></tr></thead><tbody>{filteredRequests.map(item => <tr key={item.id} onClick={() => setSelected(item)} className={`cursor-pointer border-b last:border-0 hover:bg-[#fbf8f2] ${selected?.id === item.id ? 'bg-[#fbf8f2]' : ''}`}><td className="whitespace-nowrap px-2 py-3 font-mono text-xs">{item.id.slice(0, 8)}</td><td className="max-w-[460px] px-2 py-3"><div className="font-semibold">{item.kind}</div><div className="truncate text-gray-600">{item.details || '—'}</div><div className="mt-1 text-xs text-gray-400">{dateTime(item.created_at, lang)} · {item.language}</div></td><td className="whitespace-nowrap px-2 py-3"><span className={`rounded-full px-2 py-1 text-xs font-semibold ${item.status === 'pending_staff' ? 'bg-amber-100 text-amber-800' : item.status === 'completed' ? 'bg-green-100 text-green-800' : item.status === 'rejected' ? 'bg-gray-100 text-gray-700' : 'bg-blue-100 text-blue-800'}`}>{statusLabel(item.status)}</span></td><td className="px-2 py-3 text-xs">{item.overdue || item.ack_overdue ? <span className="font-bold text-red-700">OVERDUE</span> : '—'}</td></tr>)}</tbody></table>{!filteredRequests.length && <p className="p-8 text-center text-sm text-gray-500">{tr('noItems')}</p>}</div></section><DetailPanel item={selected} lang={lang} note={note} setNote={setNote} assignee={assignee} setAssignee={setAssignee} verified={verified} setVerified={setVerified} audit={audit} busy={busy} onAction={doTransition} tr={tr} /></div>}{tab === 'emergencies' && <EmergencyPanel items={emergencies} lang={lang} onTransition={transitionEmergency} tr={tr} />}{tab === 'metrics' && <MetricsPanel token={token} lang={lang} tr={tr} />}</div></main>;
}

function Stat({label, value, tone}: {label: string; value: number | string; tone: string}) { return <div className={`rounded-2xl border bg-white p-5 shadow-sm ${tone === 'red' ? 'border-red-100' : tone === 'amber' ? 'border-amber-100' : 'border-blue-100'}`}><p className="text-sm text-gray-500">{label}</p><p className="mt-2 text-3xl font-semibold">{value}</p></div>; }
function NavButton({active, onClick, children}: {active: boolean; onClick: () => void; children: React.ReactNode}) { return <button onClick={onClick} className={`rounded-xl px-4 py-2 text-sm font-semibold ${active ? 'bg-[#142742] text-white' : 'border border-[#d8d0c2] bg-white'}`}>{children}</button>; }

function DetailPanel({item, lang, note, setNote, assignee, setAssignee, verified, setVerified, audit, busy, onAction, tr}: {item: StaffRequest | null; lang: Language; note: string; setNote: (value: string) => void; assignee: string; setAssignee: (value: string) => void; verified: boolean; setVerified: (value: boolean) => void; audit: Record<string, unknown>[]; busy: boolean; onAction: (action: Action) => void; tr: (key: string) => string}) {
  if (!item) return <aside className="rounded-2xl border border-dashed border-[#d8d0c2] p-8 text-sm text-gray-500">{tr('details')}</aside>;
  const actions: Action[] = item.status === 'pending_staff' ? ['approve', 'reject'] : item.status === 'approved' ? ['start', 'complete'] : item.status === 'in_progress' ? ['pause', 'complete'] : item.status === 'paused' ? ['resume'] : [];
  return <aside className="rounded-2xl border border-[#e8e2d5] bg-white p-5 shadow-sm"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-xs text-gray-500">{item.id}</p><h2 className="mt-1 text-xl font-semibold">{item.kind}</h2></div><span className="rounded-full bg-[#f3efe6] px-3 py-1 text-xs font-semibold">{item.language}</span></div><dl className="mt-5 space-y-3 text-sm"><div><dt className="text-xs uppercase text-gray-500">{tr('details')}</dt><dd className="mt-1 whitespace-pre-wrap">{item.details || '—'}</dd></div><div className="grid grid-cols-2 gap-3"><div><dt className="text-xs text-gray-500">Status</dt><dd className="font-semibold">{item.status}</dd></div><div><dt className="text-xs text-gray-500">{tr('assignee')}</dt><dd>{item.assignee || '—'}</dd></div></div><div className="grid grid-cols-2 gap-3"><div><dt className="text-xs text-gray-500">Created</dt><dd>{dateTime(item.created_at, lang)}</dd></div><div><dt className="text-xs text-gray-500">Verification</dt><dd>{item.guest_verification_state || 'staff_required'}</dd></div></div>{item.external_dispatch_state && item.external_dispatch_state !== 'not_requested' && <div className={`rounded-xl p-3 text-xs ${item.external_dispatch_state === 'pending_sync' ? 'bg-amber-50 text-amber-900' : 'bg-[#f8f5ee]'}`}><strong>External dispatch: {item.external_dispatch_state}</strong>{item.external_reference && <span> · {item.external_reference}</span>}<br />{item.external_dispatch_state === 'pending_sync' ? 'Upstream unavailable; local request is retained and not claimed as fulfilled.' : ''}</div>}</dl><div className="mt-5 border-t pt-4"><label className="block text-sm font-semibold">{tr('note')}<textarea value={note} onChange={event => setNote(event.target.value)} maxLength={300} rows={3} className="mt-2 w-full rounded-xl border border-[#d8d0c2] p-3 text-sm" /></label><label className="mt-3 block text-sm font-semibold">{tr('assignee')}<input value={assignee} onChange={event => setAssignee(event.target.value)} maxLength={80} className="mt-2 w-full rounded-xl border border-[#d8d0c2] p-3 text-sm" /></label>{item.status === 'pending_staff' && <label className="mt-3 flex gap-2 text-sm"><input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} />{tr('verify')}</label>}<div className="mt-4 flex flex-wrap gap-2">{actions.map(action => <button key={action} disabled={busy} onClick={() => onAction(action)} className={`rounded-xl px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${action === 'reject' ? 'bg-gray-600' : action === 'complete' ? 'bg-green-700' : 'bg-[#142742]'}`}>{tr(action)}</button>)}</div></div><details className="mt-5 border-t pt-4"><summary className="cursor-pointer text-sm font-semibold">{tr('audit')}</summary><div className="mt-3 max-h-52 space-y-2 overflow-auto text-xs">{audit.map((event, index) => <div key={index} className="rounded-lg bg-[#f8f5ee] p-2"><span className="font-semibold">{String(event.action || 'event')}</span> · {String(event.actor || 'system')}<br />{String(event.note || '')}</div>)}</div></details></aside>;
}

function EmergencyPanel({items, lang, onTransition, tr}: {items: Emergency[]; lang: Language; onTransition: (item: Emergency, action: 'acknowledge' | 'resolve') => void; tr: (key: string) => string}) { return <section className="mt-4 grid gap-4 md:grid-cols-2">{items.map(item => <article key={item.id} className={`rounded-2xl border bg-white p-5 shadow-sm ${item.status !== 'resolved' ? 'border-red-200' : 'border-[#e8e2d5]'}`}><div className="flex justify-between"><span className="font-mono text-xs">{item.id.slice(0, 8)}</span><span className="font-semibold text-red-700">{item.status}</span></div><p className="mt-4 whitespace-pre-wrap text-sm">{String(item.details || tr('emergency'))}</p><p className="mt-3 text-xs text-gray-500">{dateTime(item.created_at, lang)} · {item.language}</p><div className="mt-4 flex gap-2">{item.status === 'open' && <button onClick={() => onTransition(item, 'acknowledge')} className="rounded-xl bg-red-700 px-3 py-2 text-sm font-semibold text-white">{tr('start')}</button>}{item.status !== 'resolved' && <button onClick={() => onTransition(item, 'resolve')} className="rounded-xl border px-3 py-2 text-sm font-semibold">{tr('complete')}</button>}</div></article>)}{!items.length && <p className="rounded-2xl bg-white p-8 text-center text-sm text-gray-500">{tr('noItems')}</p>}</section>; }

function MetricsPanel({token, tr}: {token: string; lang: Language; tr: (key: string) => string}) { const [data, setData] = useState<{kpi?: {ask_total:number;grounded_answer_count:number;no_evidence_abstention_count:number;grounded_answer_rate:number|null;request_confirmed_count:number;request_completed_count:number;request_completion_rate:number|null;emergency_alert_count:number};counters?: Record<string, unknown>[]; latency?: Record<string, unknown>[]; note?: string} | null>(null); useEffect(() => { void apiCall<typeof data>(token, '/staff/metrics').then(setData).catch(() => setData(null)); }, [token]); const k=data?.kpi; const pct=(value:number|null) => value == null ? '—' : `${Math.round(value*100)}%`; return <section className="mt-4 rounded-2xl border border-[#e8e2d5] bg-white p-5 shadow-sm"><h2 className="text-xl font-semibold">{tr('metrics')}</h2><p className="mt-2 text-sm text-gray-500">{data?.note || tr('load')}</p>{k&&<div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Stat label="Grounded answers" value={`${pct(k.grounded_answer_rate)} · ${k.grounded_answer_count}/${k.ask_total}`} tone="blue"/><Stat label="Abstentions" value={k.no_evidence_abstention_count} tone="amber"/><Stat label="Request completion" value={`${pct(k.request_completion_rate)} · ${k.request_completed_count}/${k.request_confirmed_count}`} tone="blue"/><Stat label="Emergency events" value={k.emergency_alert_count} tone="red"/></div>}<details className="mt-5"><summary className="cursor-pointer text-sm font-semibold">Raw counters</summary><pre className="mt-3 max-h-[420px] overflow-auto rounded-xl bg-[#142742] p-4 text-xs text-blue-50">{JSON.stringify(data?.counters || [], null, 2)}</pre></details></section>; }

createRoot(document.getElementById('root') as HTMLElement).render(<StaffApp />);
