import { useEffect, useMemo, useState } from "react";

import { getConceptDetail, getKnowledgeMap, getMaterial } from "../api/readApi";
import type {
  ConceptDetailResponse,
  KnowledgeMapResponse,
  MaterialSummary,
} from "../api/types";
import { ConceptDetailPanel } from "./ConceptDetailPanel";
import { KnowledgeMapCanvas } from "./KnowledgeMapCanvas";
import { MapLegend } from "./MapLegend";

interface PageState {
  material: MaterialSummary | null;
  map: KnowledgeMapResponse | null;
  error: string | null;
  isLoading: boolean;
}

interface DetailState {
  detail: ConceptDetailResponse | null;
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
  const [selectedConceptId, setSelectedConceptId] = useState<number | null>(null);
  const [detailState, setDetailState] = useState<DetailState>({
    detail: null,
    error: null,
    isLoading: false,
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
        setSelectedConceptId(null);
        setDetailState({
          detail: null,
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

  useEffect(() => {
    if (selectedConceptId === null) {
      setDetailState({
        detail: null,
        error: null,
        isLoading: false,
      });
      return;
    }

    const abortController = new AbortController();

    setDetailState({
      detail: null,
      error: null,
      isLoading: true,
    });

    getConceptDetail(selectedConceptId, abortController.signal)
      .then((detail) => {
        setDetailState({
          detail,
          error: null,
          isLoading: false,
        });
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) {
          return;
        }

        setDetailState({
          detail: null,
          error: error instanceof Error ? error.message : "Unknown request error",
          isLoading: false,
        });
      });

    return () => {
      abortController.abort();
    };
  }, [selectedConceptId]);

  const isMapEmpty =
    !pageState.isLoading &&
    !pageState.error &&
    pageState.map !== null &&
    pageState.map.nodes.length === 0 &&
    pageState.map.edges.length === 0;
  const conceptLabels = useMemo(() => {
    if (!pageState.map) {
      return {};
    }

    return Object.fromEntries(
      pageState.map.nodes.map((node) => [Number(node.id), node.data.label]),
    );
  }, [pageState.map]);

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
          <div className="map-workspace">
            <div className="map-primary">
              <KnowledgeMapCanvas
                map={pageState.map}
                selectedConceptId={selectedConceptId}
                onConceptSelect={setSelectedConceptId}
              />
              <MapLegend />
            </div>
            <ConceptDetailPanel
              detail={detailState.detail}
              error={detailState.error}
              isLoading={detailState.isLoading}
              selectedConceptId={selectedConceptId}
              conceptLabels={conceptLabels}
            />
          </div>
        ) : null}
      </section>
    </main>
  );
}
