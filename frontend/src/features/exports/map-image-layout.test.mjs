import assert from 'node:assert/strict';
import test from 'node:test';
import { layoutMap, MAP_NODE_WIDTH } from './map-image-layout.ts';

const edge = (source, target, id = `${source}-${target}`, prerequisite = true) => ({ id, source, target, prerequisite });
const nodes = ids => ids.map((id, order) => ({ id, height: 80, order }));

function overlaps(layout) {
  const boxes = [...layout.nodes.values()];
  return boxes.some((a, i) => boxes.slice(i + 1).some(b =>
    Math.abs(a.x - b.x) < MAP_NODE_WIDTH && a.y < b.y + b.height && b.y < a.y + a.height));
}

test('unlinked concepts are left to the caller instead of piling into one column', () => {
  const layout = layoutMap({ nodes: nodes(['a', 'b', 'c', 'd']), edges: [edge('a', 'b')] });
  assert.deepEqual(layout.isolated, ['c', 'd']);
  assert.deepEqual([...layout.nodes.keys()].sort(), ['a', 'b']);
});

test('a long prerequisite chain wraps into rows with a screen-like aspect and no overlap', () => {
  const ids = Array.from({ length: 12 }, (_, i) => `n${i}`);
  const layout = layoutMap({ nodes: nodes(ids), edges: ids.slice(1).map((id, i) => edge(ids[i], id)) });
  assert.equal(overlaps(layout), false);
  assert.ok(layout.width / layout.height > 0.8 && layout.width / layout.height < 2.6, `${layout.width}x${layout.height}`);
});

test('an edge that skips a layer keeps its own lane instead of crossing the middle concept', () => {
  const layout = layoutMap({ nodes: nodes(['a', 'b', 'c']), edges: [edge('a', 'b'), edge('b', 'c'), edge('a', 'c')] });
  const b = layout.nodes.get('b'), skip = layout.edges.get('a-c').ops;
  const middle = skip.filter(op => op[0] === 'C').map(op => [op[5], op[6]]).find(([x]) => x > b.x - 5 && x < b.x + MAP_NODE_WIDTH);
  assert.ok(middle && (middle[1] < b.y || middle[1] > b.y + b.height));
});

test('backward and same-layer relations are drawn without changing the prerequisite layers', () => {
  const layout = layoutMap({ nodes: nodes(['a', 'b', 'c']), edges: [edge('a', 'b'), edge('c', 'a', 'back', false), edge('a', 'c', 'side', false)] });
  assert.equal(layout.edges.size, 3);
  assert.equal(overlaps(layout), false);
});
