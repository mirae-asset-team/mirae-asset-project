(function () {
  const stages = window.ROADMAP_STAGES || [];
  const fileStage = location.pathname.split("/").pop().replace(".html", "");
  const id = document.body.dataset.stage || fileStage || "database";
  const stage = stages.find((item) => item.id === id) || stages[0];
  if (!stage) return;

  document.documentElement.style.setProperty("--stage-color", stage.color);
  document.title = `${stage.number}. ${stage.title} | 공시 Agent 구축 로드맵`;

  const esc = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  const list = (items, cls) => `<ul class="${cls}">${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;

  const nav = stages.map((item) =>
    `<a href="${item.id}.html" class="${item.id === stage.id ? "active" : ""}">${item.number} ${item.short}</a>`
  ).join("");
  const flow = stage.flow.map((item, index) =>
    `<div class="flow-step"><small>${String(index + 1).padStart(2, "0")}</small><b>${item[0]}</b><span>${item[1]}</span></div>`
  ).join("");
  const candidateRows = stage.candidates.map((item) => `
    <tr>
      <td><span class="candidate-tag ${item.type}">${item.badge}</span><br><strong>${item.name}</strong></td>
      <td>${item.role}</td><td>${item.strength}</td><td>${item.risk}</td><td>${item.rule}</td>
    </tr>`).join("");
  const metrics = stage.criteria.map((item, index) =>
    `<div class="metric-card"><span class="metric-no">${String(index + 1).padStart(2, "0")}</span><b>${item[0]}</b><span>${item[1]}</span></div>`
  ).join("");
  const sources = stage.sources.map((item) => `<li><a href="${item[1]}" target="_blank" rel="noreferrer">${item[0]} <span>↗</span></a></li>`).join("");
  const progress = stage.progress || {
    tone: "wait", label: "미착수", headline: "앞 단계의 Gold·품질 Gate가 통과된 뒤 착수합니다.",
    summary: "현재는 설계 후보와 평가 기준만 정리된 상태입니다. 구현 완료로 표시하지 않으며, 앞 단계의 검증 산출물을 입력으로 고정한 뒤 이 단계의 baseline부터 시작합니다.",
    score: "대기", scoreLabel: "현재 구현 상태",
    metrics: [["0", "완료 구현", "설계 문서와 후보만 존재"], ["Gold 후", "착수 조건", "같은 평가셋에서 비교"], ["1개", "첫 baseline", "가장 단순한 경로부터"], ["분리", "오류 측정", "앞 단계와 원인 혼합 금지"]],
    done: ["후보 방식·위험·평가 기준·승격 조건을 로드맵에 문서화함"],
    next: stage.deliverables.slice(0, 3).map((item) => `${item} 구현·검증`)
  };
  const progressHtml = progress ? `
    <article class="panel stage-progress ${progress.tone}" id="current">
      <div class="stage-progress-head">
        <div><span class="progress-pill ${progress.tone}">${progress.label}</span><h2>${progress.headline}</h2><p>${progress.summary}</p></div>
        <div class="stage-progress-score"><strong>${progress.score}</strong><span>${progress.scoreLabel}</span></div>
      </div>
      <div class="stage-metric-grid">${progress.metrics.map((item) => `<div><strong>${item[0]}</strong><span>${item[1]}</span><small>${item[2] || ""}</small></div>`).join("")}</div>
      <div class="stage-progress-columns">
        <div><h3>완료·확인된 것</h3>${list(progress.done, "gate-list compact-list")}</div>
        <div><h3>남은 작업·차단선</h3>${list(progress.next, "risk-list compact-list")}</div>
      </div>
      ${progress.example ? `<div class="example progress-example"><div class="caption">실제 결과 예시</div><pre>${esc(progress.example)}</pre></div>` : ""}
      ${progress.artifact ? `<a class="artifact-link" href="${progress.artifact[1]}">${progress.artifact[0]} →</a>` : ""}
    </article>` : "";

  document.getElementById("app").innerHTML = `
    <header class="topbar detail-topbar">
      <div class="shell">
        <a class="brand" href="../system_build_roadmap.html"><span class="brand-mark">DI</span><span>Disclosure Intelligence</span></a>
        <nav class="topnav"><a href="#process">처리 흐름</a><a href="#candidates">후보 비교</a><a href="#criteria">평가 기준</a><a href="#example">구조 예시</a></nav>
      </div>
    </header>
    <main>
      <section class="detail-hero detail-hero-v2">
        <div class="shell">
          <a class="back" href="../system_build_roadmap.html">← 전체 파이프라인</a>
          <div class="detail-title detail-title-v2">
            <div class="stage-number">${stage.number}</div>
            <div><div class="eyebrow">${stage.kicker}</div><h1>${stage.title}</h1><p>${stage.tagline}</p></div>
          </div>
          <nav class="stage-nav" aria-label="상세 단계 이동">${nav}</nav>
        </div>
      </section>

      <section class="detail-main detail-main-v2">
        <div class="shell content-stack">
          ${progressHtml}
          <div class="quick-grid">
            <article class="panel recommendation quick-recommendation" id="recommendation">
              <div class="panel-kicker">Expected best combination</div><h2>먼저 검증할 조합</h2>
              <p class="big-answer">${stage.recommendation.base}</p>
              <div class="quick-reason"><b>왜 이 조합인가</b><p>${stage.recommendation.reason}</p></div>
              <div class="quick-challenger"><b>다음 도전자</b><p>${stage.recommendation.challenger}</p></div>
            </article>
            <article class="panel contract-panel">
              <div class="panel-kicker">Stage contract</div><h2>이 단계가 지켜야 할 것</h2>
              <p>${stage.why}</p>
              <div class="contract-grid contract-grid-v2">
                <div class="contract"><small>INPUT</small><p>${stage.contract.input}</p></div>
                <div class="contract"><small>OUTPUT</small><p>${stage.contract.output}</p></div>
                <div class="contract never"><small>NEVER</small><p>${stage.contract.never}</p></div>
              </div>
            </article>
          </div>

          <article class="panel process-panel" id="process">
            <div class="panel-kicker">Process map</div><div class="split-heading"><h2>세부 처리 노드</h2><p>각 상자는 구현·테스트가 가능한 독립 계약입니다.</p></div>
            <div class="flow flow-v2">${flow}</div>
          </article>

          <div class="two-column-detail">
            <article class="panel" id="criteria">
              <div class="panel-kicker">Scorecard</div><h2>평가 기준</h2><div class="score-grid score-grid-v2">${metrics}</div>
              <p class="assumption">${stage.measurementNote}</p>
            </article>
            <article class="panel compact-panel">
              <div class="panel-kicker">Promotion gate</div><h2>승격 기준</h2><p>${stage.recommendation.promote}</p>
              <h3>완료 산출물</h3>${list(stage.deliverables, "deliverables compact-list")}
            </article>
          </div>

          <article class="panel" id="candidates">
            <div class="panel-kicker">Ablation candidates</div><div class="split-heading"><h2>후보 특징과 채택 조건</h2><p>같은 데이터와 같은 Gold에서 한 계층만 바꿔 비교합니다.</p></div>
            <div class="table-wrap"><table><thead><tr><th>후보</th><th>역할</th><th>강점과 근거</th><th>위험</th><th>채택 규칙</th></tr></thead><tbody>${candidateRows}</tbody></table></div>
          </article>

          <div class="two-column-detail">
            <article class="panel" id="example">
              <div class="panel-kicker">Concrete structure</div><h2>${stage.exampleTitle}</h2><p>${stage.exampleLead}</p>
              <div class="example"><div class="caption">${stage.exampleCaption}</div><pre>${esc(stage.example)}</pre></div>
            </article>
            <article class="panel compact-panel">
              <div class="panel-kicker">Failure boundary</div><h2>실패와 방지선</h2>${list(stage.failures, "risk-list compact-list")}
            </article>
          </div>

          <div class="closing-grid">
            <article class="panel"><div class="panel-kicker">Exit gate</div><h2>다음 단계 통과 조건</h2>${list(stage.gates, "gate-list")}</article>
            <article class="panel"><div class="panel-kicker">Evidence</div><h2>근거 자료</h2><ul class="source-list source-grid">${sources}</ul></article>
          </div>
        </div>
      </section>
    </main>
    <footer><div class="shell"><span>공시 Agent 구축 로드맵</span><a href="../system_build_roadmap.html">전체 파이프라인으로 돌아가기 →</a></div></footer>`;

  if (location.hash) {
    requestAnimationFrame(() => document.querySelector(location.hash)?.scrollIntoView());
  }
})();
