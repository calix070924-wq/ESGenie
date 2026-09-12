import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CircleHelp,
  FileCheck2,
  FileSearch,
  FileText,
  Files,
  FolderOpen,
  LoaderCircle,
  NotebookPen,
  Plus,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  X,
} from 'lucide-react';
import { api, download, lastProject, rememberProject } from './api';
import type { Company, Config, Project, ProjectSummary } from './types';
import { Review, AnswerList, Submission } from './Review';

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
  const exportFile = (kind: 'xlsx' | 'bundle') =>
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
  const updateProject = (next: Project) => {
    accept(next);
    setMessage('저장했어요. 확인 상태는 그대로 유지됩니다.');
  };

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        본문으로 이동
      </a>
      <header className="appbar">
        <button
          type="button"
          className="brand"
          onClick={home}
          disabled={busy}
          aria-label="ESGenie 시작 화면"
        >
          <span className="brand-mark">E</span>ESGenie
        </button>
        <span className="appbar-divider" />
        <span className="appbar-caption">서류에서 답을 찾는 실사 준비</span>
        <div className="appbar-actions">
          <Help />
          {project && (
            <button
              className="quiet"
              onClick={() => {
                setCreating(true);
                setSelected(null);
              }}
              disabled={busy || processing}
            >
              <Plus />새 작업
            </button>
          )}
        </div>
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
                  <strong>사용법 예시</strong> · 대표 질문 3개를 살펴보는 화면이에요. 실제 회사 분석
                  결과가 아니에요.
                </span>
                <button className="text-button" onClick={() => setCreating(true)}>
                  내 회사로 시작
                  <ArrowRight />
                </button>
              </div>
            )}
            <div className="project-heading">
              <div>
                <p className="eyebrow">
                  {project.year} ·{' '}
                  {project.industry === '기타' ? '회사 실사 준비' : project.industry}
                </p>
                <h1>
                  {project.company_name}의<br className="mobile-break" /> 실사 응답 준비
                </h1>
                <p>지금 가진 서류부터 시작하세요. 모르는 내용은 나중에 채워도 괜찮아요.</p>
              </div>
              {project.result && (
                <button className="secondary" onClick={() => go(3)} disabled={busy}>
                  응답서 보기
                  <ArrowUpRight />
                </button>
              )}
            </div>
            <nav className="steps" aria-label="실사 응답 준비 단계">
              {[
                ['자료 올리기', '가지고 있는 서류부터'],
                ['내용 확인하기', '필요한 부분만 하나씩'],
                ['응답서 받기', '근거와 메모까지 함께'],
              ].map(([label, help], index) => (
                <button
                  key={label}
                  aria-current={step === index + 1 ? 'step' : undefined}
                  onClick={() => go(index + 1)}
                  disabled={index > 0 && !project.result}
                >
                  <span className="step-number">0{index + 1}</span>
                  <span>
                    {label}
                    <small>{help}</small>
                  </span>
                  <ArrowRight />
                </button>
              ))}
            </nav>
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
                onMessage={setMessage}
              />
            ) : selected && project.result ? (
              <Review
                key={selected}
                project={project}
                answer={project.result.answers.find((a) => a.id === selected)!}
                busy={busy || processing}
                onBack={() => setSelected(null)}
                onSelect={setSelected}
                onDocuments={() => go(1)}
                onSave={(id, values) =>
                  act(async () =>
                    updateProject(
                      await api<Project>(
                        `/projects/${project.id}/notes/${encodeURIComponent(id)}`,
                        'PUT',
                        values,
                      ),
                    ),
                  )
                }
              />
            ) : (
              <div className="workspace-grid">
                <section>
                  {step === 2 ? (
                    <AnswerList
                      project={project}
                      onSelect={setSelected}
                      onDocuments={() => go(1)}
                    />
                  ) : (
                    <Submission
                      project={project}
                      busy={busy || processing}
                      onDownload={exportFile}
                      onSelect={(id) => {
                        setStep(2);
                        setSelected(id);
                      }}
                    />
                  )}
                </section>
                <PackageSummary project={project} onPreview={() => go(3)} />
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
      <section className="welcome-copy">
        <p className="eyebrow">ESG, 처음이어도 괜찮아요</p>
        <h1>
          가지고 있는 서류로,
          <br />
          답변 준비를 시작하세요.
        </h1>
        <p className="welcome-description">
          고객사가 보낸 질문이 어렵게 느껴지나요?
          <br />
          서류에서 답을 찾고, 확인이 필요한 부분을 쉬운 말로 안내해 드려요.
        </p>
        <div className="welcome-actions">
          <button className="primary" onClick={onCreate} disabled={busy}>
            우리 회사로 시작하기
            <ArrowRight />
          </button>
          <button className="text-button" onClick={onExample} disabled={busy}>
            {busy ? <LoaderCircle className="spin" /> : <Sparkles />}예시로 먼저 둘러보기
          </button>
        </div>
        <p className="welcome-note">
          전기요금 고지서, 사내 규정, 고객사 질문서처럼 익숙한 서류면 돼요.
        </p>
      </section>
      <section className="welcome-route" aria-label="사용 순서">
        <div className="route-label">복잡한 용어 대신, 세 가지 순서</div>
        {[
          [UploadCloud, '서류를 올려 주세요', '어떤 질문에 쓸 수 있는지 살펴볼게요.'],
          [FileSearch, '확인할 것만 하나씩', '왜 필요한지, 무엇을 찾으면 되는지 알려드려요.'],
          [FileCheck2, '응답서로 받아보세요', '근거와 남은 확인 사항까지 함께 담아요.'],
        ].map(([Icon, title, body], i) => {
          const RouteIcon = Icon as typeof UploadCloud;
          return (
            <div className="route-row" key={String(title)}>
              <span className="route-number">0{i + 1}</span>
              <div>
                <RouteIcon />
                <h2>{String(title)}</h2>
                <p>{String(body)}</p>
              </div>
            </div>
          );
        })}
      </section>
      {recent.length > 0 && (
        <section className="recent">
          <div className="section-heading">
            <h2>이어서 할 작업</h2>
            <span>이 컴퓨터에 저장된 작업</span>
          </div>
          <div className="recent-list">
            {recent.slice(0, 6).map((item) => (
              <button
                key={item.id}
                className="recent-item"
                onClick={() => onOpen(item.id)}
                disabled={busy}
              >
                <FolderOpen />
                <span>
                  <strong>{item.company_name}</strong>
                  <small>
                    {item.year}년 · {item.mode === 'example' ? '사용법 예시' : '회사 자료'}
                  </small>
                </span>
                <ArrowUpRight />
              </button>
            ))}
          </div>
        </section>
      )}
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
    industry: '기타',
    framework: 'rba42',
  });
  return (
    <div className="create-page">
      <button className="back-button" onClick={onBack}>
        <ArrowLeft />
        돌아가기
      </button>
      <p className="eyebrow">새 작업 시작</p>
      <h1>회사 이름부터 알려주세요.</h1>
      <p className="intro">전문적인 설정은 필요 없어요. 나머지는 기본값으로 시작할 수 있어요.</p>
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

type DocumentsProps = {
  project: Project;
  config: Config;
  busy: boolean;
  onUpdate: (value: Project) => void;
  act: (work: () => Promise<void>) => Promise<void>;
  onAnalyze: () => void;
  onMessage: (value: string) => void;
};
function Documents({ project, config, busy, onUpdate, act, onAnalyze, onMessage }: DocumentsProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [values, setValues] = useState<Company>({
    company_name: project.company_name,
    year: project.year,
    industry: project.industry,
    framework: project.framework,
  });
  const example = project.mode === 'example';
  async function upload(files: FileList | null) {
    if (!files?.length || busy || example) return;
    await act(async () => {
      try {
        for (const file of Array.from(files)) {
          const form = new FormData();
          form.append('file', file);
          onUpdate(await api<Project>(`/projects/${project.id}/documents`, 'POST', form));
        }
      } finally {
        onUpdate(await api<Project>(`/projects/${project.id}`));
        if (input.current) input.current.value = '';
      }
      onMessage('서류를 올렸어요. 자료 종류를 확인한 뒤 답변 준비를 시작해 주세요.');
    });
  }
  const changeRole = (id: string, role: string) =>
    act(async () =>
      onUpdate(await api<Project>(`/projects/${project.id}/documents/${id}`, 'PATCH', { role })),
    );
  return (
    <div className="documents-grid">
      <section>
        <div className="section-heading">
          <h2>지금 가지고 있는 서류부터</h2>
          <span>{project.documents.length}개 자료</span>
        </div>
        <p className="intro">모두 준비할 필요는 없어요. 부족한 자료는 읽어본 뒤 알려드릴게요.</p>
        {!example && (
          <div
            className={`upload-area ${dragging ? 'dragging' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              void upload(event.dataTransfer.files);
            }}
          >
            <UploadCloud />
            <h3>서류를 여기에 놓아 주세요</h3>
            <p>PDF, PNG, JPG · 파일당 20MB</p>
            <input
              ref={input}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              multiple
              hidden
              onChange={(event) => void upload(event.target.files)}
            />
            <button className="secondary" onClick={() => input.current?.click()} disabled={busy}>
              {busy ? <LoaderCircle className="spin" /> : <Plus />}파일 선택하기
            </button>
          </div>
        )}
        {project.documents.length > 0 && (
          <div className="document-list">
            {project.documents.map((doc) => (
              <div className="document-row" key={doc.id}>
                <FileText />
                <div className="document-name">
                  <strong>{doc.name}</strong>
                  <small>
                    {doc.example
                      ? '사용법 예시 · 원본 파일 없음'
                      : `${doc.pages}쪽 · ${(doc.size / 1024).toFixed(0)}KB`}
                  </small>
                </div>
                <label className="document-role">
                  <span className="sr-only">{doc.name} 자료 종류</span>
                  <select
                    value={doc.role}
                    disabled={busy || example}
                    onChange={(event) => changeRole(doc.id, event.target.value)}
                  >
                    <option value="evidence">내용을 확인할 서류</option>
                    <option value="company_answer">직접 작성한 답변</option>
                  </select>
                </label>
                {!example && (
                  <button
                    className="icon-button"
                    aria-label={`${doc.name} 목록에서 빼기`}
                    disabled={busy}
                    onClick={() =>
                      act(async () =>
                        onUpdate(
                          await api<Project>(
                            `/projects/${project.id}/documents/${doc.id}`,
                            'DELETE',
                          ),
                        ),
                      )
                    }
                  >
                    <X />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="document-note">
          <ShieldCheck />
          <p>
            <strong>서류와 직접 작성한 답변을 구분해요.</strong> 고지서·규정은 내용을 확인할 자료로,
            설문에 직접 적은 답변은 확인 전 답변으로 다뤄요. 자동으로 나눈 종류가 다르면 바꿔
            주세요.
          </p>
        </div>
        {!example && (
          <>
            <div className="analysis-action">
              <button
                className="primary"
                disabled={busy || !project.documents.length || !config.analysis_available}
                onClick={onAnalyze}
              >
                {busy ? <LoaderCircle className="spin" /> : <Sparkles />}
                {project.result ? '새 내용으로 답변 다시 준비' : '서류를 읽고 답변 준비하기'}
                <ArrowRight />
              </button>
              <small>
                {!project.documents.length
                  ? '서류를 한 개 이상 올리면 시작할 수 있어요.'
                  : '서류에서 질문과 관련된 내용을 찾아 초안으로 정리해요.'}
              </small>
            </div>
            {!config.analysis_available && (
              <p className="connection-note">
                분석 연결이 아직 준비되지 않았어요. 지금은 서류를 보관하거나 시작 화면의 예시로
                사용법을 살펴볼 수 있어요.
              </p>
            )}
            <details className="company-settings">
              <summary>회사 정보 수정</summary>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void act(async () =>
                    onUpdate(await api<Project>(`/projects/${project.id}`, 'PATCH', values)),
                  );
                }}
              >
                <label>
                  회사명
                  <input
                    value={values.company_name}
                    required
                    maxLength={100}
                    onChange={(event) => setValues({ ...values, company_name: event.target.value })}
                  />
                </label>
                <label>
                  자료 연도
                  <input
                    type="number"
                    min={2000}
                    max={2100}
                    required
                    value={values.year}
                    onChange={(event) => setValues({ ...values, year: Number(event.target.value) })}
                  />
                </label>
                <label>
                  응답서 종류
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
                </label>
                <button className="secondary" disabled={busy}>
                  회사 정보 저장
                </button>
              </form>
            </details>
          </>
        )}
      </section>
      <aside className="document-guide">
        <p className="eyebrow">이런 서류가 도움이 돼요</p>
        <h2>
          어떤 서류를 올릴지
          <br />
          막막하다면
        </h2>
        {[
          ['전기·가스 고지서', '얼마나 사용했는지, 어느 기간의 자료인지 확인해요.'],
          ['취업규칙·안전·윤리 규정', '회사가 사람을 보호하고 운영하는 방법을 살펴봐요.'],
          ['고객사 질문서와 기존 답변', '회사가 직접 적은 답변과 서류 내용이 맞는지 비교해요.'],
        ].map(([title, body], i) => (
          <div className="guide-row" key={title}>
            <span>0{i + 1}</span>
            <div>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          </div>
        ))}
        <p className="guide-last">
          서류가 없는 항목은 “자료 필요”로 남겨둡니다. 답변을 임의로 채우지 않아요.
        </p>
      </aside>
    </div>
  );
}

function PackageSummary({ project, onPreview }: { project: Project; onPreview: () => void }) {
  const answers = project.result?.answers || [];
  const attention = answers.filter((a) => a.needs_attention).length;
  return (
    <aside className="package-summary">
      <div className="section-heading">
        <h2>응답서에 담길 내용</h2>
        <span className="badge neutral">검토용 초안</span>
      </div>
      {[
        [FileText, '질문별 답변', '자료로 찾은 내용과 확인 상태'],
        [Files, '연결된 근거', '답변에 사용한 서류와 위치'],
        [NotebookPen, '직접 작성한 답변·메모', '담당자가 확인하고 남긴 내용'],
      ].map(([Icon, title, body]) => {
        const ItemIcon = Icon as typeof Files;
        return (
          <div className="package-item" key={String(title)}>
            <ItemIcon />
            <div>
              <h3>{String(title)}</h3>
              <p>{String(body)}</p>
            </div>
          </div>
        );
      })}
      <div className="package-note">
        {attention
          ? `아직 확인할 항목이 ${attention}개 있어요. 초안을 받을 때도 확인 상태를 함께 표시해요.`
          : '자료가 연결된 항목도 제출 전에 회사 상황에 맞는지 읽어 주세요.'}
      </div>
      <button className="text-button" onClick={onPreview}>
        응답서 미리보기
        <ArrowUpRight />
      </button>
    </aside>
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
