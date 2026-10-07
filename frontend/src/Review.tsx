import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { Answer, Candidate, ListState, Project, ReviewValues, Source } from './types';
import { questionHelp } from './questionHelp';

// JSON object key order can change when the API resolves saved evidence.
// Compare content recursively, while keeping evidence and coordinate array order meaningful.
function sameContent(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameContent(value, right[index]))
    );
  }
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  const a = left as Record<string, unknown>;
  const b = right as Record<string, unknown>;
  return (
    Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every((key) => Object.hasOwn(b, key) && sameContent(a[key], b[key]))
  );
}

export function Badge({ answer }: { answer: Answer }) {
  return <span className={`badge ${answer.review_status}`}>{answer.review_label}</span>;
}
export function AnswerList({
  project,
  onSelect,
  onDocuments,
  state,
  onState,
}: {
  project: Project;
  onSelect: (id: string) => void;
  onDocuments: () => void;
  state: ListState;
  onState: (v: ListState) => void;
}) {
  const answers = project.result?.answers || [];
  const sections = [...new Set(answers.map((a) => a.section))];
  const query = state.search.trim().toLocaleLowerCase();
  const filtered = answers.filter(
    (a) =>
      (state.section === 'all' || a.section === state.section) &&
      (state.filter === 'all' ||
        (state.filter === 'pending' && a.review_status !== 'complete') ||
        a.review_status === state.filter) &&
      `${a.id} ${a.question} ${a.value_text} ${a.section} ${a.sources.map((s) => s.name).join(' ')}`
        .toLocaleLowerCase()
        .includes(query),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const page = Math.min(state.page, pages - 1);
  const update = (v: Partial<ListState>) => onState({ ...state, page: 0, ...v });
  if (!project.result)
    return (
      <div className="empty-state">
        <h2>분석 후 문항별 답변을 검토할 수 있습니다.</h2>
        <button className="primary" onClick={onDocuments}>
          자료 준비로 이동
        </button>
      </div>
    );
  return (
    <>
      <div className="counts">
        <span>
          전체 <strong>{answers.length}</strong>
        </span>
        <span>
          검토 완료 <strong>{answers.filter((a) => a.review_status === 'complete').length}</strong>
        </span>
        <span>
          자료 필요 <strong>{answers.filter((a) => a.review_status === 'missing').length}</strong>
        </span>
        <span>
          자료 변경 <strong>{answers.filter((a) => a.review_status === 'again').length}</strong>
        </span>
      </div>
      <div className="list-tools">
        <label>
          분야
          <select
            aria-label="분야"
            value={state.section}
            onChange={(e) => update({ section: e.target.value })}
          >
            <option value="all">전체 분야</option>
            {sections.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          검토 상태
          <select
            aria-label="검토 상태"
            value={state.filter}
            onChange={(e) => update({ filter: e.target.value })}
          >
            {[
              ['all', '전체'],
              ['pending', '남은 문항'],
              ['complete', '검토 완료'],
              ['missing', '자료 필요'],
              ['again', '자료 변경'],
            ].map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="question-search">
          <Search />
          <input
            aria-label="문항 검색"
            placeholder="문항·답변·파일명 검색"
            value={state.search}
            onChange={(e) => update({ search: e.target.value })}
          />
        </label>
      </div>
      <div className="answer-table" role="table" aria-label="실사 응답 목록">
        <div className="answer-table-head" role="row">
          {['문항', '저장된 답변', '검토 상태', '근거 파일'].map((t) => (
            <span key={t} role="columnheader">
              {t}
            </span>
          ))}
        </div>
        {filtered.slice(page * 10, page * 10 + 10).map((a) => (
          <div className="answer-table-row" role="row" key={a.id}>
            <div role="cell">
              <small>
                {a.id} · {a.section}
              </small>
              <button
                className="question-link"
                aria-label={`${a.question} 살펴보기`}
                onClick={() => onSelect(a.id)}
              >
                {a.question}
              </button>
            </div>
            <div role="cell" className="table-value">
              <strong>{a.value_text}</strong>
              <small>
                {a.method} · {a.scope_label || '범위 미확인'}
              </small>
            </div>
            <div role="cell">
              <Badge answer={a} />
              <small className="trust-note">근거 판단: {a.status_label}</small>
            </div>
            <div role="cell" className="table-sources">
              {a.sources.length ? (
                a.sources.map((s, i) => (
                  <button key={i} className="source-link" onClick={() => onSelect(a.id)}>
                    {s.name}
                    <small>
                      {s.page !== null ? `${s.page + 1}쪽` : '페이지 미확인'} · 버전{' '}
                      {s.version || 1}
                    </small>
                  </button>
                ))
              ) : (
                <button className="source-link" onClick={() => onSelect(a.id)}>
                  근거 연결하기
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {!filtered.length && <p className="empty-list">조건에 맞는 문항이 없습니다.</p>}
      <div className="pagination">
        <span>
          {filtered.length ? `${page * 10 + 1}–${Math.min((page + 1) * 10, filtered.length)}` : '0'}{' '}
          / {filtered.length}문항
        </span>
        <button className="secondary" disabled={!page} onClick={() => update({ page: page - 1 })}>
          <ChevronLeft />
          이전
        </button>
        <span>
          {page + 1} / {pages}
        </span>
        <button
          className="secondary"
          disabled={page + 1 >= pages}
          onClick={() => update({ page: page + 1 })}
        >
          다음
          <ChevronRight />
        </button>
      </div>
      <p className="table-footnote">
        담당자 검토 완료는 작업의 완료 표시입니다. 근거 자료의 사실성·충분성 판단은 별도로
        유지됩니다.
      </p>
    </>
  );
}

export function Review({
  project,
  answer,
  busy,
  onBack,
  onSelect,
  onDocuments,
  onSave,
  onDraft,
}: {
  project: Project;
  answer: Answer;
  busy: boolean;
  onBack: () => void;
  onSelect: (id: string) => void;
  onDocuments: () => void;
  onSave: (id: string, values: ReviewValues, complete?: boolean) => Promise<void>;
  onDraft: (dirty: boolean, save: () => Promise<void>, discard: () => void) => void;
}) {
  const key = `esgenie-draft:${project.id}:${answer.id}:${project.result_revision}`;
  const [values, setValues] = useState<ReviewValues>(() => {
    try {
      const v = JSON.parse(localStorage.getItem(key) || 'null');
      if (v && typeof v.answer === 'string' && Array.isArray(v.sources)) return v;
    } catch {}
    return structuredClone(answer.saved);
  });
  const [preview, setPreview] = useState<Source | undefined>(
    values.sources[0] || answer.reference_sources[0],
  );
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const dirty = !sameContent(values, answer.saved);
  const answers = project.result!.answers;
  const index = answers.findIndex((a) => a.id === answer.id);
  const change = (v: ReviewValues) => {
    setValues(v);
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {}
  };
  const save = async (complete = false) => {
    await onSave(answer.id, values, complete);
    localStorage.removeItem(key);
  };
  useEffect(() => {
    onDraft(
      dirty,
      () => save(),
      () => {
        setValues(structuredClone(answer.saved));
        localStorage.removeItem(key);
      },
    );
    return () =>
      onDraft(
        false,
        async () => {},
        () => {},
      );
  }, [dirty, values, answer.saved]);
  useEffect(() => {
    if (!dirty) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);
  const options: Candidate[] = [
    { id: 'automatic', ...answer.automatic, notices: answer.notices },
    ...answer.candidates,
  ];
  return (
    <div className="review-workspace">
      <div className="review-bar">
        <button className="back-button" onClick={onBack}>
          <ArrowLeft />
          문항 목록
        </button>
        <div className="question-position">
          <span>
            {index + 1} / {answers.length}문항
          </span>
          <button
            className="icon-button"
            aria-label="이전 문항"
            disabled={!index}
            onClick={() => onSelect(answers[index - 1].id)}
          >
            <ChevronLeft />
          </button>
          <button
            className="icon-button"
            aria-label="다음 문항"
            disabled={index + 1 === answers.length}
            onClick={() => onSelect(answers[index + 1].id)}
          >
            <ChevronRight />
          </button>
        </div>
      </div>
      <div className="review-title">
        <small>
          {answer.id} · {answer.section}
        </small>
        <h2>{answer.question}</h2>
        <Badge answer={answer} />
      </div>
      <div className="review-columns">
        <section className="answer-paper" aria-label="답변 편집">
          {answer.review_status === 'again' && (
            <div className="feedback warning">
              <p>
                {project.stale
                  ? '자료가 변경되었습니다. 기존 답변을 보관 중입니다. 재분석 후 새 근거를 확인하세요.'
                  : '재분석을 완료했습니다. 저장된 답변을 유지했습니다. 새 근거와 비교한 뒤 검토를 완료하세요.'}
              </p>
            </div>
          )}
          <div className="answer-fields">
            <label>
              답변
              <textarea
                aria-label="답변"
                value={values.answer}
                maxLength={5000}
                onChange={(e) => change({ ...values, answer: e.target.value })}
                disabled={busy || project.stale}
              />
            </label>
            <label>
              단위
              <input
                aria-label="단위"
                value={values.unit}
                maxLength={100}
                onChange={(e) => change({ ...values, unit: e.target.value })}
                disabled={busy || project.stale}
              />
            </label>
          </div>
          <label>
            측정 범위 / 기준
            <input
              aria-label="측정 범위"
              value={values.scope}
              maxLength={2000}
              placeholder="기간 · 사업장 · 대상 · 집계 기준"
              onChange={(e) => change({ ...values, scope: e.target.value })}
              disabled={busy || project.stale}
            />
          </label>
          <div className="connected-sources">
            <h3>답변에 연결된 근거</h3>
            {values.sources.length ? (
              values.sources.map((s, i) => (
                <div key={i}>
                  <button
                    className="source-link"
                    onClick={() => {
                      setPreview(s);
                      setCandidate(null);
                    }}
                  >
                    {s.name} · {s.page !== null ? `${s.page + 1}쪽` : '페이지 미확인'} · 버전{' '}
                    {s.version || 1}
                  </button>
                  <button
                    className="text-button"
                    aria-label={`${s.name} 근거 해제`}
                    disabled={busy || project.stale}
                    onClick={() =>
                      change({ ...values, sources: values.sources.filter((_, j) => j !== i) })
                    }
                  >
                    해제
                  </button>
                </div>
              ))
            ) : (
              <p>연결된 근거 없음</p>
            )}
          </div>
          <label>
            메모 / 자료 부족 사유
            <textarea
              aria-label="검토 메모"
              value={values.memo}
              maxLength={5000}
              onChange={(e) => change({ ...values, memo: e.target.value })}
              disabled={busy || project.stale}
              placeholder="추가 확인 사항이나 자료가 부족한 이유"
            />
          </label>
          <label>
            수정 이유
            <input
              aria-label="수정 이유"
              value={values.reason}
              maxLength={2000}
              onChange={(e) => change({ ...values, reason: e.target.value })}
              disabled={busy || project.stale}
            />
          </label>
          <div className="review-save">
            <button
              className="secondary"
              disabled={busy || project.stale || !dirty}
              onClick={() => void save().catch(() => {})}
            >
              저장
            </button>
            <button
              className="primary"
              disabled={busy || project.stale}
              onClick={async () => {
                try {
                  await save(true);
                } catch {
                  return;
                }
                const next = [...answers.slice(index + 1), ...answers.slice(0, index)].find(
                  (a) => a.review_status !== 'complete',
                );
                onSelect(next?.id || '');
              }}
            >
              검토 완료 후 다음
              <ArrowRight />
            </button>
          </div>
          <p className="save-status" role="status">
            {dirty
              ? '저장하지 않은 수정이 있습니다.'
              : '저장된 답변 기준 · 검토 완료는 별도 동작입니다.'}
          </p>
          <details className="more-details">
            <summary>근거 판단 · 확인 사유</summary>
            <p>
              {answer.status_label} · {answer.why}
            </p>
            <p>{answer.next_step}</p>
            {answer.notices.map((n) => (
              <p key={n}>{n}</p>
            ))}
            {answer.evidence_needed.length > 0 && (
              <p>필요 자료: {answer.evidence_needed.join(' · ')}</p>
            )}
            <button className="text-button" onClick={onDocuments}>
              자료 준비로 이동
            </button>
            <p>{questionHelp(answer).join(' ')}</p>
          </details>
          <details className="more-details">
            <summary>변경 기록 ({answer.history.length})</summary>
            {answer.history
              .slice()
              .reverse()
              .map((h, i) => (
                <div className="history-entry" key={i}>
                  <small>
                    {new Date(h.at).toLocaleString('ko-KR')} · {h.action}
                  </small>
                  <p>
                    {h.before.answer || '답변 없음'} {h.before.unit} →{' '}
                    {h.after.answer || '답변 없음'} {h.after.unit}
                  </p>
                  <p>
                    범위: {h.before.scope} → {h.after.scope}
                  </p>
                  <p>
                    근거:{' '}
                    {h.after.sources.map((s) => `${s.name} v${s.version || 1}`).join(' · ') ||
                      '없음'}
                  </p>
                  <p>수정 이유: {h.reason || '미기재'}</p>
                </div>
              ))}
          </details>
        </section>
        <aside className="evidence-panel" aria-label="원문 확인">
          <h3>원문 확인</h3>
          <p className="intro">다른 근거를 살펴보는 동안 저장된 답변은 유지됩니다.</p>
          <label>
            새 분석 값과 근거
            <select
              aria-label="새 근거 선택"
              value={candidate?.id || ''}
              onChange={(e) => {
                const c = options.find((c) => c.id === e.target.value) || null;
                setCandidate(c);
                setPreview(c?.sources[0]);
              }}
            >
              <option value="">연결된 원문 보기</option>
              {options
                .filter((c) => c.answer || c.sources.length)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.answer} {c.unit} · {c.sources[0]?.name || '근거 없음'} · {c.scope}
                  </option>
                ))}
            </select>
          </label>
          {candidate && (
            <div className="candidate">
              <strong>
                {candidate.answer} {candidate.unit}
              </strong>
              <p>{candidate.scope || '범위 미확인'}</p>
              {candidate.notices.map((n) => (
                <small key={n}>{n}</small>
              ))}
              {candidate.sources.map((s, i) => (
                <button key={i} className="source-link" onClick={() => setPreview(s)}>
                  {s.name} · {s.page !== null ? `${s.page + 1}쪽` : '페이지 미확인'}
                </button>
              ))}
              <button
                className="primary"
                disabled={busy || project.stale || !candidate.sources.length}
                onClick={() =>
                  change({
                    ...values,
                    answer: candidate.answer,
                    unit: candidate.unit,
                    scope: candidate.scope,
                    sources: structuredClone(candidate.sources),
                  })
                }
              >
                이 값과 근거 적용
              </button>
              <small>답변 값·단위·범위·근거를 함께 바꿉니다. 적용 후 저장하세요.</small>
            </div>
          )}
          {!!answer.reference_sources.length && (
            <details className="more-details">
              <summary>보완 대상 자료 · 값 산정 미사용</summary>
              {answer.reference_sources.map((s, i) => (
                <button
                  className="source-link"
                  key={i}
                  onClick={() => {
                    setPreview(s);
                    setCandidate(null);
                  }}
                >
                  {s.name}
                </button>
              ))}
            </details>
          )}
          <SourceViewer
            key={`${preview?.document_id}:${preview?.version}:${preview?.page}:${preview?.quote}`}
            project={project}
            source={preview}
            canLink={!busy && !project.stale}
            onLink={(source) => {
              if (
                !values.sources.some(
                  (s) =>
                    s.document_id === source.document_id &&
                    s.version === source.version &&
                    s.page === source.page,
                )
              )
                change({ ...values, sources: [...values.sources, source] });
            }}
          />
          <div className="connected-sources">
            <label>
              자료 보관함의 원문
              <select
                aria-label="다른 원문 보기"
                value=""
                onChange={(e) => {
                  const d = project.documents.find((d) => d.id === e.target.value);
                  if (d) {
                    setCandidate(null);
                    setPreview({
                      name: d.name,
                      document_id: d.id,
                      version: d.version,
                      page: 0,
                      quote: '담당자 직접 연결 · 해당 원문 페이지 확인 필요',
                      manual: true,
                      independent: d.role === 'evidence',
                      preview_available: !d.example && !d.error,
                      bbox: null,
                    });
                  }
                }}
              >
                <option value="">파일 선택</option>
                {project.documents.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} · {d.included ? '분석 포함' : '제외'}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </aside>
      </div>
    </div>
  );
}

function SourceViewer({
  project,
  source,
  canLink,
  onLink,
}: {
  project: Project;
  source?: Source;
  canLink: boolean;
  onLink: (source: Source) => void;
}) {
  const [page, setPage] = useState(source?.page ?? 0);
  const [failed, setFailed] = useState(false);
  const document = project.documents.find((d) => d.id === source?.document_id);
  const version = document?.versions?.find((v) => v.version === source?.version);
  const pages = version?.pages ?? document?.pages ?? 0;
  if (!source)
    return (
      <div className="empty-state">
        <p>연결된 원문이 없습니다. 자료를 선택하세요.</p>
      </div>
    );
  const path = `/api/projects/${project.id}/documents/${source.document_id}`;
  const query = `?version=${source.version || 1}`;
  const bbox = source.bbox;
  const valid =
    bbox?.length === 4 &&
    bbox.every((n) => Number.isFinite(n) && n >= 0 && n <= 1) &&
    bbox[2] > bbox[0] &&
    bbox[3] > bbox[1] &&
    page === source.page;
  return (
    <>
      <div className="source-title">
        <div>
          <strong>{source.name}</strong>
          <span>
            버전 {source.version || 1}
            {document && source.version !== document.version ? ' · 이전 원본' : ''} · {page + 1}쪽
          </span>
        </div>
      </div>
      {source.preview_available && !failed ? (
        <div className="original-page">
          <img
            alt={`${source.name} ${page + 1}쪽 원문`}
            src={`${path}/pages/${page}${query}`}
            onError={() => setFailed(true)}
          />
          {valid && (
            <div
              className="evidence-highlight"
              style={{
                left: `${bbox![0] * 100}%`,
                top: `${bbox![1] * 100}%`,
                width: `${(bbox![2] - bbox![0]) * 100}%`,
                height: `${(bbox![3] - bbox![1]) * 100}%`,
              }}
            />
          )}
        </div>
      ) : (
        <p className="inline-warning">
          {project.mode === 'example'
            ? '사용법 예시 · 실제 원본이 없습니다.'
            : failed
              ? '원문을 표시하지 못했습니다. 원본을 내려받아 확인하세요.'
              : '원문 페이지를 확인할 수 없습니다.'}
        </p>
      )}
      {source.manual && source.preview_available && (
        <button
          className="secondary"
          disabled={!canLink}
          onClick={() => onLink({ ...source, page })}
        >
          이 페이지를 근거로 연결
        </button>
      )}
      {!!source.quote && (
        <details className="more-details" open>
          <summary>읽은 원문</summary>
          <p className="source-quote">{source.quote}</p>
        </details>
      )}
      {source.preview_available && (
        <div className="pagination">
          <button className="secondary" disabled={!page} onClick={() => setPage(page - 1)}>
            이전 쪽
          </button>
          <span>
            {page + 1} / {pages}
          </span>
          <button
            className="secondary"
            disabled={page + 1 >= pages}
            onClick={() => setPage(page + 1)}
          >
            다음 쪽
          </button>
          <a className="text-button" href={`${path}/original${query}`}>
            원본 내려받기
          </a>
        </div>
      )}
    </>
  );
}

export function Submission({
  project,
  busy,
  onDownload,
  onReview,
}: {
  project: Project;
  busy: boolean;
  onDownload: (kind: 'xlsx' | 'pdf' | 'bundle') => void;
  onReview: (id: string) => void;
}) {
  const answers = project.result?.answers || [];
  const remaining = answers.filter((a) => a.review_status !== 'complete').length;
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(answers.length / 10));
  return (
    <>
      <div className={`feedback ${project.stale ? 'warning' : 'neutral'}`}>
        <div>
          <strong>
            {project.stale
              ? '자료 변경 전의 응답서입니다'
              : remaining
                ? `검토가 남은 문항 ${remaining}개`
                : '전체 문항의 담당자 검토를 완료했습니다'}
          </strong>
          <p>
            {project.stale
              ? '재분석 전까지 내려받기를 제한합니다. 저장된 답변은 보관됩니다.'
              : '검토용 응답서는 미완료 상태와 자료 부족 사유를 포함합니다.'}
          </p>
        </div>
        <div className="download-actions">
          {[
            ['xlsx', 'Excel 내려받기'],
            ['pdf', 'PDF 내려받기'],
          ].map(([k, t]) => (
            <button
              className={k === 'xlsx' ? 'primary' : 'secondary'}
              key={k}
              disabled={
                busy ||
                project.stale ||
                !project.result ||
                ['queued', 'running'].includes(project.job.status)
              }
              onClick={() => onDownload(k as 'xlsx' | 'pdf')}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <div className="response-sheet">
        <h2>{project.company_name} ESG 응답서</h2>
        <p>
          {project.year}년 · {project.framework} · 저장된 답변 기준
        </p>
        <div role="table" className="response-table" aria-label="응답서 미리보기">
          <div role="row" className="response-head">
            {['문항', '답변', '측정 범위 / 기준', '검토 상태', '근거'].map((h) => (
              <span role="columnheader" key={h}>
                {h}
              </span>
            ))}
          </div>
          {answers.slice(page * 10, page * 10 + 10).map((a) => (
            <div role="row" key={a.id} className="response-row">
              <div role="cell">
                <small>{a.id}</small>
                <button className="question-link" onClick={() => onReview(a.id)}>
                  {a.question}
                </button>
              </div>
              <div role="cell">
                <strong>{a.value_text}</strong>
                <small>{a.method}</small>
              </div>
              <div role="cell">{a.scope_label || '범위 미확인'}</div>
              <div role="cell">
                <Badge answer={a} />
                <small>근거 판단: {a.status_label}</small>
                {a.saved.memo && <small>{a.saved.memo}</small>}
              </div>
              <div role="cell">
                {a.sources.map((s, i) => (
                  <button className="source-link" key={i} onClick={() => onReview(a.id)}>
                    {s.name}
                    <small>
                      {s.page !== null ? `${s.page + 1}쪽` : '페이지 미확인'} · 버전{' '}
                      {s.version || 1}
                    </small>
                  </button>
                ))}
                {!a.sources.length && <span>연결된 근거 없음</span>}
              </div>
            </div>
          ))}
        </div>
        <div className="pagination">
          <span>
            {page + 1} / {pages}
          </span>
          <button className="secondary" disabled={!page} onClick={() => setPage(page - 1)}>
            이전
          </button>
          <button
            className="secondary"
            disabled={page + 1 >= pages}
            onClick={() => setPage(page + 1)}
          >
            다음
          </button>
        </div>
      </div>
      <details className="more-details">
        <summary>근거 판단과 확인 사유</summary>
        {answers.map((a) => (
          <p key={a.id}>
            {a.id} · {a.status_label} · {a.why} · {a.notices.join(' · ')} · {a.saved.memo}
          </p>
        ))}
      </details>
      <button
        className="text-button"
        disabled={busy || project.stale || !project.result}
        onClick={() => onDownload('bundle')}
      >
        응답서와 근거 묶음 내려받기
      </button>
      <p className="table-footnote">
        검토 완료 표시는 담당자의 작업 진행 상태입니다. 근거의 사실성·충분성 판단을 대신하지
        않습니다.
      </p>
    </>
  );
}
