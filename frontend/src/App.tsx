import "./styles.css";

export function App() {
  return (
    <main className="app-shell">
      <section className="app-panel" aria-labelledby="app-title">
        <p className="app-kicker">Studydy v1</p>
        <h1 id="app-title">Knowledge Map frontend scaffold</h1>
        <p className="app-copy">
          React, TypeScript, Vite, and React Flow dependencies are ready for the
          next frontend slice.
        </p>
      </section>
    </main>
  );
}
