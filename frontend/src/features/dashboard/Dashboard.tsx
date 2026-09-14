import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon, type IconName } from "../../ui/Icon";
import "./styles.css";

const features: { icon: IconName; title: string; description: string }[] = [
  { icon: "map", title: "建立知識地圖", description: "分析你的教材，整理概念、關聯與原始依據。" },
  { icon: "learning", title: "依循學習路徑", description: "沿著教材的學習順序，逐步掌握重要概念。" },
  { icon: "book", title: "理解概念", description: "探索教材重點，隨時回到 PDF 查看來源。" },
  { icon: "check", title: "練習與複習", description: "透過題目確認理解，接續原本的學習與回饋。" },
];

export function Dashboard({ apiClient }: { apiClient: StudydyApiClient }) {
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then(library => { if (!cancelled) setMaterials(library.materials); }, failure => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, retry]);
  const studies = (materials ?? []).flatMap(material => {
    const structure = material.available_structures[0];
    const state = structure && material.study_sessions.find(item => item.run_id === structure.run_id && item.knowledge_structure_revision === structure.knowledge_structure_revision);
    return state ? [{ material, session: state }] : [];
  });
  const ordered = [...studies].sort((a, b) => Date.parse(b.session.started_at) - Date.parse(a.session.started_at));
  const recent = ordered.find(item => item.session.status === "active" || item.session.status === "no_safe")
    ?? ordered.find(item => item.session.status === "completed");
  const stats: { title: string; value: number | undefined; note: string; icon: IconName }[] = [
    { title: "教材", value: materials?.length, note: materials?.length ? "已保存的 PDF" : "尚未上傳教材", icon: "book" },
    { title: "知識地圖", value: materials?.filter(item => item.available_structures.length > 0).length, note: "可開啟地圖的教材", icon: "map" },
    { title: "學習進度", value: materials ? studies.length : undefined, note: "保留原本的學習位置", icon: "learning" },
    { title: "已完成學習", value: materials ? studies.filter(item => item.session.status === "completed").length : undefined, note: "已完成的教材學習", icon: "check" },
  ];
  return <section className="dashboard">
    <header className="dashboard-greeting"><h1>歡迎回來！</h1><p>讓我們一起繼續你的學習旅程。</p></header>
    <div className="dashboard-content">
      <div className="dashboard-primary">
        <section className="dashboard-hero" aria-label="建立你的知識地圖">
          <div className="hero-copy"><h2>建立你的知識地圖</h2><p>上傳你的學習教材，讓 AI 為你建立專屬的知識地圖。</p>
            <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}><Icon name="upload" size={18} />上傳教材</button>
            <button className="text-button hero-library-link" type="button" onClick={() => writeRoute({ name: "materials" })}>前往我的教材 <Icon name="chevron-right" size={16} /></button>
          </div>
          <div className="hero-illustration"><div className="hero-document" aria-hidden="true"><Icon name="file" size={54} /><span /><span /><span /></div><img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="Studydy 引導你建立知識地圖" /></div>
        </section>
        {error && <div className="dashboard-error" role="alert"><p>{error}</p><button className="secondary-button" type="button" onClick={() => setRetry(value => value + 1)}>重新讀取</button></div>}
        <section className="dashboard-overview" aria-label="學習總覽"><h2>學習總覽</h2><div className="dashboard-stats" aria-busy={materials === null && !error}>
          {stats.map((stat, index) => <button className={`dashboard-stat accent-${index}`} key={stat.title} type="button" onClick={() => writeRoute({ name: "materials" })}>
            <span className="stat-icon"><Icon name={stat.icon} size={23} /></span><span className="stat-copy"><span>{stat.title}</span><strong>{error ? "—" : stat.value ?? "—"}</strong><small>{error ? "暫時無法讀取" : materials ? stat.note : "正在讀取…"}</small></span><Icon name="chevron-right" size={16} />
          </button>)}
        </div></section>
        {recent && !error && <section className="dashboard-resume surface"><div><h2>繼續學習</h2><p>{recent.material.display_name}</p></div><button className="primary-button" type="button" onClick={() => writeRoute({ name: "study-session", materialId: recent.material.material_id, runId: recent.session.run_id, structureRevision: recent.session.knowledge_structure_revision, studySessionId: recent.session.study_session_id })}>{recent.session.status === "completed" ? "查看學習成果" : "繼續學習"}<Icon name="chevron-right" size={18} /></button></section>}
      </div>
      <aside className="dashboard-help surface" aria-label="Studydy 學習協助"><h2>Studydy 如何幫助你的學習</h2><div className="dashboard-features">{features.map((feature, index) => <article className={`accent-${index}`} key={feature.title}><span className="stat-icon"><Icon name={feature.icon} size={22} /></span><div><h3>{feature.title}</h3><p>{feature.description}</p></div></article>)}</div></aside>
    </div>
  </section>;
}
