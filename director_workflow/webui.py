from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import api_setup_report, load_runtime_config, write_local_env
from .image_tools import register_codex_image
from .runtime import JobManager, build_imagegen_handoff, create_run, list_runs, list_steps, resolve_run_dir


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MPT Director Workflow</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #657282;
      --line: #d9dee7;
      --blue: #1d4ed8;
      --green: #0f7b4f;
      --red: #b42318;
      --amber: #9a5b00;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    header {
      border-bottom: 1px solid var(--line);
      background: #fff;
      padding: 16px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { font-size: 18px; margin: 0; letter-spacing: 0; }
    h2 { font-size: 15px; margin: 0 0 12px; letter-spacing: 0; }
    main {
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1480px;
      margin: 0 auto;
    }
    section, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .sidebar { display: flex; flex-direction: column; gap: 16px; }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .status-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      min-height: 58px;
    }
    .label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
    .value { font-size: 13px; word-break: break-word; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #f9fafb;
    }
    .ok { color: var(--green); border-color: #a6d8bd; background: #effaf4; }
    .bad { color: var(--red); border-color: #f0b8b3; background: #fff1f0; }
    .warn { color: var(--amber); border-color: #f0cf8c; background: #fff8e8; }
    form { display: grid; gap: 10px; }
    input, textarea, select, button {
      font: inherit;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
    }
    input, textarea, select { width: 100%; padding: 8px 9px; }
    textarea { min-height: 72px; resize: vertical; }
    button {
      min-height: 34px;
      padding: 7px 10px;
      cursor: pointer;
      background: #fff;
    }
    button.primary { background: var(--blue); color: #fff; border-color: var(--blue); }
    button:disabled { cursor: not-allowed; opacity: 0.55; }
    .runs { display: grid; gap: 8px; max-height: 540px; overflow: auto; }
    .run-row {
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      padding: 9px;
    }
    .run-row.active { outline: 2px solid #93c5fd; border-color: #60a5fa; }
    .run-title { font-weight: 650; font-size: 13px; margin-bottom: 4px; }
    .run-meta { font-size: 12px; color: var(--muted); line-height: 1.45; }
    .workspace { display: grid; gap: 16px; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .steps {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .step-button {
      min-height: 64px;
      text-align: left;
      padding: 9px;
      background: #fff;
    }
    .step-label { font-weight: 650; font-size: 13px; margin-bottom: 4px; }
    .step-desc { font-size: 12px; color: var(--muted); line-height: 1.35; }
    .files {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .file-row {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      font-size: 12px;
    }
    .jobs { display: grid; gap: 8px; }
    .job {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px;
      background: #fff;
    }
    .flow {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }
    .flow-node {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px;
      min-height: 72px;
      background: #fff;
    }
    .flow-title { font-weight: 650; font-size: 13px; margin-bottom: 6px; }
    .flow-note { color: var(--muted); font-size: 12px; line-height: 1.35; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 8px 0 0;
      padding: 10px;
      background: #101828;
      color: #f9fafb;
      border-radius: 6px;
      max-height: 260px;
      overflow: auto;
      font-size: 12px;
    }
    .path {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      word-break: break-all;
    }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .summary-grid, .steps, .files, .status-grid { grid-template-columns: 1fr; }
      .flow { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>MPT Director Workflow</h1>
    <div id="headerStatus" class="badge warn">loading</div>
  </header>
  <main>
    <div class="sidebar">
      <section>
        <h2>运行配置</h2>
        <div id="configStatus" class="status-grid"></div>
        <form id="configForm" style="margin-top:12px">
          <label>
            <div class="label">DeepSeek API Key</div>
            <input name="DEEPSEEK_API_KEY" type="password" autocomplete="off" placeholder="sk-...，留空不修改">
          </label>
          <label>
            <div class="label">Pexels API Key</div>
            <input name="PEXELS_API_KEY" type="password" autocomplete="off" placeholder="留空不修改">
          </label>
          <label>
            <div class="label">Codex CLI</div>
            <input name="CODEX_EXECUTABLE" placeholder="codex">
          </label>
          <label>
            <div class="label">MPT API Endpoint</div>
            <input name="MPT_API_ENDPOINT" placeholder="http://127.0.0.1:8080/api/v1/videos">
          </label>
          <label>
            <div class="label">TTS Voice</div>
            <input name="DIRECTOR_DEFAULT_VOICE_NAME" list="ttsVoiceOptions" placeholder="gemini:Zephyr-Female">
            <datalist id="ttsVoiceOptions">
              <option value="gemini:Zephyr-Female">
              <option value="gemini:Puck-Male">
              <option value="gemini:Kore-Female">
              <option value="edge:zh-CN-XiaoxiaoNeural">
              <option value="edge:zh-CN-YunjianNeural">
            </datalist>
          </label>
          <button type="submit">保存本机配置</button>
        </form>
      </section>
      <section>
        <h2>新建视频 Run</h2>
        <form id="createRunForm">
          <label>
            <div class="label">主题</div>
            <input name="topic" required placeholder="例如：美国每日新闻">
          </label>
          <label>
            <div class="label">目标秒数</div>
            <input name="duration_seconds" type="number" min="15" max="180" value="60">
          </label>
          <label>
            <div class="label">平台</div>
            <select name="platform">
              <option value="douyin">douyin</option>
              <option value="bilibili">bilibili</option>
              <option value="kuaishou">kuaishou</option>
              <option value="xiaohongshu">xiaohongshu</option>
            </select>
          </label>
          <label>
            <div class="label">风格</div>
            <textarea name="style">新闻解读，具体、有钩子、适合竖屏短视频</textarea>
          </label>
          <label>
            <div class="label">TTS Voice</div>
            <input name="voice_name" list="ttsVoiceOptions" placeholder="留空使用默认配置">
          </label>
          <button class="primary" type="submit">创建 Run</button>
          <button id="createAndRunButton" type="button">创建并严格生成视频</button>
        </form>
      </section>
      <section>
        <h2>Run 列表</h2>
        <div id="runs" class="runs"></div>
      </section>
    </div>
    <div class="workspace">
      <section>
        <h2>当前 Run</h2>
        <div id="runSummary"></div>
        <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap">
          <button id="autoRunButton" class="primary" type="button">严格运行当前 Run</button>
        </div>
      </section>
      <section>
        <h2>严格流程总览</h2>
        <div id="flowMap" class="flow"></div>
      </section>
      <section>
        <h2>Codex 生图任务</h2>
        <div id="imageTasks"></div>
      </section>
      <section>
        <h2>流程步骤</h2>
        <div id="steps" class="steps"></div>
      </section>
      <section>
        <h2>关键文件</h2>
        <div id="files" class="files"></div>
      </section>
      <section>
        <h2>任务日志</h2>
        <div id="jobs" class="jobs"></div>
      </section>
    </div>
  </main>
  <script>
    let state = { status: null, runs: [], selectedRunId: "", steps: [], jobs: [] };

    async function api(path, options) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }

    function badge(ok, text) {
      const cls = ok ? "badge ok" : "badge bad";
      return `<span class="${cls}">${text}</span>`;
    }

    function renderStatus() {
      const report = state.status || {};
      const checks = report.checks || {};
      const config = report.config || {};
      const apis = config.apis || {};
      const tools = config.tools || {};
      const ready = checks.deepseek_api_or_cli && checks.codex_cli && checks.mpt_endpoint_local && checks.mpt_api_reachable;
      document.getElementById("headerStatus").className = ready ? "badge ok" : "badge warn";
      document.getElementById("headerStatus").textContent = ready ? "ready" : "needs config";
      const items = [
        ["MPT endpoint", checks.mpt_endpoint_local, config.mpt_api_endpoint || ""],
        ["MPT running", checks.mpt_api_reachable, checks.mpt_api_reachable ? "online" : (checks.mpt_api_issue || "not reachable")],
        ["Pexels API", apis.pexels, apis.pexels ? "configured" : "missing"],
        ["DeepSeek", checks.deepseek_api_or_cli, apis.deepseek_api ? `api:${apis.deepseek_key_source}` : (tools.deepseek_cli ? "cli" : "missing")],
        ["Codex CLI", checks.codex_cli, tools.codex_cli ? config.codex_executable : "missing"],
        ["TTS", config.default_voice_name, config.default_voice_name || "missing"],
        ["Runs dir", true, config.runs_dir || ""],
        ["Upload dir", checks.social_upload_dir_exists, config.social_upload_dir || "not configured"],
      ];
      document.getElementById("configStatus").innerHTML = items.map(([label, ok, value]) => `
        <div class="status-item">
          <div class="label">${label}</div>
          <div class="value">${badge(ok, ok ? "OK" : "NO")} ${escapeHtml(value || "")}</div>
        </div>
      `).join("");
      const configForm = document.getElementById("configForm");
      if (configForm && !configForm.dataset.hydrated) {
        configForm.elements.CODEX_EXECUTABLE.value = config.codex_executable || "codex";
        configForm.elements.MPT_API_ENDPOINT.value = config.mpt_api_endpoint || "http://127.0.0.1:8080/api/v1/videos";
        configForm.elements.DIRECTOR_DEFAULT_VOICE_NAME.value = config.default_voice_name || "gemini:Zephyr-Female";
        configForm.dataset.hydrated = "true";
      }
    }

    function renderRuns() {
      const box = document.getElementById("runs");
      if (!state.runs.length) {
        box.innerHTML = '<div class="run-meta">暂无 run。</div>';
        return;
      }
      box.innerHTML = state.runs.map(run => `
        <button class="run-row ${run.run_id === state.selectedRunId ? "active" : ""}" data-run="${run.run_id}">
          <div class="run-title">${escapeHtml(run.topic || run.run_id)}</div>
          <div class="run-meta">${escapeHtml(run.run_id)}<br>${escapeHtml(run.target_duration_seconds || "-")}s · ${escapeHtml(run.revision_decision || "not ready")} · ${escapeHtml(run.updated_at)}</div>
        </button>
      `).join("");
      box.querySelectorAll("button").forEach(button => {
        button.addEventListener("click", () => {
          state.selectedRunId = button.dataset.run;
          renderAll();
        });
      });
    }

    function selectedRun() {
      return state.runs.find(run => run.run_id === state.selectedRunId) || state.runs[0] || null;
    }

    function renderRunSummary() {
      const run = selectedRun();
      const box = document.getElementById("runSummary");
      if (!run) {
        box.innerHTML = '<div class="run-meta">先创建或选择一个 run。</div>';
        document.getElementById("autoRunButton").disabled = true;
        return;
      }
      state.selectedRunId = run.run_id;
      const mix = run.material_mix || {};
      box.innerHTML = `
        <div class="summary-grid">
          <div class="status-item"><div class="label">主题</div><div class="value">${escapeHtml(run.topic)}</div></div>
          <div class="status-item"><div class="label">时长</div><div class="value">${escapeHtml(run.target_duration_seconds)}s</div></div>
          <div class="status-item"><div class="label">素材结构</div><div class="value">API ${mix.stock_video || 0} / 图 ${mix.codex_image || 0} / 本地 ${mix.local_asset || 0}</div></div>
          <div class="status-item"><div class="label">最终决策</div><div class="value">${escapeHtml(run.revision_decision || "pending")}</div></div>
        </div>
        <div class="path" style="margin-top:10px">${escapeHtml(run.path)}</div>
      `;
      document.getElementById("autoRunButton").disabled = false;
    }

    function renderImageTasks() {
      const run = selectedRun();
      const box = document.getElementById("imageTasks");
      if (!run) {
        box.innerHTML = '<div class="run-meta">先选择一个 run。</div>';
        return;
      }
      const imagegen = run.imagegen || {};
      const pending = imagegen.pending_tasks || [];
      const complete = imagegen.complete_tasks || [];
      if (!pending.length && !complete.length) {
        box.innerHTML = '<div class="run-meta">当前没有 codex_image 生图任务。严格流程会在需要生图时生成这里的任务。</div>';
        return;
      }
      box.innerHTML = `
        ${pending.map(task => `
          <div class="job">
            <div><span class="badge warn">待生图</span> ${escapeHtml(task.shot_id)}</div>
            <div class="run-meta">${escapeHtml(task.narration || "")}</div>
            <pre>${escapeHtml(task.prompt || "")}</pre>
            <div class="path">建议保存：${escapeHtml(task.suggested_output || "")}</div>
            <form class="register-image-form" data-shot="${escapeHtml(task.shot_id)}" style="margin-top:8px">
              <input name="image_path" placeholder="生成图片本地路径，例如 /Users/.../s03.png" required>
              <input name="fit_score" type="number" min="0" max="100" value="86">
              <button type="submit">登记并批准这张图</button>
            </form>
          </div>
        `).join("")}
        ${complete.map(task => `
          <div class="job">
            <div><span class="badge ok">已登记</span> ${escapeHtml(task.shot_id)}</div>
            <div class="path">${escapeHtml(task.actual_output || "")}</div>
          </div>
        `).join("")}
      `;
      box.querySelectorAll(".register-image-form").forEach(form => {
        form.addEventListener("submit", async event => {
          event.preventDefault();
          const active = selectedRun();
          if (!active) return;
          const data = Object.fromEntries(new FormData(form).entries());
          data.run_id = active.run_id;
          data.shot_id = form.dataset.shot;
          data.fit_score = Number(data.fit_score || 86);
          data.approve = true;
          try {
            await api("/api/register-image", { method: "POST", body: JSON.stringify(data) });
            await refresh();
          } catch (error) {
            alert(error.message);
          }
        });
      });
    }

    function fileDone(files, name, statuses = ["complete", "ready", "approved", "pass", "exists"]) {
      const item = files[name] || {};
      return statuses.includes(item.status) || item.decision === "pass";
    }

    function renderFlowMap() {
      const run = selectedRun();
      const box = document.getElementById("flowMap");
      if (!run) {
        box.innerHTML = '<div class="run-meta">先创建或选择一个 run。</div>';
        return;
      }
      const files = run.files || {};
      const imagegen = run.imagegen || {};
      const nodes = [
        ["DeepSeek 写稿", fileDone(files, "script_candidates"), "script_candidates.json"],
        ["Codex 定稿", fileDone(files, "approved_script_report") && fileDone(files, "approved_script"), "approved_script.md"],
        ["Codex 分镜", fileDone(files, "shot_plan") && fileDone(files, "material_strategy"), "shot_plan.json"],
        ["Pexels 素材", fileDone(files, "api_assets"), "api_asset_candidates.json"],
        ["Codex 生图", imagegen.status === "complete" || !Number(run.counts && run.counts.pending_image_tasks || 0), imagegen.status || "按需生成"],
        ["素材审核", fileDone(files, "asset_binding_report"), "asset_binding_report pass"],
        ["MPT 渲染", fileDone(files, "mpt_wait"), "mpt_wait_report.json"],
        ["技术 QA", fileDone(files, "qa_report"), "qa_report pass"],
        ["语义审核", fileDone(files, "semantic_review"), "semantic_review pass"],
        ["Ready", run.revision_decision === "ready", run.revision_decision || "pending"],
      ];
      box.innerHTML = nodes.map(([title, ok, note]) => `
        <div class="flow-node">
          <div class="flow-title">${escapeHtml(title)}</div>
          <span class="${ok ? "badge ok" : "badge warn"}">${ok ? "OK" : "WAIT"}</span>
          <div class="flow-note" style="margin-top:7px">${escapeHtml(note)}</div>
        </div>
      `).join("");
    }

    function renderSteps() {
      const run = selectedRun();
      const disabled = run ? "" : "disabled";
      document.getElementById("steps").innerHTML = state.steps.map(step => `
        <button class="step-button" ${disabled} data-step="${step.id}">
          <div class="step-label">${escapeHtml(step.label)}</div>
          <div class="step-desc">${escapeHtml(step.description)}</div>
        </button>
      `).join("");
      document.querySelectorAll(".step-button").forEach(button => {
        button.addEventListener("click", async () => {
          const active = selectedRun();
          if (!active) return;
          button.disabled = true;
          try {
            const stepId = button.dataset.step;
            const options = stepId === "qa_render" ? { video_path: prompt("输入 final MP4 路径") || "" } : {};
            if (stepId === "qa_render" && !options.video_path) return;
            await api("/api/jobs", {
              method: "POST",
              body: JSON.stringify({ run_id: active.run_id, step_id: stepId, options }),
            });
            await refresh();
          } catch (error) {
            alert(error.message);
          } finally {
            button.disabled = false;
          }
        });
      });
    }

    function renderFiles() {
      const run = selectedRun();
      const files = (run && run.files) || {};
      const names = Object.keys(files);
      const box = document.getElementById("files");
      if (!names.length) {
        box.innerHTML = '<div class="run-meta">暂无文件状态。</div>';
        return;
      }
      box.innerHTML = names.map(name => {
        const item = files[name] || {};
        const ok = ["complete", "ready", "approved", "pass", "exists"].includes(item.status) || item.decision === "pass";
        const warn = item.status && item.status !== "missing";
        const cls = ok ? "badge ok" : warn ? "badge warn" : "badge bad";
        return `<div class="file-row">
          <div class="label">${escapeHtml(name)}</div>
          <span class="${cls}">${escapeHtml(item.decision || item.status || "missing")}</span>
        </div>`;
      }).join("");
    }

    function renderJobs() {
      const jobs = state.jobs || [];
      const box = document.getElementById("jobs");
      if (!jobs.length) {
        box.innerHTML = '<div class="run-meta">暂无任务。</div>';
        return;
      }
      box.innerHTML = jobs.slice(0, 10).map(job => {
        const ok = job.status === "succeeded";
        const cls = ok ? "badge ok" : job.status === "failed" ? "badge bad" : "badge warn";
        const output = [job.stdout, job.stderr, job.error].filter(Boolean).join("\n");
        return `<div class="job">
          <div><span class="${cls}">${escapeHtml(job.status)}</span> ${escapeHtml(job.step_id)} · ${escapeHtml(job.run_id)}</div>
          <div class="run-meta">${escapeHtml(job.started_at || job.created_at)} ${job.finished_at ? "-> " + escapeHtml(job.finished_at) : ""}</div>
          ${output ? `<pre>${escapeHtml(output)}</pre>` : ""}
        </div>`;
      }).join("");
    }

    async function startAutoRun(runId) {
      await api("/api/auto-run", {
        method: "POST",
        body: JSON.stringify({
          run_id: runId,
          options: {
            provider: "auto",
            count: 5,
            allow_pixabay_fallback: false,
            timeout_seconds: 1200,
            poll_seconds: 10,
            stall_seconds: 300
          }
        }),
      });
      await refresh();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function renderAll() {
      renderStatus();
      renderRuns();
      renderRunSummary();
      renderFlowMap();
      renderImageTasks();
      renderSteps();
      renderFiles();
      renderJobs();
    }

    async function refresh() {
      const priorSelected = state.selectedRunId;
      const [status, steps, runs, jobs] = await Promise.all([
        api("/api/status"),
        api("/api/steps"),
        api("/api/runs"),
        api("/api/jobs"),
      ]);
      state.status = status;
      state.steps = steps.steps;
      state.runs = runs.runs;
      state.jobs = jobs.jobs;
      state.selectedRunId = priorSelected || (state.runs[0] && state.runs[0].run_id) || "";
      renderAll();
    }

    document.getElementById("createRunForm").addEventListener("submit", async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget).entries());
      data.duration_seconds = Number(data.duration_seconds || 60);
      try {
        const payload = await api("/api/runs", { method: "POST", body: JSON.stringify(data) });
        state.selectedRunId = payload.run.run_id;
        await refresh();
      } catch (error) {
        alert(error.message);
      }
    });

    document.getElementById("configForm").addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = Object.fromEntries(new FormData(form).entries());
      try {
        await api("/api/config", { method: "POST", body: JSON.stringify(data) });
        form.elements.DEEPSEEK_API_KEY.value = "";
        form.elements.PEXELS_API_KEY.value = "";
        form.dataset.hydrated = "";
        await refresh();
      } catch (error) {
        alert(error.message);
      }
    });

    document.getElementById("createAndRunButton").addEventListener("click", async event => {
      const button = event.currentTarget;
      const form = document.getElementById("createRunForm");
      if (!form.reportValidity()) return;
      button.disabled = true;
      const data = Object.fromEntries(new FormData(form).entries());
      data.duration_seconds = Number(data.duration_seconds || 60);
      try {
        const payload = await api("/api/runs", { method: "POST", body: JSON.stringify(data) });
        state.selectedRunId = payload.run.run_id;
        await startAutoRun(payload.run.run_id);
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById("autoRunButton").addEventListener("click", async event => {
      const active = selectedRun();
      if (!active) return;
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await startAutoRun(active.run_id);
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
      }
    });

    refresh();
    setInterval(refresh, 4000);
  </script>
</body>
</html>
"""


class DirectorHandler(BaseHTTPRequestHandler):
    manager: JobManager

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_html(HTML)
            elif parsed.path == "/api/status":
                self.send_json(api_setup_report())
            elif parsed.path == "/api/steps":
                self.send_json({"steps": list_steps()})
            elif parsed.path == "/api/runs":
                params = parse_qs(parsed.query)
                limit = int((params.get("limit") or ["80"])[0])
                self.send_json({"runs": list_runs(limit=limit)})
            elif parsed.path == "/api/jobs":
                self.send_json({"jobs": self.manager.list()})
            elif parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = self.manager.get(job_id)
                if not job:
                    self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(job)
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json_body()
            if parsed.path == "/api/runs":
                config = load_runtime_config()
                run = create_run(
                    topic=str(payload.get("topic") or ""),
                    duration_seconds=int(payload.get("duration_seconds") or config.default_duration_seconds),
                    platform=str(payload.get("platform") or config.default_platform),
                    style=str(payload.get("style") or "新闻解读，具体、有钩子、适合竖屏短视频"),
                    voice_name=str(payload.get("voice_name") or config.default_voice_name),
                    config=config,
                )
                self.send_json({"run": run}, HTTPStatus.CREATED)
            elif parsed.path == "/api/config":
                updates = {
                    "DEEPSEEK_API_KEY": str(payload.get("DEEPSEEK_API_KEY") or ""),
                    "PEXELS_API_KEY": str(payload.get("PEXELS_API_KEY") or ""),
                    "CODEX_EXECUTABLE": str(payload.get("CODEX_EXECUTABLE") or ""),
                    "MPT_API_ENDPOINT": str(payload.get("MPT_API_ENDPOINT") or ""),
                    "DIRECTOR_DEFAULT_VOICE_NAME": str(payload.get("DIRECTOR_DEFAULT_VOICE_NAME") or ""),
                }
                result = write_local_env(updates)
                self.manager.config = load_runtime_config()
                self.send_json({"result": result, "status": api_setup_report(self.manager.config)})
            elif parsed.path == "/api/register-image":
                config = load_runtime_config()
                run_dir = resolve_run_dir(str(payload.get("run_id") or ""), config)
                result = register_codex_image(
                    run_dir,
                    shot_id=str(payload.get("shot_id") or ""),
                    image_path=Path(str(payload.get("image_path") or "")).expanduser(),
                    fit_score=int(payload.get("fit_score") or 86),
                    approved=bool(payload.get("approve", True)),
                )
                handoff = build_imagegen_handoff(run_dir)
                self.send_json({"result": result, "image_generation_handoff": handoff})
            elif parsed.path == "/api/jobs":
                job = self.manager.start(
                    run_id=str(payload.get("run_id") or ""),
                    step_id=str(payload.get("step_id") or ""),
                    options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
                )
                self.send_json({"job": job.to_public_dict()}, HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/auto-run":
                job = self.manager.start_auto(
                    run_id=str(payload.get("run_id") or ""),
                    options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
                )
                self.send_json({"job": job.to_public_dict()}, HTTPStatus.ACCEPTED)
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 1024 * 1024:
            raise ValueError("request body too large")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    config = load_runtime_config()
    DirectorHandler.manager = JobManager(config)
    return ThreadingHTTPServer((host, port), DirectorHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local WebUI for MPT Director Workflow.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = make_server(args.host, args.port)
    print(f"http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
