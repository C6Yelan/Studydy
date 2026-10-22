import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { LearningActivityDay, LearningRhythmPeriod, LearningRhythmView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";

// 個人檔案的學習節奏：週、月顯示每天的長條，近 3 個月顯示每週彙總，近一年是每天一格的熱力圖；
// 每個區間都能往前翻到最早有紀錄的時期。數字全部來自 /v1/learning-activity/rhythm 的每日彙總。

const periods: [LearningRhythmPeriod, string][] = [["week", "週"], ["month", "月"], ["quarter", "近 3 個月"], ["year", "近一年"]];
// 各區間最多往前翻的頁數（都約十年），與後端 RHYTHM_MAX_OFFSET 相同；超過會被後端拒絕。
const maxOffset: Record<LearningRhythmPeriod, number> = { week: 520, month: 120, quarter: 40, year: 10 };
const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
const minutes = (seconds: number) => Math.floor(seconds / 60);
// ISO 日期以 UTC 解析，只做日期運算，不受瀏覽器時區影響。
const parse = (value: string) => new Date(`${value}T00:00:00Z`);
const monthDay = (value: string) => `${Number(value.slice(5, 7))}/${Number(value.slice(8))}`;
const weekday = (value: string) => weekdays[parse(value).getUTCDay()];

type Bucket = {
  key: string; label: string; title: string; today: boolean; future: boolean;
  answers: number; correct: number; cards: number; listened: number; activeDays: number; dayCount: number;
};

function bucket(days: LearningActivityDay[], today: string, label: string, title: string): Bucket {
  return days.reduce<Bucket>((sum, day) => ({
    ...sum,
    answers: sum.answers + day.answers, correct: sum.correct + day.correct, cards: sum.cards + day.card_reviews,
    listened: sum.listened + day.listened_seconds, activeDays: sum.activeDays + Number(day.active),
  }), {
    key: days[0].date, label, title, today: days.some((day) => day.date === today), future: days[0].date > today,
    answers: 0, correct: 0, cards: 0, listened: 0, activeDays: 0, dayCount: days.length,
  });
}

function detail(item: Bucket) {
  return [
    item.answers && `作答 ${item.answers} 題，答對 ${item.correct} 題`,
    item.cards && `標記 ${item.cards} 張卡`,
    minutes(item.listened) && `收聽 ${minutes(item.listened)} 分鐘`,
    item.dayCount > 1 && item.activeDays && `學習 ${item.activeDays} 天`,
  ].filter(Boolean).join("，");
}

function rangeLabel(view: LearningRhythmView) {
  if (view.period === "month") return `${view.start.slice(0, 4)} 年 ${Number(view.start.slice(5, 7))} 月`;
  const end = view.start.slice(0, 4) === view.end.slice(0, 4) ? monthDay(view.end) : `${view.end.slice(0, 4)}/${monthDay(view.end)}`;
  return `${view.start.slice(0, 4)}/${monthDay(view.start)} – ${end}`;
}

// 長條：每個時間格三條（作答、概念卡、Podcast），各自以區間內的最大值為滿格。
function Bars({ items, compact }: { items: Bucket[]; compact: boolean }) {
  const peak = {
    answers: Math.max(1, ...items.map((item) => item.answers)),
    cards: Math.max(1, ...items.map((item) => item.cards)),
    listened: Math.max(60, ...items.map((item) => item.listened)),
  };
  const height = (value: number, max: number) => `${value ? Math.max(8, (value / max) * 100) : 0}%`;
  return <ol className={`rhythm-bars${compact ? " is-compact" : ""}`} style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
    {items.map((item, index) => <li key={item.key} className={[item.today && "is-today", item.activeDays && "is-active", item.future && "is-future"].filter(Boolean).join(" ") || undefined}
      aria-label={`${item.title}${item.today && item.dayCount === 1 ? "（今天）" : ""}：${item.future ? "尚未到" : detail(item) || "沒有學習紀錄"}`}>
      <span className="rhythm-bar-stack" aria-hidden="true">
        <i className="is-answers" style={{ height: height(item.answers, peak.answers) }} />
        <i className="is-cards" style={{ height: height(item.cards, peak.cards) }} />
        <i className="is-listen" style={{ height: height(minutes(item.listened) ? item.listened : 0, peak.listened) }} />
      </span>
      {/* 月檢視每格很窄，只標 1 號與每 5 天。 */}
      <small aria-hidden="true">{!compact || index === 0 || (index + 1) % 5 === 0 ? item.label : ""}</small>
    </li>)}
  </ol>;
}

// 熱力圖：一欄一週（週一在上）、一格一天，顏色深淺依當天作答、標記與收聽分鐘的合計。
function Heatmap({ view }: { view: LearningRhythmView }) {
  const score = (day: LearningActivityDay) => day.answers + day.card_reviews + minutes(day.listened_seconds);
  const max = Math.max(1, ...view.days.map(score));
  const weeks = Array.from({ length: view.days.length / 7 }, (_, index) => view.days.slice(index * 7, index * 7 + 7));
  return <div className="rhythm-heatmap">
    <ol className="rhythm-heatmap-months" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}>
      {weeks.map((week) => {
        const first = week.find((day) => day.date.endsWith("-01"));
        return <li key={week[0].date}>{first ? `${Number(first.date.slice(5, 7))}月` : ""}</li>;
      })}
    </ol>
    <ol className="rhythm-heatmap-grid" style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}>
      {view.days.map((day) => {
        const value = score(day);
        const level = value ? Math.min(4, Math.ceil((value / max) * 4)) : day.active ? 1 : 0;
        const item = bucket([day], view.today, "", `${monthDay(day.date)}（週${weekday(day.date)}）`);
        return <li key={day.date} className={`is-level-${level}${day.date === view.today ? " is-today" : ""}${day.date > view.today ? " is-future" : ""}`}
          aria-label={`${item.title}${item.today ? "（今天）" : ""}：${item.future ? "尚未到" : detail(item) || "沒有學習紀錄"}`} />;
      })}
    </ol>
  </div>;
}

export function LearningRhythm({ apiClient }: { apiClient: StudydyApiClient }) {
  const [period, setPeriod] = useState<LearningRhythmPeriod>("week");
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<LearningRhythmView | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  // 只有讀到目前這一頁才算數；讀取中或失敗時不沿用上一頁的區間與翻頁判斷。
  const current = !failed && view?.period === period && view.offset === offset ? view : null;
  const loading = !failed && !current;
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    void apiClient.getLearningRhythm(period, offset).then(
      (value) => { if (!cancelled) setView(value); },
      () => { if (!cancelled) setFailed(true); },
    );
    return () => { cancelled = true; };
  }, [apiClient, period, offset, retry]);
  // 最早紀錄所在的這一頁之前不能再翻；沒有任何紀錄或已到上限時也不能往前。
  const earlier = !!current && !!current.first_day && current.first_day < current.start && offset < maxOffset[period];
  const items = !view ? [] : view.period === "week"
    ? view.days.map((day) => bucket([day], view.today, weekday(day.date), `星期${weekday(day.date)} ${monthDay(day.date)}`))
    : view.period === "month"
      ? view.days.map((day) => bucket([day], view.today, String(Number(day.date.slice(8))), `${monthDay(day.date)}（週${weekday(day.date)}）`))
      : view.period === "quarter"
        ? Array.from({ length: view.days.length / 7 }, (_, index) => {
          const week = view.days.slice(index * 7, index * 7 + 7);
          return bucket(week, view.today, monthDay(week[0].date), `${monthDay(week[0].date)}–${monthDay(week[6].date)} 這週`);
        })
        : [];
  const total = view ? bucket(view.days, view.today, "", "") : null;
  const parts = total ? [
    total.answers && `作答 ${total.answers} 題（答對率 ${Math.round((total.correct / total.answers) * 100)}%）`,
    total.cards && `標記 ${total.cards} 張卡`,
    minutes(total.listened) && `收聽 ${minutes(total.listened)} 分鐘`,
    total.activeDays && `學習 ${total.activeDays} 天`,
  ].filter(Boolean) : [];
  const periodName = periods.find(([key]) => key === period)![1];
  return <section className="profile-rhythm surface" aria-labelledby="profile-rhythm-title" aria-busy={loading}>
    <header className="rhythm-head">
      <h2 id="profile-rhythm-title">學習節奏</h2>
      {view && <span className={`rhythm-streak${view.streak_days ? "" : " is-idle"}`}>
        <Icon name="flame" size={16} />{view.streak_days ? `連續 ${view.streak_days} 天` : "尚未連續"}
      </span>}
      <div className="rhythm-periods" role="group" aria-label="時間區間">
        {periods.map(([key, label]) => <button key={key} type="button" aria-pressed={period === key}
          onClick={() => { setPeriod(key); setOffset(0); }}>{label}</button>)}
      </div>
    </header>
    <div className="rhythm-pager">
      <button className="rhythm-step" type="button" aria-label={`上一個${periodName}`} disabled={!earlier}
        onClick={() => setOffset((value) => value + 1)}><Icon name="chevron-left" size={18} /></button>
      <b aria-live="polite">{current ? rangeLabel(current) : failed ? "—" : "讀取中…"}</b>
      <button className="rhythm-step" type="button" aria-label={`下一個${periodName}`} disabled={offset === 0 || !current}
        onClick={() => setOffset((value) => Math.max(0, value - 1))}><Icon name="chevron-right" size={18} /></button>
      {offset > 0 && <button className="text-button" type="button" disabled={!current} onClick={() => setOffset(0)}>回到{period === "week" ? "本週" : period === "month" ? "本月" : "最近"}</button>}
    </div>
    {failed ? <div className="rhythm-failure" role="alert">
      <p>無法讀取學習節奏，請稍後再試。</p>
      <button className="secondary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button>
    </div> : view && <>
      <div className="rhythm-chart">
        {view.period === "year" ? <Heatmap view={view} /> : <Bars items={items} compact={view.period === "month"} />}
      </div>
      {view.period === "year"
        ? <ul className="rhythm-scale" aria-hidden="true"><li>少</li>{[0, 1, 2, 3, 4].map((level) => <li key={level} className={`is-level-${level}`} />)}<li>多</li></ul>
        : <ul className="rhythm-legend" aria-hidden="true"><li className="is-answers">作答</li><li className="is-cards">概念卡</li><li className="is-listen">Podcast</li></ul>}
      <p className="rhythm-summary">
        {parts.length ? <>這段期間{parts.join(" · ")}</>
          : offset === 0 ? "這段期間還沒有學習紀錄，作答、標記卡片或收聽都會出現在這裡。" : "這段期間沒有學習紀錄。"}
      </p>
    </>}
    <small className="rhythm-note">依作答、概念卡標記與實際收聽紀錄計算，以台灣時間為準；收聽滿 1 分鐘才算當天有學習。</small>
  </section>;
}
