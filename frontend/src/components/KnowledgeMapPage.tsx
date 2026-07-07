import { useEffect, useMemo, useState } from "react";

import { getKnowledgeMap, getMaterial } from "../api/readApi";
import type { KnowledgeMapResponse, MaterialSummary } from "../api/types";
import { KnowledgeMapCanvas } from "./KnowledgeMapCanvas";

interface PageState {
  material: MaterialSummary | null;
  map: KnowledgeMapResponse | null;
  error: string | null;
  isLoading: boolean;
}

function getDemoMaterialId(): number {
  const rawValue = import.meta.env.VITE_DEMO_MATERIAL_ID;
  const parsedValue = Number(rawValue);

  return Number.isInteger(parsedValue) && parsedValue > 0 ? parsedValue : 1;
}

export function KnowledgeMapPage() {
  const materialId = useMemo(() => getDemoMaterialId(), []);
  const [pageState, setPageState] = useState<PageState>({
    material: null,
    map: null,
    error: null,
    isLoading: true,
  });

  useEffect(() => {
    const abortController = new AbortController();

    setPageState((currentState) => ({
      ...currentState,
      error: null,
      isLoading: true,
    }));

    Promise.all([
      getMaterial(materialId, abortController.signal),
      getKnowledgeMap(materialId, abortController.signal),
    ])
      .then(([material, map]) => {
        setPageState({
          material,
          map,
          error: null,
          isLoading: false,
        });
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) {
          return;
        }

        setPageState({
          material: null,
          map: null,
          error: error instanceof Error ? error.message : "Unknown request error",
          isLoading: false,
        });
      });

    return () => {
      abortController.abort();
    };
  }, [materialId]);

  const isMapEmpty =
    !pageState.isLoading &&
    !pageState.error &&
    pageState.map !== null &&
    pageState.map.nodes.length === 0 &&
    pageState.map.edges.length === 0;

  return (
    <main className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">Studydy v1</p>
          <h1>Knowledge Map</h1>
        </div>
        <div className="demo-badge">Material #{materialId}</div>
      </header>

      <section className="status-panel" aria-live="polite">
        {pageState.isLoading ? (
          <p className="state-text">Loading material and knowledge map...</p>
        ) : null}

        {pageState.error ? (
          <div className="state-block state-block-error">
            <h2>Unable to load map data</h2>
            <p>{pageState.error}</p>
          </div>
        ) : null}

        {pageState.material ? (
          <div className="material-summary">
            <div>
              <p className="summary-label">Material</p>
              <h2>{pageState.material.title}</h2>
            </div>
            <dl className="summary-grid">
              <div>
                <dt>Processing</dt>
                <dd>{pageState.material.processing_status}</dd>
              </div>
              <div>
                <dt>Upload</dt>
                <dd>{pageState.material.upload_status}</dd>
              </div>
              <div>
                <dt>Subject</dt>
                <dd>{pageState.material.subject}</dd>
              </div>
            </dl>
          </div>
        ) : null}
      </section>

      <section className="map-shell" aria-labelledby="map-shell-title">
        <div className="map-shell-header">
          <div>
            <p className="summary-label">Map Shell</p>
            <h2 id="map-shell-title">Knowledge map data</h2>
          </div>
          {pageState.map ? (
            <div className="map-counts">
              <span>{pageState.map.nodes.length} concepts</span>
              <span>{pageState.map.edges.length} relations</span>
            </div>
          ) : null}
        </div>

        {isMapEmpty ? (
          <div className="state-block">
            <h3>No map data yet</h3>
            <p>The backend returned an empty knowledge map for this material.</p>
          </div>
        ) : null}

        {!pageState.isLoading && !pageState.error && pageState.map && !isMapEmpty ? (
          <KnowledgeMapCanvas map={pageState.map} />
        ) : null}
      </section>
    </main>
  );
}
