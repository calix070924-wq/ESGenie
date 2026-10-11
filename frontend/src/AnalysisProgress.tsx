import { useEffect, useState } from 'react';
import { CircleHelp, LoaderCircle } from 'lucide-react';
import type { Project } from './types';
import './AnalysisProgress.css';

function elapsed(start: string | null | undefined, now: number) {
  const seconds = start ? Math.max(0, Math.floor((now - Date.parse(start)) / 1000)) : 0;
  if (!Number.isFinite(seconds)) return '시간 확인 중';
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

export function AnalysisProgress({ job, connected }: { job: Project['job']; connected: boolean }) {
  const [now, setNow] = useState(Date.now());
  const active = job.status === 'queued' || job.status === 'running';
  useEffect(() => {
    setNow(Date.now());
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, job.run_id]);
  const end = job.ended_at ? Date.parse(job.ended_at) : now;
  return (
    <div
      className="feedback processing analysis-progress"
      role="status"
      aria-label="분석 진행 상황"
    >
      {active && connected ? <LoaderCircle className="spin" /> : <CircleHelp />}
      <div>
        <strong>{active && !connected ? '진행 상태 연결이 끊겼어요' : job.stage}</strong>
        {job.queued_at && <p>전체 경과 {elapsed(job.queued_at, end)}</p>}
        {active && job.stage_started_at && (
          <p>현재 단계 경과 {elapsed(job.stage_started_at, now)}</p>
        )}
        {job.total_files != null && (
          <p>
            파일 처리 완료 {job.completed_files ?? 0} / {job.total_files}개 · 회사 작성 답변 포함
          </p>
        )}
        {!!job.active_files?.length && (
          <p>처리 중: {job.active_files.map((file) => file.name).join(', ')}</p>
        )}
        {active && job.active_files?.length === 0 && <p>현재 처리 파일 없음 · {job.stage}</p>}
        {job.requests?.map((request) => (
          <p key={request.id}>
            {request.state === 'retrying'
              ? 'AI 재시도 중'
              : request.last_retry
                ? 'AI 재시도 응답 대기 중'
                : 'AI 응답 대기 중'}{' '}
            · 시도 {request.attempt} / {request.max_attempts} · 경과{' '}
            {elapsed(request.started_at, now)}
            {request.state === 'retrying' && (
              <>
                {' '}
                · 재요청 전 대기 {Math.ceil(request.delay_seconds ?? 0)}초 · {request.reason}
              </>
            )}
            {request.state === 'waiting' && request.last_retry && (
              <> · 최근 재시도 사유: {request.last_retry.reason}</>
            )}
          </p>
        ))}
        {active && !connected ? (
          <p>
            마지막으로 받은 상태예요. 서버 연결을 다시 확인하고 있어요. 분석 종료 여부는 연결 후
            확인할 수 있어요.
          </p>
        ) : active ? (
          <p>
            파일에 따라 시간이 걸릴 수 있어요. 이 화면을 새로고침해도 서버에 저장된 진행 상태를
            확인할 수 있어요.
          </p>
        ) : null}
      </div>
    </div>
  );
}
