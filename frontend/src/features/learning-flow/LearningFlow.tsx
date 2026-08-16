import { useEffect, useRef, useState, type SubmitEvent } from "react";

import { ApiClientError, StudydyApiClient } from "../../api/client";
import type {
  AssessmentView,
  LearningStateView,
  LearningUpdateCreate,
  MaterialOutputBinding,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import "./styles.css";

type AssessmentLoad =
  | { status: "loading" }
  | { status: "ready"; assessment: AssessmentView; binding: MaterialOutputBinding }
  | { status: "failed"; message: string };

type SubmissionState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "succeeded" }
  | { status: "failed"; message: string; needsNewIntent: boolean };

type SubmissionIntent = {
  key: string;
  update: LearningUpdateCreate;
};

type StateLoad =
  | { status: "loading" }
  | { status: "ready"; state: LearningStateView }
  | { status: "failed"; message: string };

function learningErrorMessage(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : "目前無法完成學習流程，請稍後再試。";
}

function returnToRun(route: Extract<AppRoute, { name: "assessment" }>) {
  writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId });
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    not_started: "尚未開始",
    weak: "需要補強",
    review: "需要複核",
    learning: "學習中",
    mastered: "已掌握",
    no_action: "目前不需額外行動",
    review_concept: "複習概念",
    practice_concept: "練習概念",
    start_concept: "開始學習概念",
    follow_initial_path: "依初始路徑繼續學習",
    remediation_required: "需要補救學習",
    weak_mastery: "概念掌握度偏弱",
    low: "低",
    medium: "中",
    high: "高",
    pending: "尚未開始",
    running: "處理中",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失敗",
    accepted: "已確認",
    needs_review: "待複核",
    unsupported: "不支援",
    retain: "保留",
    reject: "不採用",
  };
  return labels[value] ?? value;
}

function scoreLabel(value: number | null): string {
  return value === null ? "尚無足夠資料" : `${Math.round(value * 100)}%`;
}

export function AssessmentRoute({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "assessment" }>;
}) {
  const [retryVersion, setRetryVersion] = useState(0);
  const [assessmentLoad, setAssessmentLoad] = useState<AssessmentLoad>({ status: "loading" });
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [answerIssue, setAnswerIssue] = useState<string | null>(null);
  const [submission, setSubmission] = useState<SubmissionState>({ status: "idle" });
  const submissionIntent = useRef<SubmissionIntent | null>(null);
  const isSubmissionRunning = useRef(false);
  const submissionAttempt = useRef(0);

  // 路由或重新讀取改變時，舊的載入與送出結果都不得再覆蓋目前畫面。
  useEffect(() => {
    let isCancelled = false;
    submissionAttempt.current += 1;
    isSubmissionRunning.current = false;
    submissionIntent.current = null;
    setAnswers({});
    setAnswerIssue(null);
    setSubmission({ status: "idle" });

    const loadAssessment = async () => {
      setAssessmentLoad({ status: "loading" });
      try {
        const run = await apiClient.getMaterialRun(route.runId);
        if (isCancelled) return;
        const binding = run.output_binding;
        if (
          run.material_id !== route.materialId
          || (run.status !== "succeeded" && run.status !== "partial")
          || binding === null
          || binding.assessment_revision !== route.assessmentRevision
        ) {
          setAssessmentLoad({
            status: "failed",
            message: "此網址與教材評量版本不一致，已停止載入。",
          });
          return;
        }
        const assessment = await apiClient.getAssessment({
          materialId: route.materialId,
          outputRevision: binding.study_material_output_revision,
          mapRevision: binding.knowledge_map_revision,
          pathRevision: binding.learning_path_revision,
          assessmentRevision: binding.assessment_revision,
        });
        if (isCancelled) return;
        setAssessmentLoad({ status: "ready", assessment, binding });
      } catch (error) {
        if (!isCancelled) setAssessmentLoad({ status: "failed", message: learningErrorMessage(error) });
      }
    };

    void loadAssessment();
    return () => {
      isCancelled = true;
      submissionAttempt.current += 1;
      isSubmissionRunning.current = false;
    };
  }, [apiClient, retryVersion, route.assessmentRevision, route.materialId, route.runId]);

  const chooseAnswer = (questionId: string, optionId: string) => {
    if (isSubmissionRunning.current) return;
    setAnswers((current) => ({ ...current, [questionId]: optionId }));
    setAnswerIssue(null);
    if (submission.status === "failed") {
      submissionIntent.current = null;
      setSubmission({ status: "idle" });
    }
  };

  const submitAnswers = async (needsNewIntent = false) => {
    if (assessmentLoad.status !== "ready" || isSubmissionRunning.current) return;
    const unansweredCount = assessmentLoad.assessment.questions.filter((question) => !answers[question.question_id]).length;
    if (unansweredCount > 0) {
      setAnswerIssue(`還有 ${unansweredCount} 題未作答，請完成所有題目後再送出。`);
      return;
    }
    if (!submissionIntent.current || needsNewIntent) {
      // 一般連線重試沿用原識別鍵；只有後端回報內容衝突時才把再次送出視為新的動作。
      submissionIntent.current = {
        key: crypto.randomUUID(),
        update: {
          schema: "learning-update-create/v1",
          material_id: route.materialId,
          map_revision: assessmentLoad.binding.knowledge_map_revision,
          path_revision: assessmentLoad.binding.learning_path_revision,
          assessment_revision: assessmentLoad.binding.assessment_revision,
          responses: assessmentLoad.assessment.questions.map((question) => ({
            question_id: question.question_id,
            selected_option_id: answers[question.question_id],
          })),
        },
      };
    }
    const intent = submissionIntent.current;
    const attempt = submissionAttempt.current + 1;
    submissionAttempt.current = attempt;
    isSubmissionRunning.current = true;
    setSubmission({ status: "submitting" });
    try {
      const completed = await apiClient.submitLearningUpdate(intent.update, intent.key);
      if (submissionAttempt.current !== attempt) return;
      setSubmission({ status: "succeeded" });
      writeRoute(
        {
          name: "learning-state",
          materialId: route.materialId,
          stateRevision: completed.state.state_revision,
        },
        false,
        { learningStateReplay: completed.replayed },
      );
    } catch (error) {
      if (submissionAttempt.current !== attempt) return;
      setSubmission({
        status: "failed",
        message: learningErrorMessage(error),
        needsNewIntent: error instanceof ApiClientError && error.status === 409,
      });
    } finally {
      if (submissionAttempt.current === attempt) isSubmissionRunning.current = false;
    }
  };

  const submitForm = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitAnswers();
  };

  if (assessmentLoad.status === "loading") {
    return (
      <section className="state-page" aria-live="polite">
        <div className="loading-ring" />
        <h1>正在讀取學習評量</h1>
        <p>正在確認教材與評量版本，完成後即會顯示題目。</p>
      </section>
    );
  }
  if (assessmentLoad.status === "failed") {
    return (
      <section className="state-page failure-page" role="alert">
        <h1>無法載入學習評量</h1>
        <p>{assessmentLoad.message}</p>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>重新載入</button>
          <button className="secondary-button" type="button" onClick={() => returnToRun(route)}>返回處理結果</button>
        </div>
      </section>
    );
  }

  const { assessment, binding } = assessmentLoad;
  if (assessment.questions.length === 0) {
    return (
      <section className="state-page learning-empty-page">
        <p className="eyebrow">學習評量</p>
        <h1>目前沒有可作答的題目</h1>
        <p>目前沒有評量題目，因此不會建立作答或學習狀態。</p>
        <div className="surface learning-status-card">
          <strong>{assessment.reason_code}</strong>
          <span>{statusLabel(assessment.processing)} · {statusLabel(assessment.quality)} · {statusLabel(assessment.decision)}</span>
        </div>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>重新載入</button>
          <button className="secondary-button" type="button" onClick={() => returnToRun(route)}>返回處理結果</button>
        </div>
      </section>
    );
  }

  return (
    <section className="learning-page">
      <header className="learning-heading">
        <p className="eyebrow">學習評量</p>
        <h1>根據教材內容完成單選題</h1>
        <p>題目與選項由 Studydy 提供；送出後會同步整理學習結果。</p>
      </header>

      <section className="surface learning-status-card" aria-label="評量狀態">
        <div><span>處理狀態</span><strong>{statusLabel(assessment.processing)}</strong></div>
        <div><span>內容品質</span><strong>{statusLabel(assessment.quality)}</strong></div>
        <div><span>使用判定</span><strong>{statusLabel(assessment.decision)}</strong></div>
        <div><span>原因代碼</span><strong>{assessment.reason_code}</strong></div>
      </section>

      <form className="assessment-form" onSubmit={submitForm} noValidate>
        {assessment.questions.map((question, questionIndex) => (
          <fieldset className="surface question-card" key={question.question_id} disabled={submission.status === "submitting"}>
            <legend>第 {questionIndex + 1} 題</legend>
            <p>{question.prompt}</p>
            <div className="assessment-options">
              {question.options.map((option) => (
                <label className={answers[question.question_id] === option.option_id ? "is-selected" : ""} key={option.option_id}>
                  <input
                    type="radio"
                    name={question.question_id}
                    value={option.option_id}
                    checked={answers[question.question_id] === option.option_id}
                    onChange={() => chooseAnswer(question.question_id, option.option_id)}
                  />
                  <span>{option.text}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}

        {answerIssue && <p className="field-error assessment-message" role="alert">{answerIssue}</p>}
        {submission.status === "failed" && (
          <div className="inline-alert assessment-message" role="alert">
            <span>{submission.message}</span>
            <button
              className="text-button"
              type="button"
              onClick={() => void submitAnswers(submission.needsNewIntent)}
            >
              {submission.needsNewIntent ? "重新送出" : "重試這次送出"}
            </button>
          </div>
        )}

        <section className="surface submission-card">
          <div>
            <strong>作答完成後再送出</strong>
            <span>不會在瀏覽器計算分數、正確答案或學習建議。</span>
          </div>
          <button className="primary-button" type="submit" disabled={submission.status === "submitting"}>
            {submission.status === "submitting" ? "正在同步建立學習狀態…" : "送出作答"}
          </button>
        </section>
      </form>

      <section className="surface learning-revisions" aria-label="評量版本資訊">
        <h2>評量版本資訊</h2>
        <dl>
          <div><dt>教材輸出</dt><dd><code>{binding.study_material_output_revision}</code></dd></div>
          <div><dt>知識地圖</dt><dd><code>{binding.knowledge_map_revision}</code></dd></div>
          <div><dt>學習路徑</dt><dd><code>{binding.learning_path_revision}</code></dd></div>
          <div><dt>學習評量</dt><dd><code>{binding.assessment_revision}</code></dd></div>
        </dl>
      </section>
      <button className="text-button learning-back" type="button" onClick={() => returnToRun(route)}>返回處理結果</button>
    </section>
  );
}

export function LearningStateRoute({ apiClient, route, wasReplayed }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "learning-state" }>;
  wasReplayed: boolean;
}) {
  const [retryVersion, setRetryVersion] = useState(0);
  const [stateLoad, setStateLoad] = useState<StateLoad>({ status: "loading" });

  useEffect(() => {
    let isCancelled = false;
    setStateLoad({ status: "loading" });
    void apiClient.getLearningState({
      materialId: route.materialId,
      stateRevision: route.stateRevision,
    }).then(
      (state) => {
        if (!isCancelled) setStateLoad({ status: "ready", state });
      },
      (error: unknown) => {
        if (!isCancelled) setStateLoad({ status: "failed", message: learningErrorMessage(error) });
      },
    );
    return () => { isCancelled = true; };
  }, [apiClient, retryVersion, route.materialId, route.stateRevision]);

  if (stateLoad.status === "loading") {
    return (
      <section className="state-page" aria-live="polite">
        <div className="loading-ring" />
        <h1>正在讀取學習狀態</h1>
        <p>正在依網址讀取這次的學習結果。</p>
      </section>
    );
  }
  if (stateLoad.status === "failed") {
    return (
      <section className="state-page failure-page" role="alert">
        <h1>無法讀取學習狀態</h1>
        <p>{stateLoad.message}</p>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>重新讀取</button>
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}>返回上傳頁</button>
        </div>
      </section>
    );
  }

  const state = stateLoad.state;
  return (
    <section className="learning-page learning-state-page">
      <header className="learning-heading">
        <p className="eyebrow">學習結果</p>
        <h1>你的學習狀態</h1>
        <p>概念掌握、待加強項目與學習建議都來自這次作答結果。</p>
      </header>

      {wasReplayed && (
        <div className="replay-notice" role="status">
          這是同一次作答的既有結果，沒有重複建立學習狀態。
        </div>
      )}
      {state.quality === "needs_review" && (
        <div className="review-notice" role="status">此結果仍需複核，請保留人工判斷。</div>
      )}

      <section className="surface learning-status-card" aria-label="學習狀態">
        <div><span>處理狀態</span><strong>{statusLabel(state.processing)}</strong></div>
        <div><span>內容品質</span><strong>{statusLabel(state.quality)}</strong></div>
        <div><span>使用判定</span><strong>{statusLabel(state.decision)}</strong></div>
        <div><span>原因代碼</span><strong>{state.reason_code}</strong></div>
      </section>

      <section className="learning-section">
        <div className="section-heading"><h2>概念掌握</h2><span>{state.mastery.length} 個概念</span></div>
        {state.mastery.length === 0 ? (
          <div className="surface learning-empty-card">目前沒有概念掌握資料。</div>
        ) : (
          <div className="mastery-grid">
            {state.mastery.map((mastery) => (
              <article className="surface mastery-card" key={mastery.concept_id}>
                <div className="card-title-row">
                  <h3>{mastery.concept_id}</h3>
                  {mastery.needs_review && <span className="needs-review-badge">待複核</span>}
                </div>
                <strong className="mastery-score">{scoreLabel(mastery.mastery_score)}</strong>
                <dl>
                  <div><dt>學習狀態</dt><dd>{statusLabel(mastery.final_status)}</dd></div>
                  <div><dt>有效作答</dt><dd>{mastery.valid_answer_count}</dd></div>
                  <div><dt>正確率</dt><dd>{scoreLabel(mastery.correct_rate)}</dd></div>
                  <div><dt>練習分數</dt><dd>{scoreLabel(mastery.practice_score)}</dd></div>
                  <div><dt>完成分數</dt><dd>{scoreLabel(mastery.completion_score)}</dd></div>
                </dl>
                {mastery.reason_codes.length > 0 && <p className="reason-list">{mastery.reason_codes.join(" · ")}</p>}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="learning-section">
        <div className="section-heading"><h2>待加強項目</h2><span>{state.weaknesses.length} 項</span></div>
        {state.weaknesses.length === 0 ? (
          <div className="surface learning-empty-card">目前沒有待加強項目。</div>
        ) : state.weaknesses.map((weakness) => (
          <article className="surface weakness-card" key={`${weakness.concept_id}-${weakness.kind}`}>
            <div><strong>{weakness.concept_id}</strong><span>{statusLabel(weakness.kind)}</span></div>
            <p>{weakness.reason_codes.join(" · ")}</p>
          </article>
        ))}
      </section>

      <section className="surface suggestion-card">
        <div className="section-heading"><h2>學習建議</h2><span>{statusLabel(state.suggestion.level)}</span></div>
        <p className="suggestion-action">{statusLabel(state.suggestion.action)}</p>
        <dl>
          <div><dt>目標概念</dt><dd>{state.suggestion.target_concept_id ?? "目前沒有指定"}</dd></div>
          <div><dt>建議分數</dt><dd>{scoreLabel(state.suggestion.learning_suggestion_score)}</dd></div>
          <div><dt>個人化</dt><dd>{state.suggestion.is_personalized ? "是" : "否"}</dd></div>
          <div><dt>需要複核</dt><dd>{state.suggestion.needs_review ? "是" : "否"}</dd></div>
          <div><dt>建議判定</dt><dd>{statusLabel(state.suggestion.decision)}</dd></div>
          <div><dt>原因代碼</dt><dd><code>{state.suggestion.reason_code}</code></dd></div>
        </dl>
      </section>

      <section className="surface learning-revisions" aria-label="學習結果來源版本">
        <h2>來源版本</h2>
        <dl>
          <div><dt>學習狀態</dt><dd><code>{state.state_revision}</code></dd></div>
          <div><dt>知識地圖</dt><dd><code>{state.knowledge_map_revision}</code></dd></div>
          <div><dt>學習路徑</dt><dd><code>{state.learning_path_revision}</code></dd></div>
          <div><dt>學習評量</dt><dd><code>{state.assessment_revision}</code></dd></div>
        </dl>
      </section>

      <div className="button-row learning-actions">
        <button className="primary-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>重新讀取</button>
        <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}>返回上傳頁</button>
      </div>
    </section>
  );
}
