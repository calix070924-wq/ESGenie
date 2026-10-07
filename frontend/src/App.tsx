import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CircleHelp,
  FileSearch,
  Check,
  Layers3,
  FileText,
  Files,
  FolderOpen,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react';
import { api, download, lastProject, rememberProject } from './api';
import type { Company, Config, ListState, Project, ProjectSummary, ReviewValues } from './types';
import { Review, AnswerList, Submission } from './Review';
import { WorkspaceNav } from './WorkspaceNav';
import { Documents } from './Documents';

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [recent, setRecent] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);
  const [step, setStep] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [listState, setListState] = useState<ListState>({
    filter: 'all',
    search: '',
    section: 'all',
    page: 0,
  });
  const [dirty, setDirty] = useState(false);
  const draft = useRef({ dirty: false, save: async () => {}, discard: () => {} });
  const [pending, setPending] = useState<(() => void) | null>(null);
  const navigate = (action: () => void) => {
    if (draft.current.dirty) setPending(() => action);
    else action();
  };
  async function saveReview(id: string, values: ReviewValues, complete = false) {
    if (!project) return;
    setBusy(true);
    setError('');
    try {
      accept(
        await api<Project>(`/projects/${project.id}/reviews/${encodeURIComponent(id)}`, 'PUT', {
          values,
          complete,
        }),
      );
      draft.current.dirty = false;
      setMessage(
        complete
          ? '담당자 검토를 완료했습니다.'
          : '답변을 저장했습니다. 검토 완료는 별도 동작입니다.',
      );
    } catch (err) {
      setError((err as Error).message);
      throw err;
    } finally {
      setBusy(false);
    }
  }

  const processing = project?.job.status === 'queued' || project?.job.status === 'running';

  const accept = (value: Project) => {
    setProject(value);
    rememberProject(value.id);
  };
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : '작업을 마치지 못했어요. 다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  }
  async function openProject(id: string) {
    const current = await api<Project>(`/projects/${id}`);
    accept(current);
    setStep(current.result ? 2 : 1);
    setCreating(false);
    setSelected(null);
  }
  async function load() {
    const [settings, projects] = await Promise.all([
      api<Config>('/config'),
      api<ProjectSummary[]>('/projects'),
    ]);
    setConfig(settings);
    setRecent(projects);
    const previous = lastProject();
    if (previous && projects.some((p) => p.id === previous)) await openProject(previous);
  }
  useEffect(() => {
    let active = true;
    load()
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setBooting(false);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!processing || !project) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = await api<Project>(`/projects/${project.id}`);
        if (!active) return;
        setProject(current);
        if (current.job.status === 'complete') {
          setStep(2);
          setSelected(null);
          setMessage('확인할 내용을 정리했어요.');
          return;
        }
        if (current.job.status === 'failed') return;
      } catch (err) {
        if (active) setError((err as Error).message);
      }
      if (active) timer = setTimeout(poll, 1800);
    };
    timer = setTimeout(poll, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [project?.id, processing]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [project?.id, selected, step, creating]);

  const home = () =>
    act(async () => {
      setRecent(await api<ProjectSummary[]>('/projects'));
      setProject(null);
      setCreating(false);
      setSelected(null);
      rememberProject(null);
    });
  const startExample = () =>
    act(async () => {
      accept(await api<Project>('/examples', 'POST'));
      setCreating(false);
      setStep(2);
      setSelected(null);
    });
  const analyze = () =>
    act(async () => {
      if (project) accept(await api<Project>(`/projects/${project.id}/analysis`, 'POST'));
    });
  const exportFile = (kind: 'xlsx' | 'pdf' | 'bundle') =>
    act(async () => {
      if (project) {
        await download(project.id, kind);
        setMessage('파일을 준비했어요. 내려받은 파일을 확인해 주세요.');
      }
    });
  const go = (next: number) => {
    setStep(next);
    setSelected(null);
    setMessage('');
  };

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        본문으로 이동
      </a>
      <WorkspaceNav
        project={project}
        step={step}
        busy={busy || !!processing}
        creating={creating}
        onHome={() => navigate(home)}
        onCreate={() =>
          navigate(() => {
            setCreating(true);
            setSelected(null);
          })
        }
        onGo={(next) =>
          navigate(() => {
            setCreating(false);
            go(next);
          })
        }
      />
      {pending && (
        <div className="modal-backdrop">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="unsaved-title"
            className="unsaved-dialog"
          >
            <h2 id="unsaved-title">저장하지 않은 수정이 있습니다</h2>
            <p>이동하기 전에 수정 내용을 어떻게 처리할지 선택하세요.</p>
            <div>
              <button
                className="primary"
                disabled={busy}
                onClick={async () => {
                  try {
                    await draft.current.save();
                    const move = pending;
                    setPending(null);
                    move();
                  } catch {}
                }}
              >
                저장하고 이동
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  draft.current.discard();
                  draft.current.dirty = false;
                  const move = pending;
                  setPending(null);
                  move();
                }}
              >
                수정 버리고 이동
              </button>
              <button className="text-button" onClick={() => setPending(null)}>
                계속 작성
              </button>
            </div>
          </section>
        </div>
      )}
      <div className="main-shell">
        <header className="appbar">
          <div className="context-info">
            <strong>
              {creating ? '새 응답 작업' : project?.company_name || 'ESGenie 작업 홈'}
            </strong>
            {project && (
              <span>
                {project.year}년 ·{' '}
                {config?.frameworks.find((f) => f.key === project.framework)?.label ||
                  project.framework}
              </span>
            )}
          </div>
          <Help />
          <span className="save-status">
            {dirty ? '현재 문항 · 저장하지 않음' : '저장된 답변 기준'}
          </span>
        </header>
        <main id="main-content" className={`page ${selected ? 'page-review' : ''}`}>
          {error && (
            <div className="feedback error" role="alert">
              <span>{error}</span>
              <div>
                {!config && (
                  <button onClick={() => act(load)} className="text-button">
                    다시 연결
                  </button>
                )}
                <button className="icon-button" aria-label="알림 닫기" onClick={() => setError('')}>
                  <X />
                </button>
              </div>
            </div>
          )}
          {booting ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              <h1>작업 공간을 열고 있어요</h1>
            </div>
          ) : !config ? (
            <div className="empty-state">
              <h1>작업 공간에 연결하지 못했어요</h1>
              <button className="primary" onClick={() => act(load)}>
                다시 연결하기
              </button>
            </div>
          ) : creating ? (
            <CreateProject
              config={config}
              busy={busy}
              onBack={() => setCreating(false)}
              onCreate={(values) =>
                act(async () => {
                  accept(await api<Project>('/projects', 'POST', values));
                  setCreating(false);
                  setStep(1);
                })
              }
            />
          ) : !project ? (
            <Welcome
              recent={recent}
              busy={busy}
              onCreate={() => setCreating(true)}
              onExample={startExample}
              onOpen={(id) => act(() => openProject(id))}
            />
          ) : (
            <>
              {project.mode === 'example' && (
                <div className="example-banner">
                  <Sparkles />
                  <span>
                    <strong>사용법 예시</strong> · 대표 질문 3개를 살펴보는 화면이에요. 실제 회사
                    분석 결과가 아니에요.
                  </span>
                  <button className="text-button" onClick={() => navigate(() => setCreating(true))}>
                    내 회사로 시작
                    <ArrowRight />
                  </button>
                </div>
              )}
              <div className="project-heading">
                <div>
                  <h1>
                    {step === 1
                      ? '자료 준비'
                      : step === 3
                        ? '응답서'
                        : selected
                          ? '답변 검토'
                          : '답변 검토'}
                  </h1>
                  {!selected && (
                    <p>
                      {step === 1
                        ? '분석할 자료와 회사 작성 답변을 정리하세요.'
                        : step === 3
                          ? '저장한 답변·측정 범위·검토 상태·근거를 확인하세요.'
                          : '문항을 열어 저장된 답변과 실제 원문을 함께 확인하세요.'}
                    </p>
                  )}
                </div>
                {project.result && step !== 3 && (
                  <button className="secondary" onClick={() => navigate(() => go(3))}>
                    응답서 미리보기
                  </button>
                )}
              </div>
              {project.stale && (
                <div className="feedback warning" role="status">
                  <FileSearch />
                  <div>
                    <strong>서류나 회사 정보가 바뀌었어요.</strong>
                    <p>아래 답변은 변경 전 결과예요. 새 내용으로 다시 준비해 주세요.</p>
                  </div>
                  <button
                    className="secondary"
                    onClick={analyze}
                    disabled={busy || processing || !config.analysis_available}
                  >
                    새 자료로 다시 준비
                  </button>
                </div>
              )}
              {processing && (
                <div className="feedback processing" role="status">
                  <LoaderCircle className="spin" />
                  <div>
                    <strong>{project.job.stage}</strong>
                    <p>파일에 따라 시간이 걸릴 수 있어요. 결과가 준비되면 알려드릴게요.</p>
                  </div>
                </div>
              )}
              {project.job.status === 'failed' && (
                <div className="feedback error" role="alert">
                  <span>{project.job.error}</span>
                  <button
                    className="secondary"
                    onClick={analyze}
                    disabled={busy || !config.analysis_available}
                  >
                    다시 시도
                  </button>
                </div>
              )}
              {step === 1 ? (
                <Documents
                  project={project}
                  config={config}
                  busy={busy || processing}
                  onUpdate={accept}
                  act={act}
                  onAnalyze={analyze}
                  onReview={() => go(2)}
                />
              ) : selected && project.result ? (
                <Review
                  key={selected}
                  project={project}
                  answer={project.result.answers.find((a) => a.id === selected)!}
                  busy={busy || processing}
                  onBack={() => navigate(() => setSelected(null))}
                  onSelect={(id) => navigate(() => setSelected(id || null))}
                  onDocuments={() => navigate(() => go(1))}
                  onSave={saveReview}
                  onDraft={(dirty, save, discard) => {
                    draft.current = { dirty, save, discard };
                    setDirty(dirty);
                  }}
                />
              ) : (
                <div className="workspace-grid">
                  <section>
                    {step === 2 ? (
                      <AnswerList
                        project={project}
                        state={listState}
                        onState={setListState}
                        onSelect={setSelected}
                        onDocuments={() => go(1)}
                      />
                    ) : (
                      <Submission
                        project={project}
                        busy={busy || processing}
                        onDownload={exportFile}
                        onReview={(id) => {
                          setStep(2);
                          setSelected(id);
                        }}
                      />
                    )}
                  </section>
                </div>
              )}
              <footer className="project-footer">
                <span>
                  <ShieldCheck />
                  답변과 연결된 자료, 아직 확인하지 못한 내용을 함께 남깁니다.
                </span>
                <span>{project.mode === 'example' ? '사용법 예시' : '이 컴퓨터에 작업 저장'}</span>
              </footer>
            </>
          )}
          <div className="save-feedback" role="status" aria-live="polite">
            {message}
          </div>
        </main>
      </div>
    </div>
  );
}

function Welcome({
  recent,
  busy,
  onCreate,
  onExample,
  onOpen,
}: {
  recent: ProjectSummary[];
  busy: boolean;
  onCreate: () => void;
  onExample: () => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="welcome">
      <div className="home-heading">
        <div>
          <p className="eyebrow">YOUR SUPPLIER WORKSPACE</p>
          <h1>실사 응답, 근거부터 차근차근.</h1>
        </div>
        <span className="edition-label">ESG RESPONSE WORKSPACE</span>
      </div>
      <section className="welcome-hero">
        <div className="welcome-copy">
          <span className="hero-kicker">
            <span />
            자동차 공급업체를 위한 실사 준비
          </span>
          <h2>
            답변은 빠르게.
            <br />
            근거는 <span>분명하게.</span>
          </h2>
          <p className="welcome-description">
            고객사에 보낼 답변, 어디서부터 시작할지 막막하다면.
            <br />
            우리 회사 서류에서 초안을 찾고,
            <br />
            다른 값과 부족한 자료를 제출 전에 확인하세요.
          </p>
          <div className="welcome-actions">
            <button className="primary" onClick={onCreate} disabled={busy}>
              우리 회사로 시작하기
              <ArrowRight />
            </button>
            <button className="text-button" onClick={onExample} disabled={busy}>
              예시로 먼저 둘러보기
              <ArrowUpRight />
            </button>
          </div>
          <div className="hero-assurance">
            <Check />
            응답 초안
            <Check />
            원문 근거
            <Check />
            보완할 내용
          </div>
        </div>
        <div className="hero-preview" aria-label="답변과 근거 비교 예시">
          <div className="preview-caption">
            <span className="preview-live-dot" />
            답변과 근거를 나란히<span>사용법 예시</span>
          </div>
          <div className="preview-document">
            <div className="mini-document-head">
              <FileText />
              <span>환경 · 폐기물</span>
              <span className="badge review">범위 확인 필요</span>
            </div>
            <h3>폐기물 재활용 비율</h3>
            <div className="preview-values">
              <div>
                <span>직접 작성한 답변</span>
                <strong>
                  92<small>%</small>
                </strong>
                <p>대상 기간 미확인</p>
              </div>
              <ArrowRight />
              <div>
                <span>자료에서 계산한 값</span>
                <strong>
                  29.3<small>%</small>
                </strong>
                <p>일부 처리 내역</p>
              </div>
            </div>
            <div className="preview-insight">
              <FileSearch />
              <p>
                두 값의 기간과 범위부터 확인하세요.
                <br />
                <strong>같은 기준인지 확인하기 전에는 오류로 단정하지 않습니다.</strong>
              </p>
            </div>
          </div>
          <div className="preview-source">
            <span className="source-file-icon">
              <Files />
            </span>
            <div>
              <strong>폐기물 처리 내역.pdf</strong>
              <span>답변에 연결된 자료 · 예시</span>
            </div>
            <span className="source-line" />
            <Check />
          </div>
          <span className="preview-footnote">가상 자료로 구성한 화면입니다.</span>
        </div>
      </section>
      <section className="journey" aria-label="사용 순서">
        {[
          [FolderOpen, '01', '서류를 모으고', '고지서·규정집·기존 답변을 올리세요.'],
          [FileSearch, '02', '근거를 확인하고', '값, 기간, 범위를 원문과 비교하세요.'],
          [Layers3, '03', '응답서를 준비하세요', '초안과 남은 확인 사항을 함께 받으세요.'],
        ].map(([Icon, n, title, body]) => {
          const JourneyIcon = Icon as typeof FolderOpen;
          return (
            <div key={String(n)}>
              <span className="journey-icon">
                <JourneyIcon />
              </span>
              <div>
                <span className="journey-number">{String(n)}</span>
                <h3>{String(title)}</h3>
                <p>{String(body)}</p>
              </div>
            </div>
          );
        })}
      </section>
      <section className="recent">
        <div className="section-heading">
          <h2>이어서 할 작업</h2>
          <span>이 컴퓨터에 저장된 작업 · {recent.length}개</span>
        </div>
        {recent.length ? (
          <div className="recent-list">
            {recent.slice(0, 6).map((item) => (
              <button
                key={item.id}
                className="recent-item"
                onClick={() => onOpen(item.id)}
                disabled={busy}
              >
                <span className="recent-icon">
                  <FolderOpen />
                </span>
                <span>
                  <strong>{item.company_name}</strong>
                  <small>
                    {item.year}년 · {item.mode === 'example' ? '사용법 예시' : '회사 자료'}
                  </small>
                </span>
                <span className="recent-date">
                  {new Date(item.updated_at).toLocaleDateString('ko-KR')}
                </span>
                <ArrowUpRight />
              </button>
            ))}
          </div>
        ) : (
          <div className="recent-empty">
            <FolderOpen />
            <div>
              <strong>첫 응답 작업을 시작해 보세요.</strong>
              <p>올린 자료와 검토 기록을 저장해, 다음에 이어갈 수 있습니다.</p>
            </div>
            <button className="text-button" onClick={onCreate} disabled={busy}>
              새 작업 만들기
              <Plus />
            </button>
          </div>
        )}
      </section>
      <footer className="home-footer">
        <span>한 번의 답변에도, 확인할 수 있는 근거를.</span>
        <span>ESGenie · 공급망 ESG 실사 준비</span>
      </footer>
    </div>
  );
}

function CreateProject({
  config,
  busy,
  onBack,
  onCreate,
}: {
  config: Config;
  busy: boolean;
  onBack: () => void;
  onCreate: (values: Company) => void;
}) {
  const [values, setValues] = useState<Company>({
    company_name: '',
    year: new Date().getFullYear(),
    industry: '자동차부품',
    framework: 'hmc',
  });
  return (
    <div className="create-page">
      <button className="back-button" onClick={onBack}>
        <ArrowLeft />
        돌아가기
      </button>
      <p className="eyebrow">새 작업 시작</p>
      <h1>회사 이름부터 알려주세요.</h1>
      <p className="intro">
        회사의 자료 연도와 준비할 참고양식을 선택하세요. 고객사 공식 질문지와는 별도로 대조가
        필요합니다.
      </p>
      <form
        className="company-form"
        onSubmit={(event) => {
          event.preventDefault();
          onCreate(values);
        }}
      >
        <label>
          회사명
          <input
            name="company_name"
            autoComplete="organization"
            value={values.company_name}
            maxLength={100}
            onChange={(event) => setValues({ ...values, company_name: event.target.value })}
            placeholder="예: 한울정밀"
            required
          />
        </label>
        <div className="form-pair">
          <label>
            어느 해의 자료인가요?
            <input
              type="number"
              min={2000}
              max={2100}
              value={values.year}
              onChange={(event) => setValues({ ...values, year: Number(event.target.value) })}
              required
            />
            <small>준비하려는 자료의 연도를 골라 주세요.</small>
          </label>
          <label>
            회사 업종
            <select
              value={values.industry}
              onChange={(event) => setValues({ ...values, industry: event.target.value })}
            >
              <option value="기타">잘 모르겠어요 / 기타</option>
              {['자동차부품', '전자부품', '화학', '금속가공', '식품'].map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
            <small>정확히 몰라도 시작할 수 있어요.</small>
          </label>
        </div>
        <label>
          무엇을 준비하시나요?
          <select
            value={values.framework}
            onChange={(event) => setValues({ ...values, framework: event.target.value })}
          >
            {config.frameworks.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          <small>
            {config.frameworks.find((item) => item.key === values.framework)?.description}
          </small>
        </label>
        <button type="submit" className="primary" disabled={busy || !values.company_name.trim()}>
          {busy ? <LoaderCircle className="spin" /> : null}서류 올리러 가기
          <ArrowRight />
        </button>
      </form>
    </div>
  );
}

function Help() {
  return (
    <details className="help">
      <summary>
        <CircleHelp />
        <span>쉬운 설명</span>
      </summary>
      <div className="help-content">
        <h2>처음이어도 괜찮아요</h2>
        <dl>
          <dt>실사 응답서가 뭔가요?</dt>
          <dd>
            고객사가 우리 회사의 환경 관리, 직원 보호, 운영 방식에 대해 묻는 질문에 답하는 서류예요.
          </dd>
          <dt>근거 자료는 뭔가요?</dt>
          <dd>답변이 맞는지 확인할 수 있는 고지서, 계약서, 사내 규정 같은 서류예요.</dd>
          <dt>자료가 없으면요?</dt>
          <dd>지금 아는 내용과 메모를 저장하고, 서류를 찾은 뒤 이어갈 수 있어요.</dd>
          <dt>저장하면 확인이 끝나나요?</dt>
          <dd>
            저장은 기록을 보관하는 일이에요. 자료로 확인되지 않은 답변은 확인 전 상태로 유지해요.
          </dd>
        </dl>
      </div>
    </details>
  );
}
