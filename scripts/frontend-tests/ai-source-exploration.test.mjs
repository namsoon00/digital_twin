import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = fs.readFileSync(new URL("../../public/ai-control.mjs", import.meta.url), "utf8");
function render(input) {
  // Load the rendering functions without starting browser polling or requests.
  return vm.runInNewContext(source.slice(0, source.indexOf("async function request(")) + "\nresearchAnswerView(input)", {input, URL});
}
test("question answers expose original sources, remaining requirements and exploration counts", () => {
  const html = render({documentaryAnswer:{status:"partial",text:"분기 매출은 확인했습니다.",
    missingRequirements:["전년 동기 단위"],blockers:["일부 원문 접근 실패"],
    sources:[{title:"공식 IR",sourceUrl:"https://example.com/results"}]},
    sourceExploration:{searchCount:2,readCount:3,stopReason:"step-budget-exhausted"}});
  for (const text of ["일부 항목 확인", "분기 매출", "전년 동기 단위", "https://example.com/results", "검색 2회", "문서 읽기 3회", "탐색 횟수 소진"]) assert.ok(html.includes(text), text);
  assert.equal(render({}), "");
});
test("external source text is escaped and unsafe citation links are omitted", () => {
  const html = render({documentaryAnswer:{status:"partial",text:"<script>untrusted</script>",
    sources:[{title:"malicious",sourceUrl:"javascript:alert(1)"},
      {title:"secret",sourceUrl:"https://user:password@example.com/"},
      {title:'<img src=x>',sourceUrl:"https://example.com/"}]}});
  assert.doesNotMatch(html, /<script>|<img|javascript:|user:password/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
});
