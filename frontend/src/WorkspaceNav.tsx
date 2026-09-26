import {
  ArrowDownToLine,
  ArrowUpRight,
  FileCheck2,
  FolderOpen,
  LayoutGrid,
  Plus,
  Sprout,
} from 'lucide-react';
import type { Project } from './types';

export function WorkspaceNav({
  project,
  step,
  busy,
  creating,
  onHome,
  onCreate,
  onGo,
  onExample,
}: {
  project: Project | null;
  step: number;
  busy: boolean;
  creating: boolean;
  onHome: () => void;
  onCreate: () => void;
  onGo: (step: number) => void;
  onExample: () => void;
}) {
  return (
    <aside className="sidebar" aria-label="작업 공간 탐색">
      <button className="brand" onClick={onHome} disabled={busy} aria-label="ESGenie 시작 화면">
        <span className="brand-mark">
          <Sprout />
        </span>
        esgenie<span className="brand-dot">.</span>
      </button>
      <div className="workspace-label">공급업체 워크스페이스</div>
      <button className="new-project" onClick={onCreate} disabled={busy}>
        <Plus />새 응답 작업
      </button>
      <nav className="side-nav">
        <button
          aria-current={!project && !creating ? 'page' : undefined}
          onClick={onHome}
          disabled={busy}
        >
          <LayoutGrid />
          작업 홈
        </button>
        {project && (
          <>
            <div className="nav-section">현재 작업</div>
            {[
              [FolderOpen, '자료 보관함'],
              [FileCheck2, '답변 검토'],
              [ArrowDownToLine, '응답서 받기'],
            ].map(([Icon, label], i) => {
              const NavIcon = Icon as typeof FolderOpen;
              return (
                <button
                  key={String(label)}
                  aria-current={!creating && step === i + 1 ? 'page' : undefined}
                  disabled={busy || (i > 0 && !project.result)}
                  onClick={() => onGo(i + 1)}
                >
                  <NavIcon />
                  {String(label)}
                  {i === 1 && project.result && (
                    <span className="nav-count">
                      {project.result.answers.filter((a) => a.needs_attention).length}
                    </span>
                  )}
                </button>
              );
            })}
          </>
        )}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-note">
          <span className="small-caps">EVIDENCE BEFORE ANSWERS</span>
          <p>
            답변의 시작은,
            <br />
            우리 회사의 근거에서.
          </p>
          <button onClick={onExample} disabled={busy}>
            예시로 살펴보기
            <ArrowUpRight />
          </button>
        </div>
        <div className="local-indicator">
          <span />이 컴퓨터에 작업 저장
        </div>
      </div>
    </aside>
  );
}
