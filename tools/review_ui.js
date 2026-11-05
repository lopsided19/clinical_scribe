/* Offline review only. No network or automatic persistent storage. */
'use strict';
const reviewData = JSON.parse(document.getElementById('review-data').textContent);
const cards = Array.from(document.querySelectorAll('article[data-case-index]'));
const decisions = ['unreviewed', 'no_issue', 'needs_revision', 'adjudication'];
const message = document.getElementById('message');

function refresh() {
  const query = document.getElementById('search').value.trim().toLowerCase();
  const filter = document.getElementById('filter').value;
  let visible = 0;
  let reviewed = 0;
  for (const card of cards) {
    const decision = card.querySelector('.decision').value;
    reviewed += Number(decision !== 'unreviewed');
    const matches = filter === 'all' ||
      (filter === 'flagged' && card.dataset.flagged === 'true') ||
      (filter === 'failed' && card.dataset.failed === 'true') ||
      (filter === 'missing' && card.dataset.missing === 'true') ||
      (filter === 'unreviewed' && decision === 'unreviewed');
    card.hidden = !matches || !card.dataset.search.includes(query);
    visible += Number(!card.hidden);
  }
  document.getElementById('progress').textContent = `显示 ${visible} / ${cards.length} 例 · 已复核 ${reviewed} 例`;
}

document.getElementById('search').addEventListener('input', refresh);
document.getElementById('filter').addEventListener('change', refresh);
for (const card of cards) card.querySelector('.decision').addEventListener('change', refresh);

document.getElementById('export').addEventListener('click', () => {
  const reviewer = document.getElementById('reviewer').value.trim();
  if (!reviewer) {
    message.textContent = '请先填写评审编号，便于区分独立意见。';
    document.getElementById('reviewer').focus();
    return;
  }
  const result = {...reviewData, reviewer_id: reviewer, exported_at: new Date().toISOString(),
    cases: cards.map((card, index) => ({case_id: reviewData.case_ids[index],
      decision: card.querySelector('.decision').value, notes: card.querySelector('.notes').value}))};
  delete result.case_ids;
  const blob = new Blob([JSON.stringify(result, null, 2) + '\n'], {type: 'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'clinical_review.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  message.textContent = '已发起下载，包含全部病例（含未复核项）；请确认文件已保存。';
});

document.getElementById('import').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('意见文件超过 5 MB');
    const value = JSON.parse(await file.text());
    if (!value || value.kind !== reviewData.kind || value.schema_version !== reviewData.schema_version ||
        value.review_set_id !== reviewData.review_set_id ||
        value.input_content_sha256 !== reviewData.input_content_sha256 ||
        value.output_content_sha256 !== reviewData.output_content_sha256) {
      throw new Error('文件不属于这批输入和输出');
    }
    if (typeof value.reviewer_id !== 'string' || !value.reviewer_id.trim() ||
        !Array.isArray(value.cases) || value.cases.length !== cards.length) throw new Error('评审或病例数量无效');
    const ids = new Set();
    const mapped = new Map();
    for (const row of value.cases) {
      if (!row || typeof row.case_id !== 'string' || ids.has(row.case_id) ||
          !reviewData.case_ids.includes(row.case_id) || !decisions.includes(row.decision) ||
          typeof row.notes !== 'string') throw new Error('病例编号、状态或意见无效');
      ids.add(row.case_id);
      mapped.set(row.case_id, row);
    }
    const hasNotes = cards.some(card => card.querySelector('.decision').value !== 'unreviewed' || card.querySelector('.notes').value.trim());
    if (hasNotes && !window.confirm('导入会替换当前页面的复核意见。请确认已导出需要保留的内容。')) return;
    // Apply only after the entire document has passed validation.
    document.getElementById('reviewer').value = value.reviewer_id;
    cards.forEach((card, index) => {
      const row = mapped.get(reviewData.case_ids[index]);
      card.querySelector('.decision').value = row.decision;
      card.querySelector('.notes').value = row.notes;
    });
    message.textContent = '已导入同一批结果的复核意见。';
    refresh();
  } catch (error) {
    message.textContent = '未导入：' + error.message + '。当前意见保持不变。';
  } finally {
    event.target.value = '';
  }
});
refresh();
