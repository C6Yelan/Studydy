export function MapLegend() {
  return (
    <section className="map-legend" aria-label="Map legend">
      <h2>Map Legend</h2>
      <ul>
        <li>
          <span className="legend-swatch legend-swatch-node" />
          Concept node
        </li>
        <li>
          <span className="legend-swatch legend-swatch-relation" />
          Relation edge label
        </li>
        <li>
          <span className="legend-swatch legend-swatch-review" />
          Needs review
        </li>
      </ul>
    </section>
  );
}
