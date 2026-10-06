(() => {
  const token = decodeURIComponent(location.pathname.split('/').pop() || '');
  const $ = id => document.getElementById(id);
  const labels = {
    vi:{title:'Trạng thái yêu cầu',loading:'Đang tải…',error:'Không tìm thấy hoặc mã đã hết hạn.',reference:'Mã xác nhận',status:'Trạng thái',service:'Dịch vụ',eta:'Thời gian dự kiến',updated:'Cập nhật'},
    en:{title:'Stay request status',loading:'Loading…',error:'Status not found or expired.',reference:'Reference',status:'Status',service:'Service',eta:'Estimated time',updated:'Updated'},
    zh:{title:'服务请求状态',loading:'正在加载…',error:'找不到状态或链接已过期。',reference:'确认码',status:'状态',service:'服务',eta:'预计时间',updated:'更新时间'},
    ko:{title:'요청 상태',loading:'불러오는 중…',error:'상태를 찾을 수 없거나 링크가 만료되었습니다.',reference:'확인 코드',status:'상태',service:'서비스',eta:'예상 시간',updated:'업데이트'}
  };
  const statusLabels = {pending_staff:['Chờ nhân viên','Awaiting staff','等待员工','직원 확인 대기'],approved:['Đã duyệt','Approved','已批准','승인됨'],in_progress:['Đang xử lý','In progress','处理中','처리 중'],paused:['Tạm dừng','Paused','已暂停','일시 중지'],rejected:['Từ chối','Rejected','已拒绝','거부됨'],completed:['Hoàn tất','Completed','已完成','완료']};
  const setText = (id, value) => { $(id).textContent = value; };
  fetch('/api/status/' + encodeURIComponent(token), {credentials:'omit',cache:'no-store'}).then(r => { if(!r.ok) throw new Error('status'); return r.json(); }).then(data => {
    const lang = labels[data.language] ? data.language : 'en'; const l = labels[lang];
    const index = {vi:0,en:1,zh:2,ko:3}[lang];
    setText('title',l.title); setText('message',''); setText('reference-label',l.reference); setText('status-label',l.status); setText('kind-label',l.service); setText('eta-label',l.eta); setText('reference',data.confirmation_code); setText('status',(statusLabels[data.status] || [data.status,data.status,data.status,data.status])[index]); setText('kind',data.kind || '—'); setText('eta',data.eta_minutes == null ? '—' : data.eta_minutes + ' min'); setText('updated',l.updated + ': ' + new Date(data.updated_at * 1000).toLocaleString()); $('card').hidden = false;
  }).catch(() => { $('message').className='error'; setText('message',labels.en.error); });
})();
