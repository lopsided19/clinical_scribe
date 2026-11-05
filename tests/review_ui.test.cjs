// Pure script tests with a small in-memory DOM fixture; no browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../tools/review_ui.js'), 'utf8');

function element(value = '') {
  return {value, textContent: '', events: {}, addEventListener(name, callback) { this.events[name] = callback; },
    focus() { this.focused = true; }, appendChild() {}, click() {}, remove() {}};
}

function setup() {
  const data = {schema_version: '1.0', kind: 'human_review_not_official_score', review_set_id: 'set-1',
    input_content_sha256: 'input-1', output_content_sha256: 'output-1', case_ids: ['CASE-A', 'CASE-B']};
  const ids = {};
  for (const id of ['review-data', 'search', 'filter', 'reviewer', 'export', 'import', 'message', 'progress']) ids[id] = element();
  ids['review-data'].textContent = JSON.stringify(data);
  ids.filter.value = 'all';
  const cards = data.case_ids.map((id, index) => {
    const controls = {'.decision': element('unreviewed'), '.notes': element()};
    return {dataset: {search: id.toLowerCase(), flagged: String(index === 1), failed: String(index === 1), missing: String(index === 1)},
      hidden: false, querySelector: key => controls[key]};
  });
  const downloads = [];
  const urls = [];
  const context = {Blob, Date, Set, Map, console, setTimeout: fn => fn(),
    URL: {createObjectURL(blob) { urls.push(blob); return 'blob:fixture'; }, revokeObjectURL() {}},
    window: {confirm: () => true}, document: {getElementById: key => ids[key],
      querySelectorAll: () => cards, body: element(), createElement() {
        const link = element(); link.click = () => downloads.push(link.download); return link;
      }}};
  vm.runInNewContext(script, context);
  async function importValue(value, size = 100) {
    await ids.import.events.change({target: {value: 'fixture.json', files: [{size, text: async () => JSON.stringify(value)}]}});
  }
  const valid = () => ({...data, reviewer_id: 'reviewer-1', cases: data.case_ids.map(id => ({case_id: id, decision: 'needs_revision', notes: '人工测试意见'}))});
  return {data, ids, cards, downloads, urls, context, importValue, valid};
}

(async () => {
  let passed = 0;
  async function test(name, callback) { await callback(); passed++; console.log('ok - ' + name); }
  await test('search and issue filters combine', () => {
    const s = setup();
    s.ids.filter.value = 'flagged'; s.ids.filter.events.change();
    assert.equal(s.cards[0].hidden, true); assert.equal(s.cards[1].hidden, false);
    s.ids.search.value = 'case-a'; s.ids.search.events.input();
    assert.equal(s.cards[1].hidden, true);
  });
  await test('review decisions update progress and unreviewed filter', () => {
    const s = setup(); s.ids.filter.value = 'unreviewed';
    s.cards[0].querySelector('.decision').value = 'no_issue';
    s.cards[0].querySelector('.decision').events.change();
    assert.equal(s.cards[0].hidden, true); assert.equal(s.cards[1].hidden, false);
    assert.equal(s.ids.progress.textContent, '显示 1 / 2 例 · 已复核 1 例');
  });
  await test('export requires a reviewer', () => {
    const s = setup(); s.ids.export.events.click();
    assert.equal(s.downloads.length, 0); assert.equal(s.ids.reviewer.focused, true);
  });
  await test('export binds all cases including filtered and unreviewed cases', async () => {
    const s = setup(); s.ids.reviewer.value = 'r-1';
    s.cards[0].querySelector('.notes').value = '<script>inert notes</script>';
    s.ids.search.value = 'case-b'; s.ids.search.events.input();
    s.ids.export.events.click();
    assert.deepEqual(s.downloads, ['clinical_review.json']);
    const output = JSON.parse(await s.urls[0].text());
    assert.equal(output.cases.length, 2); assert.equal(output.cases[0].notes, '<script>inert notes</script>');
    assert.equal(output.cases[0].decision, 'unreviewed'); assert.equal(output.review_set_id, s.data.review_set_id);
    assert.equal(output.case_ids, undefined);
  });
  await test('valid import applies reviewer and opinions', async () => {
    const s = setup(); await s.importValue(s.valid());
    assert.equal(s.ids.reviewer.value, 'reviewer-1');
    assert.equal(s.cards[1].querySelector('.notes').value, '人工测试意见');
    assert.match(s.ids.progress.textContent, /已复核 2 例/);
  });
  await test('mismatched content is rejected without mutating opinions', async () => {
    const s = setup(); const value = s.valid(); value.output_content_sha256 = 'other';
    await s.importValue(value); assert.match(s.ids.message.textContent, /不属于这批/);
    assert.equal(s.cards[0].querySelector('.decision').value, 'unreviewed');
  });
  await test('duplicate case cannot cause partial import', async () => {
    const s = setup(); const value = s.valid(); value.cases[1].case_id = 'CASE-A';
    await s.importValue(value); assert.match(s.ids.message.textContent, /未导入/);
    assert.equal(s.cards[0].querySelector('.notes').value, '');
  });
  await test('unknown decisions and non-text notes are rejected', async () => {
    for (const [key, invalid] of [['decision', 'official_score_100'], ['notes', {}]]) {
      const s = setup(); const value = s.valid(); value.cases[1][key] = invalid;
      await s.importValue(value); assert.match(s.ids.message.textContent, /未导入/);
      assert.equal(s.cards[0].querySelector('.notes').value, '');
    }
  });
  await test('canceling replacement preserves current opinions', async () => {
    const s = setup(); s.cards[0].querySelector('.notes').value = '尚未导出的意见';
    s.context.window.confirm = () => false;
    await s.importValue(s.valid()); assert.equal(s.cards[0].querySelector('.notes').value, '尚未导出的意见');
    assert.equal(s.ids.reviewer.value, '');
  });
  await test('oversized file is rejected before reading', async () => {
    const s = setup(); await s.importValue(s.valid(), 6 * 1024 * 1024);
    assert.match(s.ids.message.textContent, /超过 5 MB/);
    assert.equal(s.ids.reviewer.value, '');
  });
  console.log(`${passed} offline review interaction checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
