'use strict';

const form = document.getElementById('check-form');
const sizesList = document.getElementById('sizes-list');
const patternInput = document.getElementById('pattern');
const resultSection = document.getElementById('result');
const summaryBox = document.getElementById('summary');
const sizesResults = document.getElementById('sizes-results');

const yarnInputs = {
  k: document.getElementById('weight-k'),
  p: document.getElementById('weight-p'),
  yo: document.getElementById('weight-yo'),
  k2tog: document.getElementById('weight-k2tog'),
  waste_percent: document.getElementById('waste-percent'),
  ball_grams: document.getElementById('ball-grams'),
};

const EXAMPLE_SIZES = [
  { name: 'S', cast_on: 12 },
  { name: 'M', cast_on: 16 },
  { name: 'L', cast_on: 20 },
];

const EXAMPLE_PATTERN = [
  '(k2tog yo)*{6|8|10}',
  'p*{12|16|20}',
  '(k2tog)*{6|8|10}',
  '(k2tog)*{3|4|5}',
  'k*{3|4|5}',
].join('\n');

function addSizeRow(name, castOn) {
  const row = document.createElement('div');
  row.className = 'size-row';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'size-name';
  nameInput.placeholder = '尺码名';
  nameInput.value = name;
  nameInput.required = true;

  const castOnInput = document.createElement('input');
  castOnInput.type = 'number';
  castOnInput.className = 'size-cast-on';
  castOnInput.min = '1';
  castOnInput.step = '1';
  castOnInput.placeholder = '起针数';
  castOnInput.value = castOn === '' ? '' : String(castOn);
  castOnInput.required = true;

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'remove-size';
  removeBtn.textContent = '×';
  removeBtn.title = '删除该尺码';
  removeBtn.addEventListener('click', () => {
    row.remove();
    updateRemoveButtons();
  });

  row.appendChild(nameInput);
  row.appendChild(castOnInput);
  row.appendChild(removeBtn);
  sizesList.appendChild(row);
  updateRemoveButtons();
}

function updateRemoveButtons() {
  const rows = sizesList.querySelectorAll('.size-row');
  rows.forEach((row) => {
    row.querySelector('.remove-size').disabled = rows.length === 1;
  });
}

function readSizes() {
  return Array.from(sizesList.querySelectorAll('.size-row')).map((row) => ({
    name: row.querySelector('.size-name').value,
    cast_on: row.querySelector('.size-cast-on').value,
  }));
}

function readYarn() {
  return {
    weights: {
      k: yarnInputs.k.value,
      p: yarnInputs.p.value,
      yo: yarnInputs.yo.value,
      k2tog: yarnInputs.k2tog.value,
    },
    waste_percent: yarnInputs.waste_percent.value,
    ball_grams: yarnInputs.ball_grams.value,
  };
}

document.getElementById('add-size').addEventListener('click', () => {
  addSizeRow('', '');
});

document.getElementById('fill-example').addEventListener('click', () => {
  sizesList.replaceChildren();
  for (const s of EXAMPLE_SIZES) {
    addSizeRow(s.name, s.cast_on);
  }
  patternInput.value = EXAMPLE_PATTERN;
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  summaryBox.textContent = '校验中…';
  summaryBox.className = '';
  resultSection.hidden = false;
  sizesResults.replaceChildren();

  let data;
  try {
    const res = await fetch('/api/validate-sizes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sizes: readSizes(),
        pattern: patternInput.value,
        yarn: readYarn(),
      }),
    });
    data = await res.json();
  } catch (err) {
    summaryBox.textContent = '请求失败：' + err.message;
    summaryBox.className = 'error';
    return;
  }
  render(data);
});

function render(data) {
  sizesResults.replaceChildren();

  // 尺码列表本身不合法：没有分尺码结果
  if (!Array.isArray(data.sizes) || data.sizes.length === 0) {
    summaryBox.textContent = '校验未通过：' + (data.error ? data.error.message : '未知错误');
    summaryBox.className = 'error';
    return;
  }

  const failed = data.sizes.filter((s) => !s.ok);
  if (failed.length === 0) {
    summaryBox.textContent = '校验通过：全部 ' + data.sizes.length + ' 个尺码的针数均正确。';
    summaryBox.className = 'ok';
  } else {
    summaryBox.textContent = '校验未通过：' + failed.length + ' / ' + data.sizes.length +
      ' 个尺码存在问题（' + failed.map((s) => s.name).join('、') +
      '），其余尺码已继续推算。';
    summaryBox.className = 'error';
  }

  const partial = data.sizes.filter((s) => s.yarn && s.yarn.partial);
  if (partial.length > 0) {
    const note = document.createElement('div');
    note.className = 'partial-note';
    note.textContent = '注意：' + partial.map((s) => s.name).join('、') +
      ' 的用量为部分估算（该尺码在出错行停止，后续行未计入）。';
    summaryBox.appendChild(document.createElement('br'));
    summaryBox.appendChild(note);
  }

  for (const size of data.sizes) {
    sizesResults.appendChild(renderSizeCard(size));
  }
}

function renderSizeCard(size) {
  const card = document.createElement('div');
  card.className = 'size-card';

  const title = document.createElement('h2');
  title.textContent = size.name + '（起针 ' + size.cast_on + '）';
  card.appendChild(title);

  const status = document.createElement('div');
  status.className = 'size-status ' + (size.ok ? 'ok' : 'error');
  if (size.ok) {
    status.textContent = '通过：' + size.rows.length + ' 行有效，最终 ' + size.final + ' 针。';
  } else {
    status.textContent = '未通过：' + size.rows.length + ' 行有效，止于第 ' +
      size.error.line + ' 行，最后有效针数 ' + size.final + ' 针。';
  }
  card.appendChild(status);

  if (size.error) {
    const errBox = document.createElement('div');
    errBox.className = 'error size-error';
    errBox.textContent = formatError(size.error);
    card.appendChild(errBox);
  }

  if (size.yarn) {
    card.appendChild(buildYarnBox(size.yarn));
  }

  if (size.rows.length > 0) {
    card.appendChild(buildRowsTable(size.rows, Boolean(size.yarn)));
  }
  return card;
}

function formatGrams(value) {
  // 去掉浮点运算产生的多余尾零
  return String(Math.round(value * 1000) / 1000);
}

function buildYarnBox(yarn) {
  const box = document.createElement('div');
  box.className = 'yarn-box' + (yarn.partial ? ' partial' : '');

  const title = document.createElement('div');
  title.className = 'yarn-title';
  title.textContent = '线材用量' + (yarn.partial ? '（部分估算）' : '');
  box.appendChild(title);

  const rows = [
    ['原始总量', formatGrams(yarn.raw_total) + ' g'],
    ['含损耗总量', formatGrams(yarn.with_waste) + ' g'],
    ['建议备线', yarn.balls + ' 团（向上取整）'],
  ];
  for (const [label, value] of rows) {
    const line = document.createElement('div');
    line.className = 'yarn-line';
    const k = document.createElement('span');
    k.className = 'yarn-label';
    k.textContent = label;
    const v = document.createElement('span');
    v.className = 'yarn-value';
    v.textContent = value;
    line.appendChild(k);
    line.appendChild(v);
    box.appendChild(line);
  }
  return box;
}

function formatError(err) {
  if (err.type === 'parse') {
    return '第 ' + err.line + ' 行指令无法解析：' + err.message;
  }
  if (err.type === 'count') {
    const kind = err.diff > 0 ? '针数不足' : '针数超出';
    return '第 ' + err.line + ' 行' + kind + '（差额 ' + Math.abs(err.diff) + ' 针）：' + err.message;
  }
  return err.message;
}

function buildRowsTable(rows, withWeight) {
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const labels = ['行号', '指令', '入针', '消耗', '产出', '出针'];
  if (withWeight) {
    labels.push('累计克重');
  }
  for (const label of labels) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    const values = [row.line, row.instruction, row.in, row.consume, row.produce, row.out];
    if (withWeight) {
      values.push(formatGrams(row.weight) + ' g');
    }
    for (const value of values) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

// 初始填入示例尺码，便于直接体验
for (const s of EXAMPLE_SIZES) {
  addSizeRow(s.name, s.cast_on);
}
