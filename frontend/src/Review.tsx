import { useEffect, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  FileQuestion,
  FileSearch,
  FileText,
  Image,
  Info,
  LoaderCircle,
  NotebookPen,
  Package,
  Plus,
  Search,
  TriangleAlert,
} from 'lucide-react';
import type { Answer, Project, Source } from './types';
import { questionHelp } from './questionHelp';

export function Badge({ answer }: { answer: Answer }) {
  return (
    <span className={`badge ${answer.status}`}>
      {answer.status === 'linked' ? (
        <Check />
      ) : answer.status === 'review' ? (
        <TriangleAlert />
      ) : null}
      {answer.status_label}
    </span>
  );
}

export function AnswerList({
  project,
  onSelect,
  onDocuments,
}: {
  project: Project;
  onSelect: (id: string) => void;
  onDocuments: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const answers = project.result?.answers || [];
  const attention = answers.filter((answer) => answer.needs_attention);
  const filtered = (showAll ? answers : attention).filter((answer) =>
    `${answer.question} ${answer.section} ${answer.why} ${answer.evidence_needed.join(' ')}`.includes(
      search.trim(),
    ),
  );
  const visible = filtered.slice(page * 6, page * 6 + 6);
  useEffect(() => setPage(0), [search, showAll, project.result_revision]);
  if (!project.result)
    return (
      <div className="empty-state">
        <FileSearch />
        <h2>서류를 읽으면 질문별 안내가 생겨요</h2>
        <p>지금 가지고 있는 자료를 한 개 이상 올려 주세요.</p>
        <button className="primary" onClick={onDocuments}>
          서류 올리기
          <ArrowRight />
        </button>
      </div>
    );
  return (
    <>
      <div className="section-heading">
        <h2>{showAll ? '질문별 답변' : '지금 확인할 내용'}</h2>
        <span>{showAll ? answers.length : attention.length}개 항목</span>
      </div>
      <p className="intro">
        {attention.length
          ? '모르는 질문도 괜찮아요. 필요한 이유와 자료를 하나씩 알려드릴게요.'
          : '자료와 연결된 답변을 읽고, 회사 상황과 맞는지 확인해 주세요.'}
      </p>
      <div className="list-tools">
        <div className="list-tabs" aria-label="질문 보기">
          <button aria-pressed={!showAll} onClick={() => setShowAll(false)}>
            확인할 내용 {attention.length}
          </button>
          <button aria-pressed={showAll} onClick={() => setShowAll(true)}>
            전체 답변 {answers.length}
          </button>
        </div>
        {answers.length > 6 && (
          <label className="question-search">
            <Search />
            <span className="sr-only">질문 찾기</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="찾고 싶은 말 · 전기, 안전"
            />
          </label>
        )}
      </div>
      <ul className="answer-list">
        {visible.map((answer) => (
          <li key={answer.id}>
            <span className={`answer-symbol ${answer.status}`}>
              {answer.status === 'linked' ? (
                <Check />
              ) : answer.status === 'missing' ? (
                <FileQuestion />
              ) : answer.status === 'write' ? (
                <NotebookPen />
              ) : (
                <FileSearch />
              )}
            </span>
            <div>
              <div className="answer-overline">
                <span>{answer.section}</span>
                <Badge answer={answer} />
              </div>
              <h3>{answer.question}</h3>
              <p>{answer.why}</p>
              {project.notes[answer.id] && (
                <span className="note-present">
                  <NotebookPen />
                  {project.notes[answer.id].revision === project.result_revision
                    ? '작성한 내용 있음'
                    : '이전 분석 기록 · 다시 확인해 주세요'}
                </span>
              )}
            </div>
            <button
              className="text-button"
              onClick={() => onSelect(answer.id)}
              aria-label={`${answer.question} 살펴보기`}
            >
              살펴보기
              <ArrowUpRight />
            </button>
          </li>
        ))}
      </ul>
      {!filtered.length && (
        <div className="empty-list">
          <CircleHelp />
          <p>
            {search
              ? '이 말과 관련된 질문을 찾지 못했어요. 다른 말로 찾아보세요.'
              : '이 목록에는 남은 항목이 없어요. 전체 답변을 살펴보세요.'}
          </p>
        </div>
      )}
      {filtered.length > 6 && (
        <div className="pagination">
          <span>
            {page * 6 + 1}–{Math.min(page * 6 + 6, filtered.length)} / {filtered.length}개
          </span>
          <button
            className="icon-button"
            aria-label="이전 질문 목록"
            disabled={!page}
            onClick={() => setPage(page - 1)}
          >
            <ChevronLeft />
          </button>
          <button
            className="icon-button"
            aria-label="다음 질문 목록"
            disabled={(page + 1) * 6 >= filtered.length}
            onClick={() => setPage(page + 1)}
          >
            <ChevronRight />
          </button>
        </div>
      )}
      {project.result.limitations.length > 0 && (
        <details className="more-details limitations">
          <summary>이번 자료에서 더 확인할 내용</summary>
          <ul>
            {project.result.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

type NoteValues = { answer: string; text: string };
type ReviewProps = {
  project: Project;
  answer: Answer;
  busy: boolean;
  onBack: () => void;
  onSelect: (id: string) => void;
  onDocuments: () => void;
  onSave: (id: string, values: NoteValues) => Promise<void>;
};
export function Review({
  project,
  answer,
  busy,
  onBack,
  onSelect,
  onDocuments,
  onSave,
}: ReviewProps) {
  const saved = project.notes[answer.id];
  const draftKey = `esgenie-draft:${project.id}:${answer.id}:${project.result_revision}`;
  const [values, setValues] = useState<NoteValues>(() => {
    try {
      const local = localStorage.getItem(draftKey);
      if (local) {
        const parsed = JSON.parse(local);
        if (typeof parsed.answer === 'string' && typeof parsed.text === 'string') return parsed;
      }
    } catch {
      /* Keep server copy when local storage is unavailable. */
    }
    return { answer: saved?.answer || '', text: saved?.text || '' };
  });
  const dirty = values.answer !== (saved?.answer || '') || values.text !== (saved?.text || '');
  const answers = project.result!.answers;
  const index = answers.findIndex((item) => item.id === answer.id);
  const change = (next: NoteValues) => {
    setValues(next);
    try {
      localStorage.setItem(draftKey, JSON.stringify(next));
    } catch {
      /* Server save remains available. */
    }
  };
  useEffect(() => {
    if (!dirty) {
      try {
        localStorage.removeItem(draftKey);
      } catch {
        /* Optional local draft. */
      }
    }
  }, [dirty, draftKey]);
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);
  return (
    <div className="review-workspace">
      <div className="review-bar">
        <button className="back-button" onClick={onBack}>
          <ArrowLeft />
          확인할 내용
        </button>
        <div className="question-position">
          <span>
            {index + 1} / {answers.length}개 질문
          </span>
          <button
            className="icon-button"
            aria-label="이전 질문"
            disabled={index === 0}
            onClick={() => onSelect(answers[index - 1].id)}
          >
            <ChevronLeft />
          </button>
          <button
            className="icon-button"
            aria-label="다음 질문"
            disabled={index === answers.length - 1}
            onClick={() => onSelect(answers[index + 1].id)}
          >
            <ChevronRight />
          </button>
        </div>
      </div>
      <div className="review-title">
        <span className="eyebrow">{answer.section}</span>
        <h2>{answer.question}</h2>
        <p>{answer.why}</p>
        <details className="question-help">
          <summary>이 질문은 어떤 뜻인가요?</summary>
          {questionHelp(answer).map((explanation) => (
            <p key={explanation}>{explanation}</p>
          ))}
        </details>
      </div>
      <div className="review-columns">
        <section className="answer-paper" aria-label="답변 검토">
          <div className="section-heading">
            <h3>현재 답변</h3>
            <Badge answer={answer} />
          </div>
          <div className="answer-value">{answer.draft_text || answer.value_text}</div>
          {answer.period_label && (
            <div className="period-label">자료 연도 · {answer.period_label}</div>
          )}
          {answer.company_answers.length > 0 && (
            <div className="claim-comparison">
              <div>
                <span>회사가 직접 적은 답변</span>
                {answer.company_answers.map((claim, i) => (
                  <strong key={i}>
                    {claim.value ?? '확인 필요'} {claim.unit}
                  </strong>
                ))}
              </div>
              <div>
                <span>현재 자료로 찾은 답변</span>
                <strong>{answer.value_text}</strong>
              </div>
            </div>
          )}
          {answer.notices.length > 0 && (
            <ul className="answer-notices">
              {answer.notices.map((notice) => (
                <li key={notice}>
                  <Info />
                  <span>{notice}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="next-help">
            <span className="next-help-title">이렇게 확인해 보세요</span>
            <p>{answer.next_step}</p>
            {answer.evidence_needed.length > 0 && (
              <>
                <span className="needed-label">찾아보면 좋은 서류</span>
                <ul>
                  {answer.evidence_needed.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                {project.mode !== 'example' && (
                  <button className="text-button" onClick={onDocuments}>
                    서류 추가하기
                    <Plus />
                  </button>
                )}
              </>
            )}
          </div>
          {saved && saved.revision !== project.result_revision && (
            <p className="inline-warning">
              이전 분석에서 작성한 내용이에요. 새 자료와 맞는지 다시 확인해 주세요.
            </p>
          )}
          <details className="manual-answer" open={answer.status === 'write' || !!values.answer}>
            <summary>직접 답변을 적거나 보완하기</summary>
            <label>
              직접 작성한 답변 <span className="optional">자료 확인 전</span>
              <textarea
                value={values.answer}
                maxLength={5000}
                onChange={(event) => change({ ...values, answer: event.target.value })}
                placeholder="예: 담당자는 생산팀이며, 매월 사용 내역을 확인합니다."
                disabled={busy || project.stale}
              />
              <small>자동으로 만든 답변과 구분해 보관하고, 내려받는 파일에도 따로 표시해요.</small>
            </label>
          </details>
          <label className="review-note-label">
            확인한 내용 메모 <span className="optional">선택</span>
            <textarea
              value={values.text}
              maxLength={5000}
              onChange={(event) => change({ ...values, text: event.target.value })}
              placeholder="예: 회계팀에 지난해 전기요금 고지서 요청하기"
              disabled={busy || project.stale}
            />
          </label>
          <div className="review-save">
            <button
              className="primary"
              disabled={
                busy ||
                project.stale ||
                (!dirty && (!saved || saved.revision === project.result_revision))
              }
              onClick={() => onSave(answer.id, values)}
            >
              {busy ? <LoaderCircle className="spin" /> : <Check />}작성 내용 저장
            </button>
            <span>
              {dirty
                ? '아직 저장하지 않은 내용이 있어요.'
                : saved
                  ? '저장됨 · 확인 상태는 유지돼요.'
                  : '모르면 비워두고 나중에 이어가세요.'}
            </span>
          </div>
          <details className="more-details">
            <summary>질문의 기준과 자세한 확인 내용</summary>
            <p>질문 번호: {answer.id}</p>
            {answer.technical_reason && <p>{answer.technical_reason}</p>}
            {answer.flags.length > 0 && (
              <ul>
                {answer.flags.map((flag, i) => (
                  <li key={i}>{flag}</li>
                ))}
              </ul>
            )}
          </details>
        </section>
        <EvidencePanel key={answer.id} project={project} answer={answer} />
      </div>
    </div>
  );
}

function EvidencePanel({ project, answer }: { project: Project; answer: Answer }) {
  const [index, setIndex] = useState(0);
  const [mode, setMode] = useState<'quote' | 'original'>('quote');
  const [imageError, setImageError] = useState(false);
  const source: Source | undefined = answer.sources[index];
  const document = project.documents.find((doc) => doc.id === source?.document_id);
  const [page, setPage] = useState(source?.page ?? 0);
  const bbox = source?.bbox;
  const validBox =
    bbox?.length === 4 &&
    bbox.every((n) => n >= 0 && n <= 1) &&
    bbox[2] > bbox[0] &&
    bbox[3] > bbox[1] &&
    page === source.page;
  return (
    <section className="evidence-panel" aria-label="답변과 연결된 자료">
      <div className="section-heading">
        <h3>답변과 연결된 자료</h3>
        <span>{answer.sources.length}개 연결</span>
      </div>
      {!source ? (
        <div className="no-evidence">
          <FileQuestion />
          <h3>아직 연결된 자료가 없어요</h3>
          <p>
            왼쪽에 안내된 서류를 찾아보세요.
            <br />
            자료가 없다면 메모를 남기고 나중에 이어갈 수 있어요.
          </p>
        </div>
      ) : (
        <>
          {answer.sources.length > 1 && (
            <label className="source-picker">
              자료 선택
              <select
                value={index}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setIndex(next);
                  setPage(answer.sources[next].page ?? 0);
                  setMode('quote');
                  setImageError(false);
                }}
              >
                {answer.sources.map((item, i) => (
                  <option key={i} value={i}>
                    {item.name}
                    {item.page === null ? '' : ` · ${item.page + 1}쪽`}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="source-title">
            <FileText />
            <div>
              <strong>{source.name}</strong>
              <span>
                {project.mode === 'example'
                  ? '사용법 예시 · 검증값 요약'
                  : source.page === null
                    ? '페이지 정보 없음'
                    : `연결 위치 · ${source.page + 1}쪽`}
              </span>
            </div>
          </div>
          <div className="source-tabs" aria-label="자료 보기">
            <button aria-pressed={mode === 'quote'} onClick={() => setMode('quote')}>
              <FileText />
              읽은 내용
            </button>
            <button
              aria-pressed={mode === 'original'}
              onClick={() => {
                setMode('original');
                setImageError(false);
              }}
              disabled={!source.preview_available}
            >
              <Image />
              원본 페이지
            </button>
          </div>
          {mode === 'quote' ? (
            <div className="source-quote">
              <span className="source-label">
                {project.mode === 'example'
                  ? '시연 결과의 알려진 값으로 구성'
                  : '연결된 원문에서 읽은 내용'}
              </span>
              <blockquote>
                {source.quote ||
                  '연결된 위치에서 텍스트를 표시하지 못했어요. 원본을 확인해 주세요.'}
              </blockquote>
              <div className="source-bottom">
                <span>
                  {source.independent ? '답변을 확인할 자료' : '직접 작성한 내용 · 독립 자료 아님'}
                </span>
                {source.preview_available && (
                  <button className="text-button" onClick={() => setMode('original')}>
                    원본과 비교
                    <ArrowUpRight />
                  </button>
                )}
              </div>
            </div>
          ) : (
            <>
              {document && document.pages > 1 && (
                <div className="page-controls">
                  <button
                    className="icon-button"
                    aria-label="원본 이전 페이지"
                    disabled={page === 0}
                    onClick={() => {
                      setPage(page - 1);
                      setImageError(false);
                    }}
                  >
                    <ChevronLeft />
                  </button>
                  <span>
                    {page + 1} / {document.pages}쪽
                  </span>
                  <button
                    className="icon-button"
                    aria-label="원본 다음 페이지"
                    disabled={page >= document.pages - 1}
                    onClick={() => {
                      setPage(page + 1);
                      setImageError(false);
                    }}
                  >
                    <ChevronRight />
                  </button>
                </div>
              )}
              {imageError ? (
                <div className="no-evidence">
                  <p>원본 페이지를 표시하지 못했어요.</p>
                </div>
              ) : (
                <div className="source-image">
                  <img
                    src={`/api/projects/${project.id}/documents/${source.document_id}/pages/${page}`}
                    alt={`${source.name} ${page + 1}쪽`}
                    onError={() => setImageError(true)}
                  />
                  {validBox && (
                    <span
                      className="source-highlight"
                      aria-label="관련 내용 위치"
                      style={{
                        left: `${bbox![0] * 100}%`,
                        top: `${bbox![1] * 100}%`,
                        width: `${(bbox![2] - bbox![0]) * 100}%`,
                        height: `${(bbox![3] - bbox![1]) * 100}%`,
                      }}
                    />
                  )}
                </div>
              )}
            </>
          )}
          {source.document_id && project.mode !== 'example' && (
            <a
              className="text-button original-download"
              href={`/api/projects/${project.id}/documents/${source.document_id}/original`}
            >
              <ArrowDownToLine />
              원본 파일 받기
            </a>
          )}
          {project.mode === 'example' && (
            <p className="source-example-note">
              예시에는 원본 파일이 포함되어 있지 않아요. 회사 서류를 올리면 연결된 페이지를 볼 수
              있어요.
            </p>
          )}
        </>
      )}
      {answer.company_answers.length > 0 && (
        <details className="company-claims">
          <summary>회사가 직접 적은 답변 보기</summary>
          {answer.company_answers.map((claim, i) => (
            <div key={i}>
              <span className="badge unconfirmed">직접 작성한 답변</span>
              <p>{claim.raw || `${claim.value ?? '확인 필요'} ${claim.unit || ''}`}</p>
              <small>{claim.source?.replace(/^saq:/, '')}</small>
            </div>
          ))}
          <p className="intro">회사의 답변은 다른 서류와 비교해서 확인해요.</p>
        </details>
      )}
    </section>
  );
}

export function Submission({
  project,
  busy,
  onDownload,
  onSelect,
}: {
  project: Project;
  busy: boolean;
  onDownload: (kind: 'xlsx' | 'bundle') => void;
  onSelect: (id: string) => void;
}) {
  if (!project.result)
    return (
      <div className="empty-state">
        <h2>서류를 읽은 뒤 응답서를 받을 수 있어요</h2>
      </div>
    );
  const attention = project.result.answers.filter((answer) => answer.needs_attention).length;
  return (
    <>
      <div className="section-heading">
        <h2>응답서 초안이 준비되어 있어요</h2>
        <span className="badge neutral">
          {project.mode === 'example' ? '사용법 예시' : '검토용 초안'}
        </span>
      </div>
      <p className="intro">
        {attention
          ? `확인할 항목 ${attention}개가 남아 있어요. 아직 확인하지 못한 내용도 함께 표시해서 받아보세요.`
          : '제출 전에 답변과 근거가 회사 상황에 맞는지 한 번 더 확인해 주세요.'}
      </p>
      <div className="download-actions">
        <button
          className="primary"
          disabled={busy || project.stale}
          onClick={() => onDownload('xlsx')}
        >
          {busy ? <LoaderCircle className="spin" /> : <ArrowDownToLine />}응답서 Excel 받기
        </button>
        <button
          className="secondary"
          disabled={busy || project.stale}
          onClick={() => onDownload('bundle')}
        >
          <Package />
          자료와 함께 받기
        </button>
      </div>
      <p className="download-note">
        Excel에는 직접 작성한 답변을 별도 시트로 담아요. 묶음에는 PDF, 연결된 원문, 작성 내용과 검증
        근거가 함께 들어갑니다.
      </p>
      <div className="submission-paper">
        <div className="paper-heading">
          <span>응답서 미리보기</span>
          <strong>
            {project.company_name} · {project.year}
          </strong>
          <span>{project.result.answers.length}개 질문</span>
        </div>
        {project.result.answers.map((answer, i) => (
          <section className="preview-answer" key={answer.id}>
            <div className="preview-question">
              <span>{String(i + 1).padStart(2, '0')}</span>
              <h3>{answer.question}</h3>
              <Badge answer={answer} />
            </div>
            <p>
              {answer.draft_text || answer.value_text}
              {answer.period_label && <small> · {answer.period_label}</small>}
            </p>
            {answer.notices.length > 0 && (
              <p className="preview-caution">{answer.notices.join(' ')}</p>
            )}
            {project.notes[answer.id]?.answer && (
              <div className="preview-manual">
                <span>
                  직접 작성한 답변 · 확인 전
                  {project.notes[answer.id].revision !== project.result_revision
                    ? ' · 이전 분석 기록'
                    : ''}
                </span>
                <p>{project.notes[answer.id].answer}</p>
              </div>
            )}
            <button className="text-button" onClick={() => onSelect(answer.id)}>
              답변과 근거 살펴보기
              <ArrowUpRight />
            </button>
          </section>
        ))}
      </div>
    </>
  );
}
