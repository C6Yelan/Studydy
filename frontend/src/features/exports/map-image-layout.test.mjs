import assert from 'node:assert/strict';
import test from 'node:test';
import { arrangeMapColumns, mapEdgeLanes } from './map-image-layout.ts';

test('export reorders crossed targets while keeping prerequisite layers', () => {
  const input = [['a', 'b'], ['c', 'd']];
  const arranged = arrangeMapColumns(input, [{source_concept_id:'a',target_concept_id:'d'},{source_concept_id:'b',target_concept_id:'c'}]);
  assert.equal(arranged[0].indexOf('a') < arranged[0].indexOf('b'), arranged[1].indexOf('d') < arranged[1].indexOf('c'));
  assert.deepEqual(arranged.map(ids => [...ids].sort()), input);
  assert.deepEqual(input, [['a','b'],['c','d']]);
});

test('overlapping spans use different lanes and disjoint spans can reuse a lane', () => {
  const lanes = mapEdgeLanes([{id:'long',start:0,end:3},{id:'first',start:0,end:1},{id:'last',start:2,end:3}]);
  assert.notEqual(lanes.get('long'), lanes.get('first'));
  assert.notEqual(lanes.get('long'), lanes.get('last'));
  assert.equal(lanes.get('first'), lanes.get('last'));
});
