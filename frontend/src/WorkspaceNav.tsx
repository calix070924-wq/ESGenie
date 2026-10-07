import { Plus, Sprout } from 'lucide-react';
import type { Project } from './types';
export function WorkspaceNav({
  project,
  step,
  busy,
  creating,
  onHome,
  onCreate,
  onGo,
}: {
  project: Project | null;
  step: number;
  busy: boolean;
  creating: boolean;
  onHome: () => void;
  onCreate: () => void;
  onGo: (step: number) => void;
}) {
  const answers = project?.result?.answers || [];
  const done = answers.filter((a) => a.review_status === 'complete').length;
  return (
    <aside className="sidebar" aria-label="작업 공간 탐색">
      <button className="brand" onClick={onHome} disabled={busy} aria-label="ESGenie 시작 화면">
        <Sprout />
        ESGenie
      </button>
      <p className="workspace-label">ESG 응답 작업</p>
      <nav className="side-nav" aria-label="실사 응답 준비 단계">
        {['자료 준비', '답변 검토', '응답서'].map((label, i) => (
          <button
            key={label}
            aria-current={project && !creating && step === i + 1 ? 'step' : undefined}
            disabled={busy || !project}
            onClick={() => onGo(i + 1)}
          >
            <span className="nav-number">0{i + 1}</span>
            <span>
              {label}
              <small>
                {i === 0
                  ? project?.stale
                    ? '자료 변경 · 재분석 필요'
                    : project?.job.status === 'complete'
                      ? '분석 완료'
                      : project?.job.status === 'failed'
                        ? '분석 실패'
                        : '자료를 준비하세요'
                  : i === 1
                    ? `${done} / ${answers.length} 검토 완료`
                    : project?.stale
                      ? '이전 결과 · 내려받기 제한'
                      : '저장된 답변 기준'}
              </small>
            </span>
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <button className="secondary" onClick={onCreate} disabled={busy}>
          <Plus />새 응답 작업
        </button>
        <button className="text-button" onClick={onHome} disabled={busy}>
          저장한 작업
        </button>
        <p>이 컴퓨터에 작업 저장</p>
      </div>
    </aside>
  );
}
